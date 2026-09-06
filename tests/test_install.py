"""Установка скачанного бинарника: куда кладём и как убираем обратно."""

from __future__ import annotations

import subprocess

import pytest

from snapreel import cli, install
from snapreel.platform_info import Environment, Platform

WINDOWS = Environment(platform=Platform.WINDOWS, is_wsl=False)
MACOS = Environment(platform=Platform.MACOS, is_wsl=False)
LINUX = Environment(platform=Platform.LINUX_X11, is_wsl=False)


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Делает вид, что мы — собранный бинарник, лежащий в «Загрузках»."""
    downloaded = tmp_path / "Downloads" / "snapreel"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"BINARY" * 100)
    downloaded.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(install.sys, "frozen", True, raising=False)
    monkeypatch.setattr(install.sys, "executable", str(downloaded))
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    return downloaded


# --- куда ставим ----------------------------------------------------------


@pytest.mark.parametrize(
    "env,tail",
    [
        (WINDOWS, ("Programs", "snapreel", "snapreel.exe")),
        (MACOS, ("Applications", "snapreel")),
        (LINUX, (".local", "bin", "snapreel")),
    ],
)
def test_each_platform_has_its_own_place(frozen, env, tail):
    """Права администратора не нужны нигде: ставим только текущему пользователю."""
    assert install.target_path(env).parts[-len(tail) :] == tail


def test_a_downloaded_binary_is_not_installed_yet(frozen):
    assert not install.plan(LINUX).installed


def test_a_binary_in_place_is_installed(frozen, monkeypatch):
    target = install.target_path(LINUX)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"BINARY")
    monkeypatch.setattr(install.sys, "executable", str(target))

    assert install.plan(LINUX).installed


# --- установка ------------------------------------------------------------


def test_installing_copies_the_binary_and_asks_it_to_autostart(frozen, monkeypatch):
    calls = []
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""),
    )

    outcome = install.install(LINUX)

    target = install.target_path(LINUX)
    assert outcome.ok
    assert target.read_bytes() == frozen.read_bytes()
    # автозапуск прописывает установленная копия: свой путь она знает сама
    assert calls == [[str(target), "autostart"]]


def test_installing_without_autostart_touches_nothing_else(frozen, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("автозапуск не просили")

    monkeypatch.setattr(install.subprocess, "run", forbidden)

    assert install.install(LINUX, with_autostart=False).ok


def test_a_failed_copy_leaves_no_stump(frozen, monkeypatch):
    def half_way(source, target, **kwargs):
        install.Path(target).write_bytes(b"HALF")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(install.shutil, "copy2", half_way)

    outcome = install.install(LINUX)

    assert not outcome.ok
    assert not install.target_path(LINUX).exists()
    assert not install.target_path(LINUX).with_name("snapreel.new").exists()


def test_a_source_install_says_it_has_nothing_to_copy(monkeypatch):
    monkeypatch.setattr(install, "supported", lambda: False)
    outcome = install.install(LINUX)
    assert not outcome.ok
    assert "готовый бинарник" in outcome.message


# --- удаление -------------------------------------------------------------


def test_uninstalling_removes_the_copy_and_the_autostart(frozen, monkeypatch):
    removed = []
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    install.install(LINUX)
    from snapreel import autostart

    monkeypatch.setattr(autostart, "remove_autostart", lambda env=None: removed.append(env))

    outcome = install.uninstall(LINUX)

    assert outcome.ok
    assert not install.target_path(LINUX).exists()
    assert removed == [LINUX]


def test_uninstalling_nothing_is_not_a_failure(frozen, monkeypatch):
    from snapreel import autostart

    monkeypatch.setattr(autostart, "remove_autostart", lambda env=None: None)
    outcome = install.uninstall(LINUX)
    assert outcome.ok
    assert "не найдено" in outcome.message


# --- первый запуск --------------------------------------------------------


def test_a_downloaded_binary_offers_to_install_itself(monkeypatch, tmp_path):
    """Скачанный exe сначала предлагает поставить себя, а потом уже живёт."""
    asked = []
    monkeypatch.setattr(install, "supported", lambda: True)
    monkeypatch.setattr(
        install, "plan", lambda env=None: install.Plan(tmp_path / "a", tmp_path / "b", False)
    )
    monkeypatch.setattr(cli, "_offer_install", lambda cfg: asked.append(True) or True)

    assert cli.main(["--config", str(tmp_path / "c.toml"), "tray"]) == 0
    assert asked  # окно показали, установленная копия поднялась сама


def test_an_installed_binary_goes_straight_to_the_tray(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "supported", lambda: True)
    monkeypatch.setattr(
        install, "plan", lambda env=None: install.Plan(tmp_path / "a", tmp_path / "a", True)
    )
    monkeypatch.setattr(cli, "_offer_install", lambda cfg: pytest.fail("уже установлен"))
    monkeypatch.setattr("snapreel.tray.run", lambda config, path=None, env=None: 0)

    assert cli.main(["--config", str(tmp_path / "c.toml"), "tray"]) == 0


def test_removing_itself_is_left_to_a_process_that_outlives_us(frozen, monkeypatch):
    """Бутлоадер PyInstaller не находит свой файл и пугает человека по-английски."""
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    install.install(LINUX)
    target = install.target_path(LINUX)
    monkeypatch.setattr(install.sys, "executable", str(target))

    from snapreel import autostart

    monkeypatch.setattr(autostart, "remove_autostart", lambda env=None: None)
    spawned = []
    monkeypatch.setattr(install.subprocess, "Popen", lambda argv, **kwargs: spawned.append(argv))

    outcome = install.uninstall(LINUX)

    assert outcome.ok
    assert spawned and str(target) in " ".join(spawned[0])

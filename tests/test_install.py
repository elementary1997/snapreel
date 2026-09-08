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
    """Убирается работающая копия: каталог мог быть выбран человеком свой."""
    removed = []
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    install.install(LINUX)
    target = install.target_path(LINUX)
    monkeypatch.setattr(install.sys, "executable", str(target))
    monkeypatch.setattr(install.subprocess, "Popen", lambda argv, **kwargs: None)
    from snapreel import autostart

    monkeypatch.setattr(autostart, "remove_autostart", lambda env=None: removed.append(env))

    outcome = install.uninstall(LINUX)

    assert outcome.ok
    assert removed == [LINUX]


def test_uninstalling_nothing_is_not_a_failure(frozen, monkeypatch, tmp_path):
    from snapreel import autostart

    monkeypatch.setattr(autostart, "remove_autostart", lambda env=None: None)
    monkeypatch.setattr(install.sys, "executable", str(tmp_path / "нет-такого"))

    outcome = install.uninstall(LINUX)

    assert outcome.ok
    assert "не найдено" in outcome.message


# --- каталог выбирает человек ---------------------------------------------


def test_a_chosen_folder_replaces_the_default(frozen, tmp_path):
    """Каталог берут из проводника, имя файла добавляем сами."""
    chosen = tmp_path / "Программы" / "snapreel"

    plan = install.plan(LINUX, folder=chosen)

    assert plan.target == chosen / "snapreel"
    assert not plan.installed


def test_installing_into_a_chosen_folder_creates_it(frozen, monkeypatch, tmp_path):
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    chosen = tmp_path / "куда-нибудь" / "поглубже"

    outcome = install.install(LINUX, folder=chosen)

    assert outcome.ok
    assert (chosen / "snapreel").read_bytes() == frozen.read_bytes()


# --- первый запуск --------------------------------------------------------


def test_a_double_click_offers_to_install_first(monkeypatch, tmp_path):
    """Скачанный exe сначала предлагает поставить себя, а потом уже живёт."""
    asked = []
    monkeypatch.setattr(install, "supported", lambda: True)
    monkeypatch.setattr(
        install, "plan", lambda env=None: install.Plan(tmp_path / "a", tmp_path / "b", False)
    )
    monkeypatch.setattr(cli, "_offer_install", lambda cfg: asked.append(True) or True)
    monkeypatch.setattr("snapreel.tray.run", lambda config, path=None, env=None: 0)

    assert cli.main(["--config", str(tmp_path / "c.toml")]) == 0
    assert asked  # окно показали, установленная копия поднялась сама


def test_an_explicit_tray_never_asks_about_installing(monkeypatch, tmp_path):
    """`snapreel tray` — это то, что стоит в автозапуске: оттуда ждут иконку.

    Окно установки на этом пути значило бы окно вместо иконки при каждом
    входе в систему у того, кто ставить отказался.
    """
    monkeypatch.setattr(install, "supported", lambda: True)
    monkeypatch.setattr(
        install, "plan", lambda env=None: install.Plan(tmp_path / "a", tmp_path / "b", False)
    )
    monkeypatch.setattr(cli, "_offer_install", lambda cfg: pytest.fail("окно вместо иконки"))
    monkeypatch.setattr("snapreel.tray.run", lambda config, path=None, env=None: 0)

    assert cli.main(["--config", str(tmp_path / "c.toml"), "tray"]) == 0


def test_an_installed_binary_goes_straight_to_the_tray(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "supported", lambda: True)
    monkeypatch.setattr(
        install, "plan", lambda env=None: install.Plan(tmp_path / "a", tmp_path / "a", True)
    )
    monkeypatch.setattr(cli, "_offer_install", lambda cfg: pytest.fail("уже установлен"))
    monkeypatch.setattr("snapreel.tray.run", lambda config, path=None, env=None: 0)

    assert cli.main(["--config", str(tmp_path / "c.toml")]) == 0


def test_macos_does_not_start_a_second_copy(frozen, monkeypatch):
    """`launchctl load` поднимает агент сам — вторая копия дралась бы за хоткеи."""
    spawned = []
    monkeypatch.setattr(install.subprocess, "Popen", lambda argv, **kwargs: spawned.append(argv))

    assert install.launch(install.target_path(MACOS), MACOS, autostarted=True)
    assert spawned == []

    assert install.launch(install.target_path(MACOS), MACOS, autostarted=False)
    assert spawned  # без автозапуска поднимать копию всё-таки нам


def test_a_restart_starts_the_binary_that_took_our_place(frozen, monkeypatch):
    """После обновления новый файл лежит по нашему же пути — его и поднимаем."""
    spawned = []
    monkeypatch.setattr(install.subprocess, "Popen", lambda argv, **kwargs: spawned.append(argv))

    assert install.relaunch(LINUX)

    assert spawned == [[str(frozen.resolve()), "tray"]]


def test_a_restart_on_macos_leaves_the_job_to_launchd(frozen, monkeypatch):
    """У LaunchAgent стоит KeepAlive: launchd вернёт трей сам.

    Своя копия оказалась бы второй — две иконки в строке меню и общая драка
    за комбинации.
    """
    spawned = []
    monkeypatch.setattr(install.subprocess, "Popen", lambda argv, **kwargs: spawned.append(argv))
    agent = install.Path.home() / "Library" / "LaunchAgents" / "com.snapreel.daemon.plist"
    agent.parent.mkdir(parents=True)
    agent.write_text("plist", encoding="utf-8")

    assert install.relaunch(MACOS)
    assert spawned == []


def test_without_a_launch_agent_macos_starts_the_copy_itself(frozen, monkeypatch):
    """Поднимать некому: автозапуск не прописан, launchd про нас не знает."""
    spawned = []
    monkeypatch.setattr(install.subprocess, "Popen", lambda argv, **kwargs: spawned.append(argv))

    assert install.relaunch(MACOS)
    assert spawned == [[str(frozen.resolve()), "tray"]]


def test_a_source_install_has_nothing_to_relaunch(monkeypatch):
    """`sys.executable` там — интерпретатор: подниматься по нему нечему."""
    monkeypatch.delattr(install.sys, "frozen", raising=False)

    assert install.relaunch(LINUX) is False


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

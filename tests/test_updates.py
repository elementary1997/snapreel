"""Обновление: проверка версии, скачивание с проверкой суммы, замена бинарника.

Сеть подменяется целиком: тесты не должны ходить наружу, а единственный
разрешённый snapreel запрос всё равно нужно проверять на подделке.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib

import pytest

from snapreel import updates
from snapreel.platform_info import Environment, Platform

WINDOWS = Environment(Platform.WINDOWS, is_wsl=False)
MACOS = Environment(Platform.MACOS, is_wsl=False)
X11 = Environment(Platform.LINUX_X11, is_wsl=False)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def net(monkeypatch):
    """Отдаёт заготовленные ответы по URL и запоминает, куда ходили."""
    routes: dict[str, bytes] = {}
    visited: list[str] = []

    def fake_urlopen(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        visited.append(url)
        if url not in routes:
            raise OSError(f"нет маршрута: {url}")
        return FakeResponse(routes[url])

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    fake_urlopen.routes = routes
    fake_urlopen.visited = visited
    return fake_urlopen


def release_json(tag="v9.9.9", asset=None, with_sums=True):
    """Ответ github с файлом для той системы, на которой идёт прогон.

    Имя файла зашитое здесь означало бы совсем другую проверку: на Windows и
    macOS такой релиз — это «в релизе нет файла для этой системы», и тест про
    суточную отметку падал бы не там, где смотрит.
    """
    asset = asset or updates.asset_name()
    assets = [{"name": asset, "browser_download_url": f"https://d/{asset}", "size": 10}]
    if with_sums:
        assets.append({"name": "SHA256SUMS", "browser_download_url": "https://d/SHA256SUMS"})
    return json.dumps({"tag_name": tag, "assets": assets}).encode()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v0.2.0", (0, 2, 0)),
        ("0.2.0", (0, 2, 0)),
        ("v1.10.3", (1, 10, 3)),
        ("v0.3.0-rc1", (0, 3, 0)),
        ("что-то", ()),
    ],
)
def test_versions_are_read_the_same_with_or_without_v(text, expected):
    assert updates.parse_version(text) == expected


def test_a_newer_version_sorts_above_the_old_one():
    assert updates.parse_version("v0.10.0") > updates.parse_version("v0.9.9")


@pytest.mark.parametrize(
    ("env", "arch", "expected"),
    [
        (WINDOWS, "x86_64", "snapreel-windows-x86_64.exe"),
        (MACOS, "arm64", "snapreel-macos-arm64"),
        (MACOS, "x86_64", "snapreel-macos-x86_64"),
        (X11, "x86_64", "snapreel-linux-x86_64"),
    ],
)
def test_each_platform_asks_for_its_own_file(env, arch, expected):
    assert updates.asset_name(env, arch) == expected


def test_github_is_asked_no_more_than_once_a_day(tmp_path, net):
    net.routes[updates.API] = release_json()

    first = updates.check(tmp_path, now=1_000_000)
    second = updates.check(tmp_path, now=1_000_000 + 3600)

    assert first is not None  # 9.9.9 новее любой нашей
    assert second is None
    assert net.visited == [updates.API]


def test_a_day_later_it_asks_again(tmp_path, net):
    net.routes[updates.API] = release_json()

    updates.check(tmp_path, now=1_000_000)
    updates.check(tmp_path, now=1_000_000 + updates.CHECK_EVERY + 1)

    assert net.visited == [updates.API, updates.API]


def test_the_same_version_is_not_an_update(tmp_path, net, monkeypatch):
    monkeypatch.setattr(updates, "current", lambda: (9, 9, 9))
    net.routes[updates.API] = release_json()

    assert updates.check(tmp_path, force=True) is None


def test_a_release_without_our_file_says_so(tmp_path, net, monkeypatch):
    monkeypatch.setattr(updates, "asset_name", lambda *a: "snapreel-linux-x86_64")
    net.routes[updates.API] = release_json(asset="snapreel-macos-arm64")

    with pytest.raises(updates.UpdateError, match="нет файла для этой системы"):
        updates.check(tmp_path, force=True)


def test_a_dead_network_is_a_readable_error(tmp_path, net):
    with pytest.raises(updates.UpdateError, match="не спросить github"):
        updates.check(tmp_path, force=True)


# --- скачивание ------------------------------------------------------------


def a_release(asset="snapreel-linux-x86_64", with_sums=True):
    return updates.Release(
        version=(9, 9, 9),
        tag="v9.9.9",
        asset=asset,
        url=f"https://d/{asset}",
        checksums_url="https://d/SHA256SUMS" if with_sums else None,
        size=4,
    )


def test_a_download_is_checked_against_the_release_sums(tmp_path, net):
    payload = "новый бинарник".encode()
    digest = hashlib.sha256(payload).hexdigest()
    net.routes["https://d/snapreel-linux-x86_64"] = payload
    net.routes["https://d/SHA256SUMS"] = f"{digest}  snapreel-linux-x86_64\n".encode()

    path = updates.download(a_release(), tmp_path)

    assert path.read_bytes() == payload


def test_a_tampered_download_is_refused_and_removed(tmp_path, net):
    net.routes["https://d/snapreel-linux-x86_64"] = "подменённое".encode()
    net.routes["https://d/SHA256SUMS"] = b"0" * 64 + b"  snapreel-linux-x86_64\n"

    with pytest.raises(updates.UpdateError, match="не сошлось по сумме"):
        updates.download(a_release(), tmp_path)
    assert not (tmp_path / "snapreel-linux-x86_64").exists()


def test_a_release_without_sums_is_refused(tmp_path, net):
    """Без чего сверить — не ставим: это исполняемый файл, а не картинка."""
    net.routes["https://d/snapreel-linux-x86_64"] = "что-то".encode()

    with pytest.raises(updates.UpdateError, match="SHA256SUMS"):
        updates.download(a_release(with_sums=False), tmp_path)


# --- замена бинарника ------------------------------------------------------


def test_the_running_binary_is_moved_aside_not_deleted(tmp_path):
    """На Windows запущенный exe нельзя удалить, а переименовать — можно."""
    running = tmp_path / "snapreel"
    running.write_bytes("старое".encode())
    new = tmp_path / "new" / "snapreel"
    new.parent.mkdir()
    new.write_bytes("новое".encode())

    updates.install(new, running)

    assert running.read_bytes() == "новое".encode()
    assert (tmp_path / "snapreel.old").read_bytes() == "старое".encode()


def test_a_failed_install_puts_the_old_binary_back(tmp_path, monkeypatch):
    running = tmp_path / "snapreel"
    running.write_bytes("старое".encode())
    new = tmp_path / "нет-такого"

    with pytest.raises(updates.UpdateError):
        updates.install(new, running)

    assert running.read_bytes() == "старое".encode()


def test_an_interrupted_copy_leaves_the_working_binary_alone(tmp_path, monkeypatch):
    """Место на диске кончилось на середине — прежний бинарник обязан уцелеть."""
    running = tmp_path / "snapreel"
    running.write_bytes(b"CTAPOE" * 200)
    new = tmp_path / "new" / "snapreel"
    new.parent.mkdir()
    new.write_bytes(b"HOBOE" * 200)

    def half_way(source, target, **kwargs):
        pathlib.Path(target).write_bytes(pathlib.Path(source).read_bytes()[:20])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(updates.shutil, "copy2", half_way)

    with pytest.raises(updates.UpdateError):
        updates.install(new, running)

    assert running.read_bytes() == b"CTAPOE" * 200
    assert not (tmp_path / "snapreel.new").exists()


def test_the_leftover_copy_is_cleaned_on_the_next_run(tmp_path):
    running = tmp_path / "snapreel"
    running.write_bytes("новое".encode())
    (tmp_path / "snapreel.old").write_bytes("старое".encode())

    updates.clean_leftovers(running)

    assert not (tmp_path / "snapreel.old").exists()


def test_updating_a_source_install_explains_itself(monkeypatch):
    monkeypatch.setattr(updates, "supported", lambda: False)

    with pytest.raises(updates.UpdateError, match="pip install -U"):
        updates.update(a_release())

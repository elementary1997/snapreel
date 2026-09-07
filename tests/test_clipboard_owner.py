"""Буфер обмена на X11: что именно мы объявляем и кому это достаётся.

Часть проверок — без экрана (что кладём и когда вообще идём этим путём),
часть требует настоящего сервера и лежит в `tests/test_clipboard_gui.py`:
список типов виден только тому, кто спросит его у X.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from snapreel import clipboard
from snapreel.clipboard import x11_owner
from snapreel.platform_info import Environment, Platform

X11 = Environment(platform=Platform.LINUX_X11, is_wsl=False)
WSL = Environment(platform=Platform.LINUX_WAYLAND, is_wsl=True)
WAYLAND = Environment(platform=Platform.LINUX_WAYLAND, is_wsl=False)
WINDOWS = Environment(platform=Platform.WINDOWS, is_wsl=False)


def test_the_file_is_offered_in_both_kinds():
    """Electron спрашивает один тип, файловые менеджеры — другой."""
    data = x11_owner.payload(["file:///clips/a.mp4"])

    assert data[x11_owner.URI_LIST] == b"file:///clips/a.mp4\r\n"
    assert data[x11_owner.GNOME_FILES] == b"copy\nfile:///clips/a.mp4"


def test_the_path_is_not_offered_as_text():
    """Из-за строки с путём чат и вставлял текст вместо вложения."""
    data = x11_owner.payload(["file:///clips/a.mp4"])

    assert "text/plain" not in data


def test_copying_is_not_cutting():
    """`cut` заставил бы файловый менеджер удалить исходник после вставки."""
    data = x11_owner.payload(["file:///clips/a.mp4"])

    assert data[x11_owner.GNOME_FILES].startswith(b"copy\n")


def test_the_resident_owns_the_clipboard_itself(monkeypatch):
    taken: list = []
    monkeypatch.setattr(x11_owner, "copy_files", taken.append)

    clipboard.copy_files([Path("/clips/a.mp4")], X11, resident=True)

    assert taken == [[Path("/clips/a.mp4")]]


def test_wsl_counts_as_x11_here(monkeypatch):
    """В WSLg сессия зовётся Wayland, а буфер обмена — самый обычный X."""
    taken: list = []
    monkeypatch.setattr(x11_owner, "copy_files", taken.append)

    clipboard.copy_files([Path("/clips/a.mp4")], WSL, resident=True)

    assert taken


def test_a_one_shot_run_leaves_the_clipboard_to_xclip(monkeypatch):
    """Владение живёт, пока жив процесс: разовой записи оно ни к чему."""
    taken: list = []
    handed: list = []
    monkeypatch.setattr(x11_owner, "copy_files", taken.append)
    monkeypatch.setattr("snapreel.clipboard.posix.x11_copy_files", handed.append)

    clipboard.copy_files([Path("/clips/a.mp4")], X11, resident=False)

    assert taken == []
    assert handed == [[Path("/clips/a.mp4")]]


def test_a_refusal_falls_back_to_the_known_path(monkeypatch):
    """Не вышло завладеть — не повод остаться без буфера вовсе."""

    def refuse(paths):
        raise clipboard.ClipboardError("нет python-xlib")

    handed: list = []
    monkeypatch.setattr(x11_owner, "copy_files", refuse)
    monkeypatch.setattr("snapreel.clipboard.posix.x11_copy_files", handed.append)

    clipboard.copy_files([Path("/clips/a.mp4")], X11, resident=True)

    assert handed == [[Path("/clips/a.mp4")]]


@pytest.mark.parametrize("env", [WAYLAND, WINDOWS])
def test_other_sessions_keep_their_own_way(monkeypatch, env):
    taken: list = []
    monkeypatch.setattr(x11_owner, "copy_files", taken.append)
    monkeypatch.setattr("snapreel.clipboard.posix.wayland_copy_files", lambda paths: None)
    monkeypatch.setattr("snapreel.clipboard.windows.copy_files", lambda paths: None, raising=False)

    clipboard.copy_files([Path("/clips/a.mp4")], env, resident=True)

    assert taken == []


def test_the_handoff_goes_to_a_process_that_outlives_us(monkeypatch):
    """Иначе закрывший иконку сразу после записи остался бы с пустым буфером."""
    handed: list = []

    class Held:
        def __init__(self):
            self.paths = [Path("/clips/a.mp4")]

        def owns(self):
            return True

        def stop(self):
            handed.append("отпустили")

    monkeypatch.setattr(x11_owner, "_current", Held())
    monkeypatch.setattr("snapreel.clipboard.posix.x11_copy_files", handed.append)

    clipboard.hand_off(X11)

    assert handed == [[Path("/clips/a.mp4")], "отпустили"]


def test_the_handoff_does_nothing_when_we_never_owned_it(monkeypatch):
    monkeypatch.setattr(x11_owner, "_current", None)
    monkeypatch.setattr(
        "snapreel.clipboard.posix.x11_copy_files",
        lambda paths: pytest.fail("отдавать нечего"),
    )

    clipboard.hand_off(X11)
    clipboard.hand_off(WINDOWS)

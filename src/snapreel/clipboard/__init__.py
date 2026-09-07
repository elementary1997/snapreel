"""Единая точка входа: положить видеофайл в буфер обмена текущей ОС."""

from __future__ import annotations

from pathlib import Path

from ..platform_info import Environment, Platform, detect
from .posix import ClipboardError, file_uri

__all__ = ["ClipboardError", "copy_files", "copy_text", "file_uri", "hand_off"]


def _has_x_display(env: Environment) -> bool:
    """Есть ли X-сервер, у которого можно забрать буфер.

    В WSL сессия зовётся Wayland (WSLg выставляет `WAYLAND_DISPLAY`), а окна
    там на самом деле X — как и буфер обмена.
    """
    if env.platform is Platform.LINUX_X11:
        return True
    return env.platform is Platform.LINUX_WAYLAND and env.is_wsl


def hand_off(env: Environment | None = None) -> None:
    """Отдаёт буфер тому, кто переживёт наш выход.

    Владение селекцией в X11 живёт, пока жив процесс: закрыв иконку сразу
    после записи, человек остался бы с пустым буфером. Зовётся из трея на
    выходе; там, где буфером владели не мы, не делает ничего.
    """
    env = env or detect()
    if not _has_x_display(env):
        return
    from . import x11_owner

    x11_owner.hand_off()


def copy_files(paths: list[Path], env: Environment | None = None, resident: bool = False) -> None:
    """Кладёт файлы в буфер так, чтобы Ctrl+V вставлял именно файл.

    `resident` — процесс переживёт запись (это трей). Тогда на X11 буфером
    владеет он сам и отдаёт файл сразу в двух типах: Chromium и Electron
    спрашивают один, файловые менеджеры и Telegram другой. Разовому
    `snapreel record` так нельзя — с концом процесса пропало бы и содержимое,
    поэтому ему остаются `xclip` и `wl-copy`, которые его переживают.
    """
    env = env or detect()
    if resident and _has_x_display(env):
        from . import x11_owner

        try:
            x11_owner.copy_files(paths)
            return
        except ClipboardError:
            pass  # не вышло — идём проверенным путём, он рядом
    if env.platform is Platform.WINDOWS:
        from .windows import copy_files as impl

        impl(paths)
    elif env.platform is Platform.MACOS:
        from .posix import macos_copy_files as impl

        impl(paths)
    elif env.platform is Platform.LINUX_WAYLAND:
        from .posix import wayland_copy_files as impl

        impl(paths)
    else:
        from .posix import x11_copy_files as impl

        impl(paths)


def copy_text(text: str, env: Environment | None = None) -> None:
    env = env or detect()
    if env.platform is Platform.WINDOWS:
        from .windows import copy_text as impl

        impl(text)
    elif env.platform is Platform.MACOS:
        from .posix import macos_copy_text as impl

        impl(text)
    elif env.platform is Platform.LINUX_WAYLAND:
        from .posix import wayland_copy_text as impl

        impl(text)
    else:
        from .posix import x11_copy_text as impl

        impl(text)

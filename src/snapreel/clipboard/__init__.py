"""Единая точка входа: положить видеофайл в буфер обмена текущей ОС."""

from __future__ import annotations

from pathlib import Path

from ..platform_info import Environment, Platform, detect
from .posix import ClipboardError, file_uri

__all__ = ["ClipboardError", "copy_files", "copy_text", "file_uri"]


def copy_files(paths: list[Path], env: Environment | None = None) -> None:
    """Кладёт файлы в буфер так, чтобы Ctrl+V вставлял именно файл."""
    env = env or detect()
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

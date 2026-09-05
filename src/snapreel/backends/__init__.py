"""Выбор бэкенда захвата под текущую платформу."""

from __future__ import annotations

import os

from ..config import Config
from ..platform_info import Environment, Platform, detect
from .base import CaptureBackend, CaptureError, Recording
from .linux import WfRecorderBackend, X11GrabBackend
from .macos import AvFoundationBackend
from .windows import GdigrabBackend

__all__ = [
    "AvFoundationBackend",
    "CaptureBackend",
    "CaptureError",
    "GdigrabBackend",
    "Recording",
    "WfRecorderBackend",
    "X11GrabBackend",
    "for_environment",
]

_BY_PLATFORM: dict[Platform, type[CaptureBackend]] = {
    Platform.WINDOWS: GdigrabBackend,
    Platform.MACOS: AvFoundationBackend,
    Platform.LINUX_X11: X11GrabBackend,
    Platform.LINUX_WAYLAND: WfRecorderBackend,
}


def for_environment(config: Config, env: Environment | None = None) -> CaptureBackend:
    env = env or detect()
    if env.is_wsl and os.environ.get("SNAPREEL_ALLOW_WSL", "").lower() not in ("1", "true", "yes"):
        raise CaptureError(
            "запись запущена внутри WSL: отсюда виден только экран WSLg, "
            "а не рабочий стол Windows. Установите snapreel на хосте Windows "
            "и запускайте его оттуда. Чтобы записать сам экран WSLg — "
            "SNAPREEL_ALLOW_WSL=1."
        )
    return _BY_PLATFORM[env.platform](config)

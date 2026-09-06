"""Определение платформы, графической сессии и границ рабочего стола."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum

from .region import Region


class Platform(StrEnum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX_X11 = "linux-x11"
    LINUX_WAYLAND = "linux-wayland"


@dataclass(frozen=True)
class Environment:
    platform: Platform
    is_wsl: bool

    @property
    def is_linux(self) -> bool:
        return self.platform in (Platform.LINUX_X11, Platform.LINUX_WAYLAND)


def _is_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    if "microsoft" in platform.release().lower():
        return True
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def detect() -> Environment:
    system = platform.system()
    if system == "Windows":
        return Environment(Platform.WINDOWS, is_wsl=False)
    if system == "Darwin":
        return Environment(Platform.MACOS, is_wsl=False)
    if system == "Linux":
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        wayland = session == "wayland" or (not session and bool(os.environ.get("WAYLAND_DISPLAY")))
        kind = Platform.LINUX_WAYLAND if wayland else Platform.LINUX_X11
        return Environment(kind, is_wsl=_is_wsl())
    raise RuntimeError(f"платформа {system} не поддерживается")


def machine() -> str:
    """Архитектура в терминах имён файлов релиза: `arm64` либо `x86_64`.

    Живёт здесь по той же причине, что и `detect`: вопросы «где мы работаем»
    задаются системе в одном месте, а не расползаются по модулям.
    """
    name = platform.machine().lower()
    if name in ("arm64", "aarch64"):
        return "arm64"
    return "x86_64"


def enable_dpi_awareness() -> None:
    """На Windows переводит процесс в per-monitor DPI awareness.

    Без этого Tk отдаёт логические координаты, а gdigrab работает в физических,
    и при масштабе экрана 125%/150% записывается не та область, что выделили.
    Вызывать до создания первого окна Tk.
    """
    if platform.system() != "Windows":
        return
    import ctypes

    try:  # Windows 10 1703+
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
        return
    except (AttributeError, OSError):
        pass
    try:  # Windows 8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def virtual_desktop(env: Environment | None = None) -> Region:
    """Границы виртуального рабочего стола со всеми мониторами."""
    env = env or detect()
    if env.platform is Platform.WINDOWS:
        return _virtual_desktop_windows()
    if env.platform is Platform.LINUX_X11:
        region = _virtual_desktop_xrandr()
        if region is not None:
            return region
    return _virtual_desktop_tk()


def _virtual_desktop_windows() -> Region:
    import ctypes

    metrics = ctypes.windll.user32.GetSystemMetrics
    sm_xvirtualscreen, sm_yvirtualscreen = 76, 77
    sm_cxvirtualscreen, sm_cyvirtualscreen = 78, 79
    return Region(
        metrics(sm_xvirtualscreen),
        metrics(sm_yvirtualscreen),
        metrics(sm_cxvirtualscreen),
        metrics(sm_cyvirtualscreen),
    )


def _virtual_desktop_xrandr() -> Region | None:
    """Размер X-экрана целиком: `xrandr` печатает его в строке `Screen 0:`."""
    if not shutil.which("xrandr"):
        return None
    try:
        out = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    for line in out.splitlines():
        if not line.startswith("Screen "):
            continue
        # Screen 0: minimum 320 x 200, current 3840 x 1080, maximum 16384 x 16384
        for chunk in line.split(","):
            chunk = chunk.strip()
            if chunk.startswith("current "):
                parts = chunk.split()
                try:
                    return Region(0, 0, int(parts[1]), int(parts[3]))
                except (IndexError, ValueError):
                    return None
    return None


def _virtual_desktop_tk() -> Region:
    import tkinter

    root = tkinter.Tk()
    try:
        root.withdraw()
        return Region(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    finally:
        root.destroy()

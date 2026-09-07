"""Определение платформы, графической сессии и границ рабочего стола."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum

from . import proc
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


def prefers_dark(env: Environment | None = None) -> bool | None:
    """Тёмная ли тема у системы. None — спросить не вышло.

    Спрашиваем сами: Qt отвечает на этот вопрос не везде, а светлое окно
    посреди тёмного рабочего стола выглядит чужим приложением.
    """
    env = env or detect()
    try:
        if env.platform is Platform.WINDOWS:
            return _windows_dark()
        if env.platform is Platform.MACOS:
            return _macos_dark()
        return _linux_dark()
    except Exception:  # тема — украшение: любая неудача значит «не знаем»
        return None


def _windows_dark() -> bool | None:
    import winreg

    path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
        # 0 — тёмная тема приложений, 1 — светлая; ключа может не быть вовсе
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    return not int(value)


def _macos_dark() -> bool | None:
    result = proc.run(
        ["defaults", "read", "-g", "AppleInterfaceStyle"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=5,
    )
    # в светлой теме ключа нет вовсе, и defaults отвечает ошибкой
    return result.returncode == 0 and "dark" in result.stdout.strip().lower()


def _linux_dark() -> bool | None:
    if not shutil.which("gsettings"):
        return None
    for key in ("color-scheme", "gtk-theme"):
        result = proc.run(
            ["gsettings", "get", "org.gnome.desktop.interface", key],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip().strip("'\""):
            answer = result.stdout.strip().lower()
            if "dark" in answer:
                return True
            if key == "color-scheme" and "light" in answer:
                return False
    return None


def attach_console() -> None:
    """На Windows подключает вывод к консоли, из которой нас позвали.

    Релизный exe собран оконным — иначе двойной щелчок открывал бы чёрное
    окно консоли рядом с иконкой. У оконной сборки своих потоков вывода нет,
    поэтому из терминала мы подключаемся к его консоли, а без неё
    подставляем заглушку: `print` не должен ронять команду только потому,
    что писать некуда.
    """
    if detect().platform is not Platform.WINDOWS:
        return
    if sys.stdout is not None and sys.stderr is not None and sys.stdin is not None:
        return  # консольная сборка: потоки на месте

    import ctypes

    attached = False
    try:
        attached = bool(ctypes.windll.kernel32.AttachConsole(-1))  # ATTACH_PARENT_PROCESS
    except (AttributeError, OSError):
        attached = False

    for name in ("stdout", "stderr"):
        if getattr(sys, name) is not None:
            continue
        try:
            target = "CONOUT$" if attached else os.devnull
            setattr(sys, name, open(target, "w", encoding="utf-8", errors="replace"))
        except OSError:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))

    # ввод чинится так же: без него `input` в `hotkey set` и `setup` падает
    # на `sys.stdin = None`, а спросить человека всё равно было бы нечем
    if sys.stdin is None:
        try:
            sys.stdin = open("CONIN$" if attached else os.devnull, encoding="utf-8")
        except OSError:
            sys.stdin = open(os.devnull, encoding="utf-8")


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

    Без этого Qt отдаёт логические координаты, а gdigrab работает в физических,
    и при масштабе экрана 125%/150% записывается не та область, что выделили.
    Зовётся до создания `QApplication` (`qt.application`): после того как Qt
    поднялся, режим процесса уже не сменить.
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
    return _virtual_desktop_qt()


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
        out = proc.run(
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


def _virtual_desktop_qt() -> Region:
    """Границы всех экранов по мнению Qt — запасной путь для macOS и Wayland.

    Считаем в физических пикселях: именно ими меряет захват экрана, а Qt
    отдаёт логические точки.
    """
    from PySide6.QtGui import QGuiApplication

    from .qt import application

    application()
    united = None
    for screen in QGuiApplication.screens():
        ratio = screen.devicePixelRatio()
        geometry = screen.geometry()
        box = Region(
            round(geometry.x() * ratio),
            round(geometry.y() * ratio),
            round(geometry.width() * ratio),
            round(geometry.height() * ratio),
        )
        united = box if united is None else _union(united, box)
    if united is None:
        raise RuntimeError("Qt не нашёл ни одного экрана")
    return united


def _union(first: Region, second: Region) -> Region:
    left = min(first.x, second.x)
    top = min(first.y, second.y)
    right = max(first.right, second.right)
    bottom = max(first.bottom, second.bottom)
    return Region(left, top, right - left, bottom - top)

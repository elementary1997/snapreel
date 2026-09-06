"""Что нужно snapreel в системе и как это поставить.

Держим знание о пакетах в одном месте: `doctor` показывает недостающее,
`setup` ставит его руками пакетного менеджера.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field

from .config import Config
from .platform_info import Environment, Platform, detect


@dataclass(frozen=True)
class Requirement:
    key: str
    reason: str
    packages: dict[str, str] = field(default_factory=dict)
    optional: bool = False

    def package_for(self, manager: str) -> str | None:
        return self.packages.get(manager)


# apt/dnf/pacman/zypper — Linux, brew — macOS, winget — Windows
FFMPEG = Requirement(
    key="ffmpeg",
    reason="без него нечем писать и кодировать видео",
    packages={
        "apt": "ffmpeg",
        "dnf": "ffmpeg",
        "pacman": "ffmpeg",
        "zypper": "ffmpeg",
        "brew": "ffmpeg",
        "winget": "Gyan.FFmpeg",
    },
)

XCLIP = Requirement(
    key="xclip",
    reason="без него файл не попадёт в буфер обмена X11",
    packages={"apt": "xclip", "dnf": "xclip", "pacman": "xclip", "zypper": "xclip"},
)

WL_CLIPBOARD = Requirement(
    key="wl-copy",
    reason="без него файл не попадёт в буфер обмена Wayland",
    packages={
        "apt": "wl-clipboard",
        "dnf": "wl-clipboard",
        "pacman": "wl-clipboard",
        "zypper": "wl-clipboard",
    },
)

WF_RECORDER = Requirement(
    key="wf-recorder",
    reason="им идёт захват экрана в Wayland",
    packages={
        "apt": "wf-recorder",
        "dnf": "wf-recorder",
        "pacman": "wf-recorder",
        "zypper": "wf-recorder",
    },
)

XCB_CURSOR = Requirement(
    key="libxcb-cursor",
    reason="без неё Qt не поднимет окно: с версии 6.5 плагин xcb требует эту библиотеку",
    packages={
        "apt": "libxcb-cursor0",
        "dnf": "xcb-util-cursor",
        "pacman": "xcb-util-cursor",
        "zypper": "libxcb-cursor0",
    },
)

NOTIFY_SEND = Requirement(
    key="notify-send",
    reason="уведомление о готовом клипе",
    packages={
        "apt": "libnotify-bin",
        "dnf": "libnotify",
        "pacman": "libnotify",
        "zypper": "libnotify-tools",
    },
    optional=True,
)


def _library_present(name: str) -> bool:
    """Есть ли библиотека в системе: спрашиваем ldconfig, а не гадаем по путям."""
    if not shutil.which("ldconfig"):
        return True  # спросить не у кого — не пугаем человека зря
    try:
        listing = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True, errors="replace", timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return name in listing.stdout


def required(env: Environment | None = None) -> list[Requirement]:
    """Полный список того, что нужно на этой платформе."""
    env = env or detect()
    items = [FFMPEG]
    if env.is_linux:
        items.append(XCB_CURSOR)
    if env.platform is Platform.LINUX_X11:
        items += [XCLIP, NOTIFY_SEND]
    elif env.platform is Platform.LINUX_WAYLAND:
        items += [WF_RECORDER, WL_CLIPBOARD, NOTIFY_SEND]
    return items


def is_satisfied(requirement: Requirement, config: Config | None = None) -> bool:
    if requirement.key == "libxcb-cursor":
        # это библиотека, а не команда: её ищет линковщик, а не PATH
        return _library_present("libxcb-cursor.so.0")
    if requirement.key == "ffmpeg" and config:
        return bool(shutil.which(config.ffmpeg_path))
    return bool(shutil.which(requirement.key))


def missing(config: Config, env: Environment | None = None) -> list[Requirement]:
    return [item for item in required(env) if not is_satisfied(item, config)]


# --- пакетные менеджеры ---------------------------------------------------


def package_manager(env: Environment | None = None) -> str | None:
    """Какой менеджер пакетов доступен. None — ставить придётся вручную."""
    env = env or detect()
    if env.platform is Platform.WINDOWS:
        return "winget" if shutil.which("winget") else None
    if env.platform is Platform.MACOS:
        return "brew" if shutil.which("brew") else None
    for manager in ("apt", "dnf", "pacman", "zypper"):
        if shutil.which(manager) or shutil.which(f"{manager}-get"):
            return manager
    return None


KNOWN_MANAGERS = ("apt", "dnf", "pacman", "zypper", "brew", "winget")


def install_commands(manager: str, requirements: list[Requirement]) -> list[list[str]]:
    """Команды установки. Для winget — по одной на пакет, он не берёт списком."""
    if manager not in KNOWN_MANAGERS:
        raise ValueError(f"неизвестный пакетный менеджер {manager!r}")
    packages = [
        package for requirement in requirements if (package := requirement.package_for(manager))
    ]
    if not packages:
        return []
    if manager == "winget":
        return [
            [
                "winget",
                "install",
                "--exact",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--id",
                package,
            ]
            for package in packages
        ]
    if manager == "brew":
        return [["brew", "install", *packages]]
    sudo = [] if _is_root() else ["sudo"]
    if manager == "apt":
        return [
            [*sudo, "apt-get", "update"],
            [*sudo, "apt-get", "install", "-y", *packages],
        ]
    if manager == "dnf":
        return [[*sudo, "dnf", "install", "-y", *packages]]
    if manager == "pacman":
        return [[*sudo, "pacman", "-S", "--needed", "--noconfirm", *packages]]
    return [[*sudo, "zypper", "install", "-y", *packages]]


def unresolved(manager: str, requirements: list[Requirement]) -> list[Requirement]:
    """То, для чего у этого менеджера пакета нет — например xclip в winget."""
    return [item for item in requirements if item.package_for(manager) is None]


def _is_root() -> bool:
    if platform.system() == "Windows":
        return False
    import os

    return os.geteuid() == 0


def run(commands: list[list[str]]) -> tuple[bool, str]:
    """Выполняет команды по очереди; первая же неудача останавливает установку."""
    for command in commands:
        try:
            result = subprocess.run(command)
        except OSError as exc:
            return False, f"{' '.join(command)}: {exc}"
        if result.returncode != 0:
            return False, f"{' '.join(command)} завершилась с кодом {result.returncode}"
    return True, ""

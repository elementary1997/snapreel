"""Список звуковых устройств — тех, что понимает бэкенд захвата.

Имя устройства человек не должен печатать по памяти: на Windows это точная
строка из dshow, на macOS — номер входа avfoundation, в Linux — имя источника
pulse. Спрашиваем их у того же ffmpeg, который потом будет писать звук, —
иначе список и запись разойдутся.

Разбор вывода отделён от запуска: у ffmpeg он идёт в stderr, форматы у трёх
платформ разные, и проверять их надо без самого ffmpeg.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

from .config import Config
from .platform_info import Environment, Platform, detect

TIMEOUT = 8


@dataclass(frozen=True)
class Device:
    """Что показать человеку и что записать в конфиг."""

    value: str  # то, что уходит в ffmpeg
    label: str  # то, что видно в списке


def devices(config: Config | None = None, env: Environment | None = None) -> list[Device]:
    """Устройства этой машины. Пустой список — спросить не вышло."""
    env = env or detect()
    config = config or Config()
    try:
        if env.platform is Platform.WINDOWS:
            return parse_dshow(_ffmpeg_devices(config, ["-f", "dshow", "-i", "dummy"]))
        if env.platform is Platform.MACOS:
            return parse_avfoundation(_ffmpeg_devices(config, ["-f", "avfoundation", "-i", ""]))
        return _pulse_devices()
    except (OSError, subprocess.SubprocessError):
        return []


def _ffmpeg_devices(config: Config, tail: list[str]) -> str:
    """`-list_devices true` печатает список и завершается ошибкой — это норма."""
    command = [config.ffmpeg_path, "-hide_banner", "-list_devices", "true", *tail]
    result = subprocess.run(
        command, capture_output=True, text=True, errors="replace", timeout=TIMEOUT
    )
    return result.stderr


def parse_dshow(output: str) -> list[Device]:
    """Windows: имена в кавычках под заголовком про audio devices.

    Формат менялся между версиями ffmpeg — раньше тип писали в скобках в той
    же строке, теперь он идёт заголовком секции, — поэтому понимаем оба.
    """
    found: list[Device] = []
    audio = False
    for line in output.splitlines():
        lowered = line.lower()
        if "devices" in lowered and ("audio" in lowered or "video" in lowered):
            audio = "audio" in lowered
            continue
        if "alternative name" in lowered:
            continue
        match = re.search(r'"([^"]+)"', line)
        if not match:
            continue
        name = match.group(1)
        if audio or "(audio)" in lowered:
            found.append(Device(name, name))
    return found


def parse_avfoundation(output: str) -> list[Device]:
    """macOS: номер входа в скобках; в конфиг уходит именно он."""
    found: list[Device] = []
    audio = False
    for line in output.splitlines():
        lowered = line.lower()
        if "audio devices" in lowered:
            audio = True
            continue
        if "video devices" in lowered:
            audio = False
            continue
        match = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if audio and match:
            found.append(Device(match.group(1), f"[{match.group(1)}] {match.group(2)}"))
    return found


def parse_pactl(output: str) -> list[Device]:
    """Linux: короткий список источников pulse, первое поле — имя."""
    found: list[Device] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1].strip()
        if not name:
            continue
        # мониторы — это «то, что слышно из колонок», их и берут для записи
        label = f"{name} (звук системы)" if name.endswith(".monitor") else name
        found.append(Device(name, label))
    return found


def _pulse_devices() -> list[Device]:
    if not shutil.which("pactl"):
        return []
    result = subprocess.run(
        ["pactl", "list", "short", "sources"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=TIMEOUT,
    )
    if result.returncode != 0:
        return []
    return parse_pactl(result.stdout)

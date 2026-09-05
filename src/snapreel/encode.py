"""Пост-обработка записи: GIF и справка о готовом файле."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config


class EncodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    duration: float
    size_bytes: int

    @property
    def human_size(self) -> str:
        value = float(self.size_bytes)
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if value < 1024 or unit == "ГБ":
                return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} ГБ"


def to_gif(source: Path, target: Path, config: Config) -> Path:
    """Двухпроходная палитра в одном вызове: без неё GIF получается грязным."""
    # min(...) не даёт растянуть узкую область до gif_max_width
    scale = (
        f"scale=w='min({config.gif_max_width},iw)':h=-1:flags=lanczos,"
        if config.gif_max_width > 0
        else ""
    )
    filters = (
        f"fps={config.gif_fps},{scale}split[a][b];"
        "[a]palettegen=stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )
    command = [
        config.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter_complex",
        filters,
        "-loop",
        "0",
        "-y",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not target.is_file():
        raise EncodeError(f"не собрать GIF: {result.stderr.strip()[:500]}")
    return target


def probe(path: Path, config: Config) -> MediaInfo | None:
    """Размеры и длительность файла; без ffprobe возвращает только размер на диске."""
    size = path.stat().st_size if path.is_file() else 0
    command = [
        config.ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        duration = float((data.get("format") or {}).get("duration", 0.0))
        return MediaInfo(
            width=int(stream.get("width", 0)),
            height=int(stream.get("height", 0)),
            duration=duration,
            size_bytes=size,
        )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return MediaInfo(0, 0, 0.0, size) if size else None

"""Захват экрана в macOS через ffmpeg/avfoundation."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .. import proc
from ..region import Region
from .base import CaptureBackend, CaptureError, encode_args, require_binary

_SCREEN_LINE = re.compile(r"\[(\d+)\]\s+Capture screen\s*(\d+)?", re.IGNORECASE)
_VIDEO_SIZE = re.compile(r"Stream #0:0.*?,\s*(\d{2,5})x(\d{2,5})")


def _cache_file() -> Path:
    return Path.home() / "Library" / "Caches" / "snapreel" / "probe.json"


class AvFoundationBackend(CaptureBackend):
    """avfoundation отдаёт экран целиком, область вырезается фильтром crop.

    На Retina поток идёт в физических пикселях, а Tk выделяет область в
    логических точках, поэтому координаты домножаются на масштаб, измеренный
    пробным кадром. Результат кешируется — проба занимает около секунды.
    """

    name = "avfoundation"

    def preflight(self) -> list[str]:
        return require_binary(self.config.ffmpeg_path, "brew install ffmpeg")

    def screen_index(self) -> int:
        if self.config.screen_index >= 0:
            return self.config.screen_index
        cached = self._read_cache().get("screen_index")
        if isinstance(cached, int):
            return cached
        index = self._discover_screen_index()
        self._write_cache(screen_index=index)
        return index

    def scale(self) -> float:
        cached = self._read_cache().get("scale")
        if isinstance(cached, (int, float)) and cached > 0:
            return float(cached)
        value = self._measure_scale()
        self._write_cache(scale=value)
        return value

    def build_command(self, region: Region, output: Path, duration: float) -> list[str]:
        cfg = self.config
        scale = self.scale()
        crop = _scaled_crop(region, scale)
        device = (
            f"{self.screen_index()}:{cfg.audio_device}"
            if (cfg.capture_audio and cfg.audio_device)
            else f"{self.screen_index()}:none"
        )
        command = [
            cfg.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            "-capture_cursor",
            "1" if cfg.capture_cursor else "0",
            "-framerate",
            str(cfg.fps),
            "-i",
            device,
            "-t",
            f"{duration:.3f}",
            "-vf",
            f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}",
        ]
        command += encode_args(cfg)
        if cfg.capture_audio and cfg.audio_device:
            command += ["-c:a", "aac", "-b:a", "128k"]
        else:
            command += ["-an"]
        command += ["-y", str(output)]
        return command

    # --- пробы устройства -------------------------------------------------

    def _list_devices(self) -> str:
        result = proc.run(
            [
                self.config.ffmpeg_path,
                "-hide_banner",
                "-f",
                "avfoundation",
                "-list_devices",
                "true",
                "-i",
                "",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20,
        )
        # ffmpeg завершается с ошибкой — это ожидаемо, список уходит в stderr
        return result.stderr

    def _discover_screen_index(self) -> int:
        try:
            output = self._list_devices()
        except (subprocess.SubprocessError, OSError) as exc:
            raise CaptureError(f"не опросить устройства avfoundation: {exc}") from exc
        for line in output.splitlines():
            match = _SCREEN_LINE.search(line)
            if match:
                return int(match.group(1))
        raise CaptureError(
            "avfoundation не показал ни одного экрана — проверьте разрешение "
            "«Запись экрана» в Системных настройках для терминала или snapreel"
        )

    def _measure_scale(self) -> float:
        """Один кадр захвата показывает реальное разрешение экрана в пикселях."""
        from ..platform_info import virtual_desktop

        logical = virtual_desktop()
        try:
            result = proc.run(
                [
                    self.config.ffmpeg_path,
                    "-hide_banner",
                    "-f",
                    "avfoundation",
                    "-i",
                    f"{self.screen_index()}:none",
                    "-frames:v",
                    "1",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError):
            return 1.0
        match = _VIDEO_SIZE.search(result.stderr)
        if not match or not logical.width:
            return 1.0
        physical_width = int(match.group(1))
        ratio = physical_width / logical.width
        # реальные значения — 1.0 или 2.0; всё остальное считаем шумом пробы
        return 2.0 if ratio > 1.5 else 1.0

    def _read_cache(self) -> dict:
        path = _cache_file()
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_cache(self, **values) -> None:
        path = _cache_file()
        data = self._read_cache()
        data.update(values)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass


def _scaled_crop(region: Region, scale: float) -> tuple[int, int, int, int]:
    """Переводит область в пиксели устройства, сохраняя чётность сторон."""
    x = round(region.x * scale)
    y = round(region.y * scale)
    width = round(region.width * scale)
    height = round(region.height * scale)
    return x, y, width - (width % 2), height - (height % 2)

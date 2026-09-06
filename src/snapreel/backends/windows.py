"""Захват экрана в Windows через ffmpeg/gdigrab."""

from __future__ import annotations

from pathlib import Path

from ..region import Region
from .base import CaptureBackend, encode_args, require_binary


class GdigrabBackend(CaptureBackend):
    """gdigrab умеет снимать произвольный прямоугольник рабочего стола.

    Смещения могут быть отрицательными — это нормальный случай для монитора,
    стоящего слева от основного.
    """

    name = "gdigrab"

    def preflight(self) -> list[str]:
        return require_binary(
            self.config.ffmpeg_path,
            "поставьте ffmpeg: winget install Gyan.FFmpeg",
        )

    def build_command(self, region: Region, output: Path, duration: float) -> list[str]:
        cfg = self.config
        command = [
            cfg.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "gdigrab",
            "-framerate",
            str(cfg.fps),
            "-draw_mouse",
            "1" if cfg.capture_cursor else "0",
            "-offset_x",
            str(region.x),
            "-offset_y",
            str(region.y),
            "-video_size",
            f"{region.width}x{region.height}",
            "-i",
            "desktop",
        ]
        if cfg.capture_audio and cfg.audio_device:
            command += ["-f", "dshow", "-i", f"audio={cfg.audio_device}"]
        command += ["-t", f"{duration:.3f}"]
        command += encode_args(cfg)
        if cfg.capture_audio and cfg.audio_device:
            command += ["-c:a", "aac", "-b:a", "128k"]
        else:
            command += ["-an"]
        command += ["-y", str(output)]
        return command

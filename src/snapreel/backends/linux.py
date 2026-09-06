"""Захват экрана в Linux: x11grab для X11, wf-recorder для wlroots-Wayland."""

from __future__ import annotations

import os
from pathlib import Path

from ..region import Region
from .base import CaptureBackend, encode_args, require_binary


class X11GrabBackend(CaptureBackend):
    name = "x11grab"

    def preflight(self) -> list[str]:
        problems = require_binary(self.config.ffmpeg_path, "sudo apt install ffmpeg")
        if not os.environ.get("DISPLAY"):
            problems.append("не задан DISPLAY — нет доступа к X-серверу")
        return problems

    def build_command(self, region: Region, output: Path, duration: float) -> list[str]:
        cfg = self.config
        display = os.environ.get("DISPLAY", ":0")
        command = [
            cfg.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-framerate",
            str(cfg.fps),
            "-draw_mouse",
            "1" if cfg.capture_cursor else "0",
            "-video_size",
            f"{region.width}x{region.height}",
            "-i",
            f"{display}+{region.x},{region.y}",
        ]
        if cfg.capture_audio:
            source = cfg.audio_device or "default"
            command += ["-f", "pulse", "-i", source]
        command += ["-t", f"{duration:.3f}"]
        command += encode_args(cfg)
        if cfg.capture_audio:
            command += ["-c:a", "aac", "-b:a", "128k"]
        else:
            command += ["-an"]
        command += ["-y", str(output)]
        return command


class WfRecorderBackend(CaptureBackend):
    """Wayland через wf-recorder.

    Работает на композиторах wlroots (Sway, Hyprland, river). У GNOME и KDE
    свои протоколы записи, wf-recorder там не заработает — для них остаётся
    сессия X11 либо запись средствами самого окружения.

    Ограничения по времени у wf-recorder нет, поэтому длительность держит
    таймер в рекордере, а останов идёт по SIGINT.
    """

    name = "wf-recorder"
    graceful_stop = "sigint"

    def preflight(self) -> list[str]:
        problems = require_binary(
            "wf-recorder", "sudo apt install wf-recorder (нужен композитор на wlroots)"
        )
        if not os.environ.get("WAYLAND_DISPLAY"):
            problems.append("не задан WAYLAND_DISPLAY")
        return problems

    def build_command(self, region: Region, output: Path, duration: float) -> list[str]:
        cfg = self.config
        command = [
            "wf-recorder",
            "--geometry",
            f"{region.x},{region.y} {region.width}x{region.height}",
            "--codec",
            "libx264",
            "--pixel-format",
            "yuv420p",
            "--framerate",
            str(cfg.fps),
            "--codec-param",
            f"crf={cfg.crf}",
            "--codec-param",
            f"preset={cfg.preset}",
        ]
        if cfg.capture_audio:
            command += ["--audio"] if not cfg.audio_device else [f"--audio={cfg.audio_device}"]
        command += ["--overwrite", "--file", str(output)]
        return command

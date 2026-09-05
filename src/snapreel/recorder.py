"""Сценарий целиком: выделить область, записать, упаковать, положить в буфер."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from . import clipboard, notify, storage
from .backends import CaptureError, for_environment
from .config import Config
from .encode import MediaInfo, probe, to_gif
from .platform_info import Environment, detect, enable_dpi_awareness, virtual_desktop
from .region import Region, clamp, normalize
from .selector import select_region


@dataclass
class Result:
    video: Path
    region: Region
    info: MediaInfo | None
    gif: Path | None = None
    clipboard_error: str | None = None
    user_stopped: bool = False

    @property
    def payload(self) -> Path:
        """Файл, который уходит в буфер обмена."""
        return self.gif or self.video

    @property
    def clipboard_ok(self) -> bool:
        return self.clipboard_error is None


def record(
    config: Config,
    *,
    region: Region | None = None,
    as_gif: bool = False,
    indicator: bool = True,
    env: Environment | None = None,
) -> Result:
    env = env or detect()
    enable_dpi_awareness()
    backend = for_environment(config, env)

    problems = backend.preflight()
    if problems:
        raise CaptureError("; ".join(problems))

    if region is None:
        region = select_region(env)
    region = normalize(clamp(region, virtual_desktop(env)))

    storage.prune(config)
    video_path = storage.new_path(config, ".mp4")
    recording = backend.start(region, video_path, config.max_seconds)

    user_stopped = False
    try:
        widget_cls = _indicator_class() if indicator else None
        if widget_cls is not None:
            widget = widget_cls(
                region,
                max_seconds=config.max_seconds,
                min_seconds=config.min_seconds,
                is_finished=lambda: recording.finished,
                elapsed=lambda: recording.elapsed,
                request_stop=recording.stop,
                env=env,
            )
            user_stopped = widget.run()
        else:
            _wait_out(recording, config.max_seconds)
    finally:
        code = recording.stop(timeout=20)

    if not video_path.is_file() or video_path.stat().st_size == 0:
        raise CaptureError(f"запись не создала файл (код {code}).\n{recording.stderr_tail}".strip())

    result = Result(
        video=video_path,
        region=region,
        info=probe(video_path, config),
        user_stopped=user_stopped,
    )

    if as_gif:
        result.gif = to_gif(video_path, video_path.with_suffix(".gif"), config)
        result.info = probe(result.gif, config) or result.info

    _to_clipboard(result, config, env)
    _announce(result, config, env)
    return result


def _indicator_class():
    """Без tkinter запись всё равно идёт — просто молча и до конца таймера."""
    try:
        from .indicator import RecordingIndicator
    except ImportError:
        return None
    return RecordingIndicator


def _wait_out(recording, limit: float) -> None:
    deadline = time.monotonic() + limit + 1
    while not recording.finished and time.monotonic() < deadline:
        time.sleep(0.1)


def _to_clipboard(result: Result, config: Config, env: Environment) -> None:
    # в буфере живёт что-то одно: либо файл, либо путь к нему
    try:
        if config.copy_path_as_text:
            clipboard.copy_text(str(result.payload), env)
        else:
            clipboard.copy_files([result.payload], env)
    except Exception as exc:  # буфер не должен ронять уже готовую запись
        result.clipboard_error = str(exc)


def _announce(result: Result, config: Config, env: Environment) -> None:
    if not config.notify:
        return
    info = result.info
    details = result.payload.name
    if info:
        details = f"{details} · {info.human_size}"
        if info.duration:
            details = f"{details} · {info.duration:.1f} с"
    title = "Клип в буфере обмена" if result.clipboard_ok else "Клип записан"
    notify.send(title, details, env)

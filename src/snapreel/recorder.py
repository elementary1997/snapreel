"""Сценарий целиком: выделить область, записать, упаковать, положить в буфер."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import clipboard, notify, storage
from .backends import CaptureError, Recording, for_environment
from .config import Config
from .encode import EncodeError, MediaInfo, probe, to_gif
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
    gif_error: str | None = None
    user_stopped: bool = False

    @property
    def payload(self) -> Path:
        """Файл, который уходит в буфер обмена."""
        return self.gif or self.video

    @property
    def clipboard_ok(self) -> bool:
        return self.clipboard_error is None


@dataclass
class Session:
    """Снятая область, пишущийся файл и всё, что понадобится упаковке.

    Существует ради трея: у него запись идёт в главном потоке — оверлей и
    рамка иначе не нарисуются, — а упаковка (ffmpeg, буфер обмена) в нём
    висеть не вправе, иначе иконка замолкает на десятки секунд. Поэтому
    сценарий разрезан ровно там, где заканчивается работа с окнами.
    """

    config: Config
    env: Environment
    region: Region
    recording: Recording
    video: Path
    as_gif: bool
    user_stopped: bool = False
    # запись идёт из резидента (трея), и он переживёт её: на Linux это
    # решает, кому владеть буфером обмена
    resident: bool = False


def record(
    config: Config,
    *,
    region: Region | None = None,
    as_gif: bool = False,
    indicator: bool = True,
    env: Environment | None = None,
) -> Result:
    """Весь сценарий целиком — так его зовёт командная строка."""
    return finish(start(config, region=region, as_gif=as_gif, indicator=indicator, env=env))


def start(
    config: Config,
    *,
    region: Region | None = None,
    as_gif: bool = False,
    indicator: bool = True,
    env: Environment | None = None,
    on_started: Callable[[Recording], None] | None = None,
    resident: bool = False,
) -> Session:
    """Часть с окнами: выделение области, запись и рамка с таймером.

    Возвращается, когда запись остановлена — человеком, таймером или самим
    ffmpeg, — и на диске уже лежит файл.

    `on_started` получает запущенную запись, пока рамка ещё висит: трею нужно
    за что-то дёрнуть, если человек выбрал «Выйти» посреди записи.
    """
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
    session = Session(
        config=config,
        env=env,
        region=region,
        recording=recording,
        video=video_path,
        as_gif=as_gif,
        resident=resident,
    )
    if on_started is not None:
        on_started(recording)

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
            session.user_stopped = widget.run()
        else:
            _wait_out(recording, config.max_seconds)
    except BaseException:
        recording.stop(timeout=20)  # оверлей сорвался — ffmpeg не бросаем
        raise
    return session


def finish(session: Session) -> Result:
    """Часть без окон: дописать файл, собрать GIF, положить в буфер, сказать.

    Ни одной строки Qt здесь нет намеренно — трей выполняет это в фоновом
    потоке, пока иконка и меню продолжают отвечать.
    """
    config, env = session.config, session.env
    recording = session.recording
    video_path = session.video
    code = recording.stop(timeout=20)

    if not video_path.is_file() or video_path.stat().st_size == 0:
        raise CaptureError(f"запись не создала файл (код {code}).\n{recording.stderr_tail}".strip())

    result = Result(
        video=video_path,
        region=session.region,
        info=probe(video_path, config),
        user_stopped=session.user_stopped,
    )

    if session.as_gif:
        # GIF — обёртка над уже записанным клипом; её провал не повод терять MP4
        try:
            result.gif = to_gif(video_path, video_path.with_suffix(".gif"), config)
            result.info = probe(result.gif, config) or result.info
        except EncodeError as exc:
            result.gif_error = str(exc)

    _to_clipboard(result, config, env, session.resident)
    _announce(result, config, env)
    return result


def _indicator_class():
    """Без Qt запись всё равно идёт — просто молча и до конца таймера."""
    try:
        from .indicator import RecordingIndicator
    except ImportError:
        return None
    return RecordingIndicator


def _wait_out(recording, limit: float) -> None:
    deadline = time.monotonic() + limit + 1
    while not recording.finished and time.monotonic() < deadline:
        time.sleep(0.1)


def _to_clipboard(result: Result, config: Config, env: Environment, resident: bool = False) -> None:
    # в буфере живёт что-то одно: либо файл, либо путь к нему
    try:
        if config.clipboard == "path":
            clipboard.copy_text(str(result.payload), env)
        else:
            clipboard.copy_files([result.payload], env, resident=resident)
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

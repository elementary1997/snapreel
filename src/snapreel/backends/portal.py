"""Захват разрешённых мониторов KDE/GNOME через ScreenCast и PipeWire."""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from .. import autostart, proc
from ..config import Config
from ..region import Region
from .base import CaptureBackend, CaptureError, Recording


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def desktop(streams) -> Region:
    left = min(stream.bounds.x for stream in streams)
    top = min(stream.bounds.y for stream in streams)
    right = max(stream.bounds.right for stream in streams)
    bottom = max(stream.bounds.bottom for stream in streams)
    return Region(left, top, right - left, bottom - top)


def build_pipeline(config: Config, streams, region: Region, output: Path, *, snapshot=False) -> str:
    """Потоки масштабируются в координаты композитора до обрезки.

    Так 125% и разные масштабы мониторов не смещают выбранную область;
    отрицательные позиции экранов сохраняются до пересечения с областью.
    """
    branches = []
    positions = []
    for stream in streams:
        bounds = stream.bounds
        left, top = max(region.x, bounds.x), max(region.y, bounds.y)
        right, bottom = min(region.right, bounds.right), min(region.bottom, bounds.bottom)
        if right <= left or bottom <= top:
            continue
        index = len(branches)
        positions += [
            f"sink_{index}::xpos={left - region.x}",
            f"sink_{index}::ypos={top - region.y}",
        ]
        source = (
            f"pipewiresrc fd={stream.fd} path={stream.node} do-timestamp=true "
            f"keepalive-time={max(1, 1000 // config.fps)}"
        )
        if stream.serial is not None:
            source += f" target-object={stream.serial}"
        if snapshot:
            source += " num-buffers=1"
        branches.append(
            f"{source} ! queue ! videoconvert ! videoscale ! "
            f"video/x-raw,format=RGBA,width={bounds.width},height={bounds.height},"
            "pixel-aspect-ratio=1/1 ! "
            f"videocrop left={left - bounds.x} top={top - bounds.y} "
            f"right={bounds.right - right} bottom={bounds.bottom - bottom} ! mix.sink_{index}"
        )
    if not branches:
        raise CaptureError(
            "Область вне разрешённых экранов — разрешите нужный монитор в диалоге портала."
        )
    pipeline = f"compositor name=mix background=black {' '.join(positions)} ! "
    pipeline += f"video/x-raw,width={region.width},height={region.height} ! videoconvert ! "
    if snapshot:
        pipeline += f"pngenc snapshot=true ! filesink location={_quote(str(output))} "
    else:
        pipeline += (
            f"videorate max-closing-segment-duplication-duration=0 ! "
            f"video/x-raw,format=I420,framerate={config.fps}/1 ! "
            f"x264enc speed-preset={config.preset} pass=qual quantizer={config.crf} ! "
            "h264parse ! queue ! mux. mp4mux name=mux faststart=true ! "
            f"filesink location={_quote(str(output))} "
        )
        if config.capture_audio:
            audio = f" device={_quote(config.audio_device)}" if config.audio_device else ""
            pipeline += (
                f"pulsesrc do-timestamp=true{audio} ! queue ! audioconvert ! audioresample ! "
                "avenc_aac bitrate=128000 ! aacparse ! queue ! mux. "
            )
    return pipeline + " ".join(branches)


class PortalRecording(Recording):
    def __init__(self, process, output, session):
        super().__init__(process, output)
        self.session = session

    @property
    def finished(self):
        return super().finished or self.session.closed.is_set()

    def wait(self, timeout=10.0):
        try:
            return super().wait(timeout)
        finally:
            self.session.close()

    def kill(self):
        try:
            super().kill()
        finally:
            self.session.close()


class ScreenCastBackend(CaptureBackend):
    name = "ScreenCast/PipeWire"

    def __init__(self, config):
        super().__init__(config)
        self.session = None

    def preflight(self):
        from ..gstreamer import Gst
        from ..portal import PATH, Gio, PortalError

        problems = []
        try:
            missing = Gst().plugins_missing(self.config.capture_audio)
            if missing:
                problems.append(
                    f"Нет плагинов GStreamer: {', '.join(missing)} — запустите snapreel setup"
                )
        except CaptureError as exc:
            problems.append(str(exc))
        gio = None
        try:
            gio = Gio()
            xml = gio.call(PATH, "org.freedesktop.DBus.Introspectable", "Introspect")[0]
            if 'interface name="org.freedesktop.portal.ScreenCast"' not in xml:
                problems.append(
                    "Нет портала ScreenCast — установите xdg-desktop-portal и портал KDE/GNOME"
                )
        except PortalError as exc:
            problems.append(str(exc))
        finally:
            if gio:
                gio.close()
        return problems

    def prepare(self):
        from ..screencast import ScreenSession

        self.session = ScreenSession(self.config.capture_cursor)
        self.session.start()

    @property
    def desktop(self):
        return desktop(self.session.streams)

    def close(self):
        if self.session:
            self.session.close()

    def indicator_class(self, resident):
        from functools import partial

        from ..indicator import PortalIndicator

        return partial(PortalIndicator, resident=resident)

    def select_region(self, env):
        from ..selector import select_preview

        self.session.renew()
        with tempfile.TemporaryDirectory(prefix="snapreel-preview-") as directory:
            path = Path(directory) / "frame.png"
            pipeline = build_pipeline(
                self.config, self.session.streams, self.desktop, path, snapshot=True
            )
            process = self._launch(pipeline, path, 10)
            deadline = time.monotonic() + 12
            import threading

            from ..screencast import wait_ready

            done = threading.Event()

            def wait():
                try:
                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                finally:
                    done.set()

            threading.Thread(target=wait, daemon=True).start()
            wait_ready(done)
            process._drain()
            if not path.is_file():
                raise CaptureError(f"Не получить кадр PipeWire. {process.stderr_tail}")
            return select_preview(path, self.desktop, self.config)

    def build_command(self, region, output, duration):
        pipeline = build_pipeline(self.config, self.session.streams, region, output)
        return autostart.argv_for("--capture-worker", pipeline, str(duration))

    def _launch(self, pipeline, output, duration):
        try:
            process = proc.popen(
                autostart.argv_for("--capture-worker", pipeline, str(duration)),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                pass_fds=tuple(stream.fd for stream in self.session.streams),
            )
        except OSError as exc:
            raise CaptureError(f"Не начать запись PipeWire: {exc}. Повторите запись.") from exc
        return Recording(process, output)

    def start(self, region, output, duration):
        if self.session is None:
            self.prepare()
        self.session.renew()
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            command = self.build_command(region, output, duration)
            process = proc.popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                pass_fds=tuple(stream.fd for stream in self.session.streams),
            )
        except (OSError, CaptureError) as exc:
            self.close()
            raise CaptureError(f"Не начать запись PipeWire: {exc}. Повторите запись.") from exc
        return PortalRecording(process, output, self.session)

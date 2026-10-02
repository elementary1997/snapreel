"""Разрешение на выбранные экраны и закрытый PipeWire remote от портала."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import autostart, proc
from .backends.base import CaptureError
from .portal import SESSION, Gio, PortalError
from .region import Region

INTERFACE = "org.freedesktop.portal.ScreenCast"


@dataclass(frozen=True)
class Stream:
    node: int
    bounds: Region
    fd: int
    serial: int | None = None


def cache_path() -> Path:
    return (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "snapreel"
        / "screencast.json"
    )


def wait_ready(event: threading.Event):
    app = None
    try:
        from PySide6.QtCore import QCoreApplication, QEventLoop

        app = QCoreApplication.instance()
    except ImportError:
        pass
    while not event.wait(0.01):
        if app is not None:
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)


def stream_size(fd: int, node: int) -> tuple[int, int]:
    """Размер и положение в ответе портала необязательны, в KDE их может не быть.

    Размер берём из первого кадра самого разрешённого потока, без доступа к
    чужим экранам. Кадр остаётся только во временном каталоге и удаляется.
    """
    with tempfile.TemporaryDirectory(prefix="snapreel-size-") as directory:
        path = Path(directory) / "frame.png"
        pipeline = (
            f"pipewiresrc fd={fd} path={node} num-buffers=1 ! "
            f'videoconvert ! pngenc snapshot=true ! filesink location="{path}"'
        )
        process = proc.popen(
            autostart.argv_for("--capture-worker", pipeline, "10"),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            pass_fds=(fd,),
        )
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        finally:
            process.stdin.close()
        reason = process.stderr.read().decode("utf-8", "replace")
        process.stderr.close()
        if not path.is_file() or path.stat().st_size < 24:
            raise CaptureError(f"Не получить кадр PipeWire: {reason}. Повторите запись.")
        with path.open("rb") as image:
            header = image.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n":
            raise CaptureError("PipeWire не дал кадр PNG — проверьте плагины GStreamer.")
        return struct.unpack(">II", header[16:24])


class ScreenSession:
    def __init__(self, cursor: bool):
        self.cursor = cursor
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.stopped = threading.Event()
        self.streams: list[Stream] = []
        self.error = None
        self.session = ""
        self.lock = threading.RLock()
        self.gio = None
        self.thread = threading.Thread(target=self._run, name="snapreel-screencast", daemon=True)

    def start(self):
        self.thread.start()
        try:
            wait_ready(self.ready)
        except BaseException:
            self.close()
            raise
        if self.error:
            self.close()
            raise CaptureError(str(self.error)) from self.error

    def _run(self):
        gio = None
        context = None
        try:
            gio = Gio()
            gio.register_app()
            self.gio = gio
            context = gio.glib.g_main_context_new()
            gio.glib.g_main_context_push_thread_default(context)
            token = "snapreel_" + uuid.uuid4().hex

            def request(method, arguments):
                return gio.request(context, self.stopped, method, arguments, interface=INTERFACE)

            created = request(
                "CreateSession",
                lambda options: f"({options[:-1]}, 'session_handle_token': <'{token}'>}},)",
            )
            self.session = created["session_handle"]
            gio.subscribe(self.session, SESSION, "Closed", lambda values: self.closed.set())
            path = cache_path()
            try:
                restore = json.loads(path.read_text()).get("restore_token", "")
            except (OSError, ValueError):
                restore = ""
            selection = (
                "'types': <uint32 1>, 'multiple': <true>, "
                f"'cursor_mode': <uint32 {2 if self.cursor else 1}>, 'persist_mode': <uint32 2>"
            )
            if restore:
                selection += f", 'restore_token': <{json.dumps(restore)}>"
            request(
                "SelectSources",
                lambda options: f"(objectpath '{self.session}', {options[:-1]}, {selection}}})",
            )
            result = request(
                "Start", lambda options: f"(objectpath '{self.session}', '', {options})"
            )
            offset = 0
            for node, properties in result.get("streams", []):
                fd = gio.open_remote(self.session)
                try:
                    size = properties.get("size")
                    width, height = size or stream_size(fd, node)
                    if size is None:
                        os.close(fd)
                        fd = -1
                        fd = gio.open_remote(self.session)
                except BaseException:
                    if fd >= 0:
                        os.close(fd)
                    raise
                x, y = properties.get("position", (offset, 0))
                if width <= 0 or height <= 0:
                    os.close(fd)
                    raise CaptureError("Портал вернул пустой экран — выберите другой монитор.")
                self.streams.append(
                    Stream(
                        node,
                        Region(x, y, width, height),
                        fd,
                        properties.get("pipewire-serial"),
                    )
                )
                offset = max(offset, x + width)
            if not self.streams:
                raise CaptureError(
                    "Экран не выбран — разрешите запись нужного монитора в системном диалоге."
                )
            if result.get("restore_token"):
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(".tmp")
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w") as file:
                        json.dump({"restore_token": result["restore_token"]}, file)
                    temporary.replace(path)
                except OSError:
                    pass  # запись важнее запоминания разрешения
            self.ready.set()
            while not self.stopped.wait(0.01) and not self.closed.is_set():
                gio.glib.g_main_context_iteration(context, False)
        except Exception as exc:
            self.error = exc
        finally:
            self.lock.acquire()
            for stream in self.streams:
                os.close(stream.fd)
            if gio:
                if self.session:
                    try:
                        gio.call(self.session, SESSION, "Close")
                    except PortalError:
                        pass
                gio.close()
                if context:
                    gio.glib.g_main_context_pop_thread_default(context)
                    gio.glib.g_main_context_unref(context)
            self.closed.set()
            self.ready.set()
            self.gio = None
            self.lock.release()

    def renew(self):
        """Каждый потребитель PipeWire получает отдельный сокет, не старое соединение."""
        from dataclasses import replace

        with self.lock:
            if self.closed.is_set() or self.gio is None:
                raise CaptureError("Разрешение на экран закрыто — повторите запись.")
            fresh = []
            try:
                for stream in self.streams:
                    fresh.append(replace(stream, fd=self.gio.open_remote(self.session)))
            except BaseException as exc:
                for stream in fresh:
                    os.close(stream.fd)
                if isinstance(exc, PortalError):
                    raise CaptureError(f"Не открыть поток PipeWire: {exc}") from exc
                raise
            for stream in self.streams:
                os.close(stream.fd)
            self.streams = fresh

    def close(self):
        self.stopped.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=6)

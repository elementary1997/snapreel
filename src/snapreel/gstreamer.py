"""Нативный GStreamer в дочернем процессе: EOS дописывает контейнер.

Отдельный процесс изолирует плагины и их потоки от Qt. Python-модуль gi не
нужен: используем стабильный C API уже установленного GStreamer.
"""

from __future__ import annotations

import ctypes as C
import os
import threading
import time

from .backends.base import CaptureError


class MiniObject(C.Structure):
    _fields_ = [
        ("type", C.c_size_t),
        ("refcount", C.c_int),
        ("lockstate", C.c_int),
        ("flags", C.c_uint),
        ("copy", C.c_void_p),
        ("dispose", C.c_void_p),
        ("free", C.c_void_p),
        ("priv_uint", C.c_uint),
        ("priv_pointer", C.c_void_p),
    ]


class Message(C.Structure):
    _fields_ = [("mini", MiniObject), ("type", C.c_uint)]


class Error(C.Structure):
    _fields_ = [("domain", C.c_uint), ("code", C.c_int), ("message", C.c_char_p)]


class Gst:
    def __init__(self):
        try:
            self.lib = C.CDLL("libgstreamer-1.0.so.0")
            self.glib = C.CDLL("libglib-2.0.so.0")
            self.obj = C.CDLL("libgobject-2.0.so.0")
        except OSError as exc:
            raise CaptureError(
                "Не найден GStreamer — запустите snapreel setup для установки."
            ) from exc
        p = C.c_void_p
        from .portal import Gio

        bind = Gio._bind
        bind(self.lib, "gst_init", None, [p, p])
        bind(self.lib, "gst_parse_launch", p, [C.c_char_p, C.POINTER(p)])
        bind(self.lib, "gst_element_factory_find", p, [C.c_char_p])
        bind(self.lib, "gst_element_set_state", C.c_int, [p, C.c_int])
        bind(self.lib, "gst_element_get_bus", p, [p])
        bind(self.lib, "gst_bus_timed_pop_filtered", p, [p, C.c_uint64, C.c_uint])
        bind(self.lib, "gst_message_parse_error", None, [p, C.POINTER(p), C.POINTER(p)])
        bind(self.lib, "gst_mini_object_unref", None, [p])
        bind(self.lib, "gst_event_new_eos", p, [])
        bind(self.lib, "gst_element_send_event", C.c_int, [p, p])
        bind(self.obj, "g_object_unref", None, [p])
        bind(self.glib, "g_error_free", None, [p])
        bind(self.glib, "g_free", None, [p])
        self.lib.gst_init(None, None)

    def plugins_missing(self, audio: bool) -> list[str]:
        names = [
            "pipewiresrc",
            "compositor",
            "videoconvert",
            "videoscale",
            "videorate",
            "videocrop",
            "x264enc",
            "h264parse",
            "mp4mux",
            "pngenc",
            "filesink",
        ]
        if audio:
            names += ["pulsesrc", "audioconvert", "audioresample", "avenc_aac", "aacparse"]
        missing = []
        for name in names:
            factory = self.lib.gst_element_factory_find(name.encode())
            if factory:
                self.obj.g_object_unref(factory)
            else:
                missing.append(name)
        return missing

    def run(self, pipeline: str, seconds: float, source) -> tuple[int, str]:
        error = C.c_void_p()
        graph = self.lib.gst_parse_launch(pipeline.encode(), C.byref(error))
        if error.value:
            message = C.cast(error, C.POINTER(Error)).contents.message.decode("utf-8", "replace")
            self.glib.g_error_free(error)
            if graph:
                self.obj.g_object_unref(graph)
            return 1, f"Не собрать запись GStreamer: {message}. Запустите snapreel setup."
        if not graph:
            return 1, "GStreamer не создал запись — проверьте плагины командой snapreel doctor."
        bus = self.lib.gst_element_get_bus(graph)
        done = threading.Event()
        stopping = threading.Event()
        lock = threading.Lock()

        def stop():
            with lock:
                if not stopping.is_set() and not done.is_set():
                    stopping.set()
                    self.lib.gst_element_send_event(graph, self.lib.gst_event_new_eos())

        def read_stop():
            try:
                while not done.is_set():
                    if os.read(source.fileno(), 1) in (b"q", b""):
                        stop()
                        return
            except (OSError, ValueError):
                stop()

        try:
            if self.lib.gst_element_set_state(graph, 4) == 0:
                return 1, "Не начать захват PipeWire — повторите разрешение в системном диалоге."
            threading.Thread(target=read_stop, daemon=True).start()
            deadline = time.monotonic() + seconds
            eos_deadline = None
            while True:
                message = self.lib.gst_bus_timed_pop_filtered(bus, 100_000_000, 3)
                if message:
                    try:
                        if C.cast(message, C.POINTER(Message)).contents.type == 1:
                            return 0, ""
                        error, debug = C.c_void_p(), C.c_void_p()
                        self.lib.gst_message_parse_error(message, C.byref(error), C.byref(debug))
                        try:
                            reason = C.cast(error, C.POINTER(Error)).contents.message.decode(
                                "utf-8", "replace"
                            )
                        finally:
                            if error:
                                self.glib.g_error_free(error)
                            if debug:
                                self.glib.g_free(debug)
                        return 1, f"Захват PipeWire сорвался: {reason}. Повторите запись."
                    finally:
                        self.lib.gst_mini_object_unref(message)
                now = time.monotonic()
                if now >= deadline:
                    stop()
                if stopping.is_set() and eos_deadline is None:
                    eos_deadline = now + 15
                if eos_deadline is not None and now >= eos_deadline:
                    return 1, "GStreamer не дописал контейнер за 15 секунд — повторите запись."
        finally:
            with lock:
                done.set()
                self.lib.gst_element_set_state(graph, 1)
            self.obj.g_object_unref(bus)
            self.obj.g_object_unref(graph)

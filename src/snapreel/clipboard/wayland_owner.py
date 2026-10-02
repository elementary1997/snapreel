"""Wayland data-control объявляет только файловые типы, без text/plain.

Владелец живёт отдельным процессом, чтобы пережить разовую команду record.
Он читает только свой payload и не читает прежнее содержимое буфера.
"""

from __future__ import annotations

import ctypes as C
import json
import os
import select
import subprocess
from pathlib import Path

from .. import autostart, proc
from .posix import ClipboardError, file_uri


class Interface(C.Structure):
    pass


class Message(C.Structure):
    _fields_ = [
        ("name", C.c_char_p),
        ("signature", C.c_char_p),
        ("types", C.POINTER(C.POINTER(Interface))),
    ]


Interface._fields_ = [
    ("name", C.c_char_p),
    ("version", C.c_int),
    ("method_count", C.c_int),
    ("methods", C.POINTER(Message)),
    ("event_count", C.c_int),
    ("events", C.POINTER(Message)),
]


class Owner:
    def __init__(self, paths):
        self.payloads = {
            "text/uri-list": ("\r\n".join(file_uri(p) for p in paths) + "\r\n").encode(),
            "x-special/gnome-copied-files": (
                "copy\n" + "\n".join(file_uri(p) for p in paths)
            ).encode(),
        }
        self.keep = []
        self.cancelled = False
        self.globals = {}
        self.display = None
        try:
            self.lib = C.CDLL("libwayland-client.so.0")
        except OSError as exc:
            raise ClipboardError("Нет libwayland-client — установите libwayland-client0.") from exc
        from ..portal import Gio

        bind = Gio._bind
        p, u, i = C.c_void_p, C.c_uint32, C.c_int
        bind(self.lib, "wl_display_connect", p, [C.c_char_p])
        bind(self.lib, "wl_display_disconnect", None, [p])
        bind(self.lib, "wl_display_roundtrip", i, [p])
        bind(self.lib, "wl_display_dispatch", i, [p])
        bind(self.lib, "wl_display_flush", i, [p])
        bind(self.lib, "wl_proxy_add_listener", i, [p, C.POINTER(p), p])
        bind(self.lib, "wl_proxy_marshal_flags", p, [p, u, C.POINTER(Interface), u, u])
        self.registry_interface = Interface.in_dll(self.lib, "wl_registry_interface")
        self.seat_interface = Interface.in_dll(self.lib, "wl_seat_interface")
        self.manager = Interface()
        self.source = Interface()
        self.device = Interface()
        self.offer = Interface()
        self._define(
            self.manager,
            "zwlr_data_control_manager_v1",
            2,
            [
                ("create_data_source", "n", [self.source]),
                ("get_data_device", "no", [self.device, self.seat_interface]),
                ("destroy", "", []),
            ],
            [],
        )
        self._define(
            self.source,
            "zwlr_data_control_source_v1",
            1,
            [("offer", "s", [None]), ("destroy", "", [])],
            [("send", "sh", [None, None]), ("cancelled", "", [])],
        )
        self._define(
            self.device,
            "zwlr_data_control_device_v1",
            2,
            [
                ("set_selection", "?o", [self.source]),
                ("destroy", "", []),
                ("set_primary_selection", "2?o", [self.source]),
            ],
            [
                ("data_offer", "n", [self.offer]),
                ("selection", "?o", [self.offer]),
                ("finished", "", []),
                ("primary_selection", "2?o", [self.offer]),
            ],
        )
        self._define(
            self.offer,
            "zwlr_data_control_offer_v1",
            1,
            [("receive", "sh", [None, None]), ("destroy", "", [])],
            [("offer", "s", [None])],
        )

    def _define(self, interface, name, version, methods, events):
        def messages(entries):
            values = []
            for member, signature, types in entries:
                array = (C.POINTER(Interface) * len(types))(
                    *(C.pointer(value) if value is not None else None for value in types)
                )
                self.keep.append(array)
                values.append(Message(member.encode(), signature.encode(), array))
            array = (Message * len(values))(*values)
            self.keep.append(array)
            return array

        interface.name = name.encode()
        interface.version = version
        interface.method_count = len(methods)
        interface.methods = messages(methods)
        interface.event_count = len(events)
        interface.events = messages(events)

    def _listen(self, proxy, specifications):
        callbacks = []
        for types, function in specifications:
            callback = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, *types)(function)
            callbacks.append(callback)
        array = (C.c_void_p * len(callbacks))(*(C.cast(cb, C.c_void_p) for cb in callbacks))
        self.keep.extend([callbacks, array])
        if self.lib.wl_proxy_add_listener(proxy, array, None) != 0:
            raise ClipboardError("Не подключить буфер Wayland — повторите запись.")

    def _marshal(self, proxy, opcode, interface, version, *arguments):
        return self.lib.wl_proxy_marshal_flags(
            proxy,
            opcode,
            C.pointer(interface) if interface is not None else None,
            version,
            0,
            *arguments,
        )

    def start(self):
        self.display = self.lib.wl_display_connect(None)
        if not self.display:
            raise ClipboardError(
                "Нет доступа к Wayland — запускайте snapreel в графической сессии."
            )
        p, u, s = C.c_void_p, C.c_uint32, C.c_char_p
        registry = self._marshal(self.display, 1, self.registry_interface, 1, p())

        def global_(data, proxy, name, interface, version):
            self.globals[interface.decode()] = (name, version)

        self._listen(registry, [([u, s, u], global_), ([u], lambda *args: None)])
        self.lib.wl_display_roundtrip(self.display)
        if self.manager.name.decode() not in self.globals or "wl_seat" not in self.globals:
            raise ClipboardError(
                "Композитор не предоставляет data-control — используйте wl-clipboard."
            )
        name, version = self.globals[self.manager.name.decode()]
        manager = self._marshal(
            registry,
            0,
            self.manager,
            min(version, 2),
            u(name),
            s(self.manager.name),
            u(min(version, 2)),
            p(),
        )
        name, _ = self.globals["wl_seat"]
        seat = self._marshal(registry, 0, self.seat_interface, 1, u(name), s(b"wl_seat"), u(1), p())
        self._listen(seat, [([u], lambda *args: None)])
        source = self._marshal(manager, 0, self.source, 1, p())

        def send(data, proxy, mime, fd):
            try:
                payload = self.payloads.get(mime.decode(), b"")
                while payload:
                    payload = payload[os.write(fd, payload) :]
            except (OSError, UnicodeError):
                pass
            finally:
                os.close(fd)

        def cancelled(*args):
            self.cancelled = True

        self._listen(source, [([s, C.c_int], send), ([], cancelled)])
        for mime in self.payloads:
            self._marshal(source, 0, None, 1, s(mime.encode()))
        device = self._marshal(manager, 1, self.device, min(version, 2), p(), p(seat))

        def offered(data, proxy, offer):
            self._listen(offer, [([s], lambda *args: None)])

        self._listen(
            device,
            [([p], offered), ([p], lambda *args: None), ([], cancelled), ([p], lambda *args: None)],
        )
        self._marshal(device, 0, None, min(version, 2), p(source))
        if self.lib.wl_display_roundtrip(self.display) < 0:
            raise ClipboardError("Wayland отказал в доступе к буферу — проверьте wl-clipboard.")

    def run(self):
        while not self.cancelled:
            if self.lib.wl_display_dispatch(self.display) < 0:
                return

    def close(self):
        if self.display:
            self.lib.wl_display_disconnect(self.display)
            self.display = None


def copy_files(paths: list[Path]):
    process = proc.popen(
        autostart.argv_for("--clipboard-worker"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        process.stdin.write(json.dumps([str(path.resolve()) for path in paths]).encode() + b"\n")
        process.stdin.close()
        if not select.select([process.stdout], [], [], 5)[0]:
            raise ClipboardError("Буфер Wayland не ответил за 5 секунд — повторите запись.")
        response = process.stdout.readline(4096).decode("utf-8", "replace").strip()
        if response != "READY":
            raise ClipboardError(response or "Не запустить владельца буфера Wayland.")
    except (OSError, ClipboardError):
        process.terminate()
        process.wait(timeout=3)
        raise
    finally:
        process.stdout.close()

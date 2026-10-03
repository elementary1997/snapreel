"""Нативный транспорт Wayland для буфера и подсветки, без Python-зависимостей."""

from __future__ import annotations

import ctypes as C


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


class NativeClient:
    def __init__(self, error_type):
        self.error_type = error_type
        self.keep = []
        self.globals = {}
        self.display = None
        try:
            self.lib = C.CDLL("libwayland-client.so.0")
        except OSError as exc:
            raise error_type("Нет libwayland-client — установите libwayland-client0.") from exc
        from .portal import Gio

        bind = Gio._bind
        p, i = C.c_void_p, C.c_int
        bind(self.lib, "wl_display_connect", p, [C.c_char_p])
        bind(self.lib, "wl_display_disconnect", None, [p])
        bind(self.lib, "wl_display_roundtrip", i, [p])
        bind(self.lib, "wl_display_dispatch", i, [p])
        bind(self.lib, "wl_display_dispatch_pending", i, [p])
        bind(self.lib, "wl_display_get_fd", i, [p])
        bind(self.lib, "wl_display_flush", i, [p])
        bind(self.lib, "wl_proxy_add_listener", i, [p, C.POINTER(p), p])
        bind(
            self.lib,
            "wl_proxy_marshal_flags",
            p,
            [p, C.c_uint32, C.POINTER(Interface), C.c_uint32, C.c_uint32],
        )
        self.registry_interface = Interface.in_dll(self.lib, "wl_registry_interface")
        self.seat_interface = Interface.in_dll(self.lib, "wl_seat_interface")

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
            raise self.error_type("Не подключить Wayland — повторите запись.")

    def _marshal(self, proxy, opcode, interface, version, *arguments):
        return self.lib.wl_proxy_marshal_flags(
            proxy,
            opcode,
            C.pointer(interface) if interface is not None else None,
            version,
            0,
            *arguments,
        )

    def close(self):
        if self.display:
            self.lib.wl_display_disconnect(self.display)
            self.display = None
        self.keep.clear()

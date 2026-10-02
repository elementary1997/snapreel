"""Общий транспорт порталов: GIO и GVariant без Python-зависимостей."""

from __future__ import annotations

import ctypes as C
import time
import uuid

SERVICE = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
HELP = "Проверьте xdg-desktop-portal и портал вашего рабочего стола; войдите в сессию заново."


class PortalError(Exception):
    pass


class _Error(C.Structure):
    _fields_ = [("domain", C.c_uint32), ("code", C.c_int), ("message", C.c_char_p)]


class Gio:
    def __init__(self):
        try:
            self.lib = C.CDLL("libgio-2.0.so.0")
            self.glib = C.CDLL("libglib-2.0.so.0")
            self.object = C.CDLL("libgobject-2.0.so.0")
        except OSError as exc:
            raise PortalError(HELP) from exc
        p = C.c_void_p
        s = C.c_char_p
        u = C.c_uint
        self._bind(self.lib, "g_bus_get_sync", p, [C.c_int, p, C.POINTER(p)])
        self._bind(self.lib, "g_dbus_address_get_for_bus_sync", p, [C.c_int, p, C.POINTER(p)])
        self._bind(
            self.lib, "g_dbus_connection_new_for_address_sync", p, [s, C.c_int, p, p, C.POINTER(p)]
        )
        self._bind(self.lib, "g_dbus_connection_close_sync", C.c_int, [p, p, C.POINTER(p)])
        self._bind(self.glib, "g_free", None, [p])
        self._bind(self.lib, "g_dbus_connection_get_unique_name", s, [p])
        self._bind(
            self.lib,
            "g_dbus_connection_call_sync",
            p,
            [p, s, s, s, s, p, p, C.c_int, C.c_int, p, C.POINTER(p)],
        )
        self.callback_type = C.CFUNCTYPE(None, p, s, s, s, s, p, p)
        self._bind(
            self.lib,
            "g_dbus_connection_signal_subscribe",
            u,
            [p, s, s, s, s, s, C.c_int, self.callback_type, p, p],
        )
        self._bind(self.lib, "g_dbus_connection_signal_unsubscribe", None, [p, u])
        self._bind(self.glib, "g_variant_parse", p, [p, s, p, p, C.POINTER(p)])
        self._bind(self.glib, "g_variant_get_type_string", s, [p])
        self._bind(self.glib, "g_variant_get_string", s, [p, p])
        self._bind(self.glib, "g_variant_get_uint32", C.c_uint32, [p])
        self._bind(self.glib, "g_variant_get_uint64", C.c_uint64, [p])
        self._bind(self.glib, "g_variant_get_int32", C.c_int32, [p])
        self._bind(self.glib, "g_variant_get_handle", C.c_int32, [p])
        self._bind(
            self.lib,
            "g_dbus_connection_call_with_unix_fd_list_sync",
            p,
            [p, s, s, s, s, p, p, C.c_int, C.c_int, p, C.POINTER(p), p, C.POINTER(p)],
        )
        self._bind(self.lib, "g_unix_fd_list_get", C.c_int, [p, C.c_int, C.POINTER(p)])
        self._bind(self.glib, "g_variant_get_variant", p, [p])
        self._bind(self.glib, "g_variant_n_children", C.c_size_t, [p])
        self._bind(self.glib, "g_variant_get_child_value", p, [p, C.c_size_t])
        self._bind(self.glib, "g_variant_unref", None, [p])
        self._bind(self.glib, "g_error_free", None, [p])
        self._bind(self.glib, "g_main_context_new", p, [])
        self._bind(self.glib, "g_main_context_push_thread_default", None, [p])
        self._bind(self.glib, "g_main_context_pop_thread_default", None, [p])
        self._bind(self.glib, "g_main_context_iteration", C.c_int, [p, C.c_int])
        self._bind(self.glib, "g_main_context_unref", None, [p])
        self._bind(self.object, "g_object_unref", None, [p])
        address = self._checked(self.lib.g_dbus_address_get_for_bus_sync, 2, None)
        try:
            self.connection = self._checked(
                self.lib.g_dbus_connection_new_for_address_sync,
                C.cast(address, C.c_char_p),
                9,
                None,
                None,
            )
        finally:
            self.glib.g_free(address)
        self.subscriptions: dict[int, object] = {}

    def register_app(self):
        """Портал не должен записывать наши сочетания на имя терминала или браузера."""
        from .autostart import PORTAL_APP_ID, ensure_portal_entry

        ensure_portal_entry()
        try:
            self.call(
                PATH,
                "org.freedesktop.host.portal.Registry",
                "Register",
                f"('{PORTAL_APP_ID}', @a{{sv}} {{}})",
            )
        except PortalError as exc:
            if "UnknownMethod" not in str(exc) and "UnknownInterface" not in str(exc):
                raise

    @staticmethod
    def _bind(lib, name, result, args):
        function = getattr(lib, name)
        function.restype = result
        function.argtypes = args

    def _checked(self, function, *args):
        error = C.c_void_p()
        result = function(*args, C.byref(error))
        if error.value:
            message = C.cast(error, C.POINTER(_Error)).contents.message.decode("utf-8", "replace")
            self.glib.g_error_free(error)
            raise PortalError(f"Не подключиться к порталу: {message}. {HELP}")
        if not result:
            raise PortalError(HELP)
        return result

    def unpack(self, value):
        kind = self.glib.g_variant_get_type_string(value).decode()
        if kind in ("s", "o", "g"):
            return self.glib.g_variant_get_string(value, None).decode()
        if kind == "u":
            return self.glib.g_variant_get_uint32(value)
        if kind == "t":
            return self.glib.g_variant_get_uint64(value)
        if kind == "i":
            return self.glib.g_variant_get_int32(value)
        if kind == "h":
            return self.glib.g_variant_get_handle(value)
        if kind == "v":
            child = self.glib.g_variant_get_variant(value)
            try:
                return self.unpack(child)
            finally:
                self.glib.g_variant_unref(child)
        if kind.startswith(("a", "(", "{")):
            children = []
            for index in range(self.glib.g_variant_n_children(value)):
                child = self.glib.g_variant_get_child_value(value, index)
                try:
                    children.append(self.unpack(child))
                finally:
                    self.glib.g_variant_unref(child)
            return dict(children) if kind.startswith("a{") else children
        return None

    def call(self, path: str, interface: str, method: str, arguments: str = "()"):
        value = self._checked(self.glib.g_variant_parse, None, arguments.encode(), None, None)
        try:
            reply = self._checked(
                self.lib.g_dbus_connection_call_sync,
                self.connection,
                SERVICE.encode(),
                path.encode(),
                interface.encode(),
                method.encode(),
                value,
                None,
                0,
                5000,
                None,
            )
        finally:
            self.glib.g_variant_unref(value)
        try:
            return self.unpack(reply)
        finally:
            self.glib.g_variant_unref(reply)

    def open_remote(self, session: str) -> int:
        value = self._checked(
            self.glib.g_variant_parse,
            None,
            f"(objectpath '{session}', @a{{sv}} {{}})".encode(),
            None,
            None,
        )
        fds = C.c_void_p()
        reply = None
        try:
            reply = self._checked(
                self.lib.g_dbus_connection_call_with_unix_fd_list_sync,
                self.connection,
                SERVICE.encode(),
                PATH.encode(),
                b"org.freedesktop.portal.ScreenCast",
                b"OpenPipeWireRemote",
                value,
                None,
                0,
                5000,
                None,
                C.byref(fds),
                None,
            )
            return self._checked(self.lib.g_unix_fd_list_get, fds, self.unpack(reply)[0])
        finally:
            self.glib.g_variant_unref(value)
            if reply:
                self.glib.g_variant_unref(reply)
            if fds.value:
                self.object.g_object_unref(fds)

    def subscribe(self, path: str, interface: str, signal: str, handler):
        def received(connection, sender, object_path, iface, member, parameters, data):
            handler(self.unpack(parameters))

        callback = self.callback_type(received)
        token = self.lib.g_dbus_connection_signal_subscribe(
            self.connection,
            SERVICE.encode(),
            interface.encode(),
            signal.encode(),
            path.encode(),
            None,
            0,
            callback,
            None,
            None,
        )
        self.subscriptions[token] = callback
        return token

    def unsubscribe(self, token):
        self.lib.g_dbus_connection_signal_unsubscribe(self.connection, token)
        self.subscriptions.pop(token, None)

    def request(self, context, stopped, method, arguments, interface=INTERFACE):
        token = "snapreel_" + uuid.uuid4().hex
        sender = self.lib.g_dbus_connection_get_unique_name(self.connection).decode()[1:]
        path = f"/org/freedesktop/portal/desktop/request/{sender.replace('.', '_')}/{token}"
        response = []
        subscription = self.subscribe(path, REQUEST, "Response", response.append)
        # Подписываемся заранее: ответ может прийти до возврата метода.
        options = f"@a{{sv}} {{'handle_token': <'{token}'>}}"
        try:
            self.call(PATH, interface, method, arguments(options))
            deadline = time.monotonic() + 120
            while not response and not stopped.is_set() and time.monotonic() < deadline:
                self.glib.g_main_context_iteration(context, False)
                stopped.wait(0.01)
            if not response:
                self.call(path, REQUEST, "Close")
                raise PortalError("Портал не ответил. Повторите запрос разрешения.")
            code, results = response[0]
            if code:
                if interface == "org.freedesktop.portal.ScreenCast":
                    raise PortalError(
                        "Запись экрана не разрешена. Выберите монитор в системном диалоге."
                    )
                raise PortalError(
                    "Горячие клавиши не разрешены. Разрешите их в диалоге KDE Plasma."
                )
            return results
        finally:
            self.unsubscribe(subscription)

    def close(self):
        for token in list(self.subscriptions):
            self.unsubscribe(token)
        try:
            self._checked(self.lib.g_dbus_connection_close_sync, self.connection, None)
        except PortalError:
            pass
        self.object.g_object_unref(self.connection)

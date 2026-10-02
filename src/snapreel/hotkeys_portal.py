"""Wayland отдаёт действия через портал, а не через перехват клавиатуры.

GIO уже входит в Linux-окружения с порталами. ctypes позволяет использовать
его без новой Python-зависимости; отдельный GLib-контекст не трогает Qt.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable

from .autostart import HotkeySetupError, parse_hotkey
from .config import Config
from .portal import Gio as _Gio
from .portal import PortalError

SERVICE = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
HELP = (
    "В Wayland нужен портал GlobalShortcuts. В KDE Plasma установите "
    "xdg-desktop-portal и xdg-desktop-portal-kde и войдите в сессию заново. "
    "Если портал не поддерживается, назначьте snapreel record и "
    "snapreel record --gif средствами рабочего стола."
)


def trigger(spec: str) -> str:
    """Портал принимает синтаксис XDG, а конфиг хранит синтаксис pynput."""
    try:
        modifiers, key = parse_hotkey(spec)
    except HotkeySetupError as exc:
        raise PortalError(f"Исправьте горячую клавишу в настройках: {exc}") from exc
    names = {"ctrl": "CTRL", "shift": "SHIFT", "alt": "ALT", "super": "LOGO"}
    keys = {
        "space": "space",
        "enter": "Return",
        "esc": "Escape",
        "escape": "Escape",
        "pageup": "Prior",
        "pagedown": "Next",
        "printscreen": "Print",
        "print": "Print",
    }
    symbol = keys.get(
        key, key if len(key) == 1 else key.upper() if key.startswith("f") else key.title()
    )
    return "+".join([*(names[m] for m in modifiers), symbol])


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def available() -> bool:
    """Диагностика спрашивает интерфейс, не открывая диалог разрешения."""
    gio = None
    try:
        gio = _Gio()
        xml = gio.call(PATH, "org.freedesktop.DBus.Introspectable", "Introspect")[0]
        return f'interface name="{INTERFACE}"' in xml
    except (PortalError, OSError):
        return False
    finally:
        if gio is not None:
            gio.close()


class Listener:
    def __init__(self, config: Config, handler: Callable[[bool], None]):
        self.config = config
        self.handler = handler
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.error: PortalError | None = None
        self.session = ""
        self.thread: threading.Thread | None = None

    def start(self):
        # Проверяем конфиг до создания потока, чтобы ошибки не терялись в нём.
        self.triggers = (trigger(self.config.hotkey_mp4), trigger(self.config.hotkey_gif))
        self.thread = threading.Thread(target=self._run, name="snapreel-shortcuts", daemon=True)
        self.thread.start()
        # В трее Qt уже поднят: продолжаем обслуживать его, пока KDE спрашивает
        # разрешение. В CLI достаточно дождаться того же результата без Qt.
        from importlib import util

        app = None
        if util.find_spec("PySide6") is not None:
            try:
                from PySide6.QtCore import QCoreApplication, QEventLoop

                app = QCoreApplication.instance()
            except ImportError:
                pass  # слушатель CLI не зависит от исправности Qt
        try:
            while not self.ready.wait(0.01):
                if app is not None:
                    app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        except BaseException:
            self.stop()
            raise
        if self.error is not None:
            self.stop()
            raise self.error

    def _activated(self, values):
        session, shortcut, *_ = values
        if not self.stopped.is_set() and session == self.session and shortcut in ("mp4", "gif"):
            self.handler(shortcut == "gif")

    def _run(self):
        gio = None
        context = None
        try:
            gio = _Gio()
            gio.register_app()
            context = gio.glib.g_main_context_new()
            gio.glib.g_main_context_push_thread_default(context)
            token = "snapreel_" + uuid.uuid4().hex
            result = gio.request(
                context,
                self.stopped,
                "CreateSession",
                lambda options: f"({options[:-1]}, 'session_handle_token': <'{token}'>}},)",
            )
            self.session = result["session_handle"]
            gio.subscribe(PATH, INTERFACE, "Activated", self._activated)
            shortcuts = ", ".join(
                f"({_string(identifier)}, {{'description': <{_string(description)}>, "
                f"'preferred_trigger': <{_string(preferred)}>}})"
                for identifier, description, preferred in (
                    ("mp4", "Записать MP4", self.triggers[0]),
                    ("gif", "Записать GIF", self.triggers[1]),
                )
            )
            bound = gio.request(
                context,
                self.stopped,
                "BindShortcuts",
                lambda options: (
                    f"(objectpath {_string(self.session)}, "
                    f"@a(sa{{sv}}) [{shortcuts}], '', {options})"
                ),
            )
            ids = {item[0] for item in bound.get("shortcuts", [])}
            if ids != {"mp4", "gif"}:
                raise PortalError(
                    "Назначены не обе комбинации. Разрешите MP4 и GIF в настройках KDE."
                )
            self.ready.set()
            while not self.stopped.wait(0.01):
                gio.glib.g_main_context_iteration(context, False)
        except Exception as exc:
            self.error = (
                exc
                if isinstance(exc, PortalError)
                else PortalError(f"Не назначить комбинации: {exc}. {HELP}")
            )
        finally:
            if gio is not None:
                if self.session:
                    try:
                        gio.call(self.session, SESSION, "Close")
                    except PortalError:
                        pass
                gio.close()
                if context is not None:
                    gio.glib.g_main_context_pop_thread_default(context)
                    gio.glib.g_main_context_unref(context)
            self.ready.set()

    def stop(self):
        self.stopped.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=6)

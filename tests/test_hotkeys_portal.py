"""Портал подменяется: обычный прогон не спрашивает разрешение у рабочего стола."""

import threading

import pytest

from snapreel import hotkeys
from snapreel import hotkeys_portal as portal
from snapreel.config import Config
from snapreel.platform_info import Environment, Platform

WAYLAND = Environment(Platform.LINUX_WAYLAND, False)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("<ctrl>+<shift>+<alt>+r", "CTRL+SHIFT+ALT+r"),
        ("<super>+<enter>", "LOGO+Return"),
        ("<ctrl>+<pageup>", "CTRL+Prior"),
        ("<alt>+<f12>", "ALT+F12"),
    ],
)
def test_preferred_shortcuts_use_xdg_key_names(spec, expected):
    assert portal.trigger(spec) == expected


def test_a_supported_wayland_session_uses_the_portal(monkeypatch):
    monkeypatch.setattr(portal, "available", lambda: True)
    assert hotkeys.why_silent(WAYLAND) is None
    assert hotkeys.mechanism(WAYLAND) == "портал GlobalShortcuts"


def test_doctor_never_requests_permission_from_the_portal(monkeypatch):
    monkeypatch.setattr(portal, "available", lambda: True)
    monkeypatch.setattr(portal.Listener, "start", lambda self: pytest.fail("открыли диалог"))
    assert not hotkeys.can_probe(WAYLAND)
    assert hotkeys.probe(Config(), WAYLAND) is None


def test_portal_failure_becomes_a_hotkey_error(monkeypatch):
    def refuse(self):
        raise portal.PortalError("Разрешите комбинации в KDE")

    monkeypatch.setattr(portal.Listener, "start", refuse)
    with pytest.raises(hotkeys.HotkeyError, match="Разрешите"):
        hotkeys.listen(Config(), lambda gif: None, WAYLAND)


@pytest.fixture
def service(monkeypatch):
    class Glib:
        def g_main_context_new(self):
            return object()

        def g_main_context_push_thread_default(self, context):
            pass

        def g_main_context_pop_thread_default(self, context):
            calls.append("popped")

        def g_main_context_unref(self, context):
            calls.append("released")

        def g_main_context_iteration(self, context, blocking):
            pass

    calls = []

    class Gio:
        glib = Glib()
        response = ("mp4", "gif")
        failure = None

        def register_app(self):
            pass

        def request(self, context, stopped, method, arguments):
            calls.append((method, arguments("@a{sv} {'handle_token': <'test'>}")))
            if method == "CreateSession":
                return {"session_handle": "/session/snapreel"}
            if self.failure:
                raise self.failure
            return {"shortcuts": [[identifier, {}] for identifier in self.response]}

        def subscribe(self, path, interface, signal, handler):
            calls.append(signal)

        def call(self, path, interface, method):
            calls.append((path, method))

        def close(self):
            calls.append("disconnected")

    monkeypatch.setattr(portal, "_Gio", Gio)
    return Gio, calls


def test_both_actions_are_bound_and_dispatched_only_for_our_session(service):
    _, calls = service
    received = []
    threads = []
    listener = portal.Listener(
        Config(), lambda gif: (received.append(gif), threads.append(threading.get_ident()))
    )
    listener.start()
    try:
        listener._activated(["/other/session", "mp4", 0, {}])
        listener._activated([listener.session, "unknown", 0, {}])
        listener._activated([listener.session, "mp4", 0, {}])
        listener._activated([listener.session, "gif", 0, {}])
        assert received == [False, True]
        binding = next(
            item[1] for item in calls if isinstance(item, tuple) and item[0] == "BindShortcuts"
        )
        assert '"mp4"' in binding and '"gif"' in binding
        assert "CTRL+SHIFT+ALT+r" in binding
    finally:
        listener.stop()
    listener._activated([listener.session, "mp4", 0, {}])
    assert received == [False, True]
    assert ("/session/snapreel", "Close") in calls
    assert calls[-3:] == ["disconnected", "popped", "released"]
    assert not listener.thread.is_alive()


@pytest.mark.parametrize("ids", [[], ["mp4"], ["gif"]])
def test_partial_permission_closes_the_session_instead_of_claiming_success(service, ids):
    gio, calls = service
    gio.response = ids
    listener = portal.Listener(Config(), lambda gif: None)
    with pytest.raises(portal.PortalError, match="не обе"):
        listener.start()
    assert ("/session/snapreel", "Close") in calls
    assert not listener.thread.is_alive()


def test_cancelled_permission_releases_the_session(service):
    gio, calls = service
    gio.failure = portal.PortalError("Не разрешены")
    listener = portal.Listener(Config(), lambda gif: None)
    with pytest.raises(portal.PortalError, match="Не разрешены"):
        listener.start()
    assert ("/session/snapreel", "Close") in calls
    assert "disconnected" in calls


@pytest.mark.parametrize("code", [0, 1, 2])
def test_a_response_arriving_before_the_method_returns_is_not_lost(code):
    calls = []

    class Lib:
        def g_dbus_connection_get_unique_name(self, connection):
            return b":1.42"

    class Gio(portal._Gio):
        def __init__(self):
            self.connection = object()
            self.lib = Lib()

        def subscribe(self, path, interface, signal, handler):
            assert path.startswith("/org/freedesktop/portal/desktop/request/1_42/")
            self.deliver = handler
            calls.append("subscribed")
            return 7

        def call(self, path, interface, method, arguments):
            calls.append("called")
            self.deliver([code, {"session_handle": "/session/test"}])

        def unsubscribe(self, token):
            assert token == 7
            calls.append("unsubscribed")

    gio = Gio()
    if code:
        with pytest.raises(portal.PortalError, match="не разрешены"):
            gio.request(None, threading.Event(), "CreateSession", lambda options: f"({options},)")
    else:
        result = gio.request(
            None, threading.Event(), "CreateSession", lambda options: f"({options},)"
        )
        assert result["session_handle"] == "/session/test"
    assert calls == ["subscribed", "called", "unsubscribed"]

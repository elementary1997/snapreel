"""Кто ловит комбинации в этой сессии — и что говорят человеку, когда никто.

Проверяется без экрана: обе развилки (Wayland, X-сервер без RECORD) задаются
подменой, потому что настоящий сервер без RECORD в обычном прогоне не поднять.
Сам перехват через `XGrabKey` проверяется на живом X — `tests/test_hotkeys_gui.py`.
"""

from __future__ import annotations

import pytest

from snapreel import hotkeys, hotkeys_x11
from snapreel.config import Config
from snapreel.platform_info import Environment, Platform

WAYLAND = Environment(platform=Platform.LINUX_WAYLAND, is_wsl=False)
WSL = Environment(platform=Platform.LINUX_WAYLAND, is_wsl=True)
X11 = Environment(platform=Platform.LINUX_X11, is_wsl=False)
WINDOWS = Environment(platform=Platform.WINDOWS, is_wsl=False)


@pytest.fixture
def x_server(monkeypatch):
    """Живой X-сервер, у которого можно включать и выключать RECORD."""

    def state(record: bool, reachable: bool = True):
        monkeypatch.setattr(hotkeys_x11, "has_record", lambda: record if reachable else None)
        monkeypatch.setattr(hotkeys_x11, "available", lambda: reachable)

    return state


def test_a_wayland_session_says_why_the_hotkeys_are_silent():
    """Композитор глобальные клавиши приложению не отдаёт — обойти нечем."""
    refusal = hotkeys.why_silent(WAYLAND)

    assert refusal is not None
    assert "Wayland" in refusal
    assert "средствами рабочего стола" in refusal


def test_wsl_listens_through_its_x_server(x_server):
    """В WSLg выставлен WAYLAND_DISPLAY, но клавиши идут через XWayland.

    Отказ по одному имени переменной оставлял там человека вовсе без
    комбинаций, хотя перехват работает.
    """
    x_server(record=True)

    assert hotkeys.why_silent(WSL) is None


def test_a_server_without_record_is_grabbed_directly(x_server):
    """Без RECORD поток pynput умирает молча — ловим клавиши сами."""
    x_server(record=False)

    assert hotkeys.mechanism(X11) == "перехват X11"
    assert hotkeys.why_silent(X11) is None


def test_a_server_with_record_keeps_pynput(x_server):
    """Проверенный путь без нужды не трогаем."""
    x_server(record=True)

    assert hotkeys.mechanism(X11) == "pynput"


def test_an_unanswered_server_counts_as_having_record(x_server):
    """«Не знаем» — не «нет»: pynput провереннее, и гадать в его пользу дешевле."""
    x_server(record=False, reachable=False)

    assert hotkeys.mechanism(X11) == "pynput"


def test_windows_never_grabs_x_keys():
    assert hotkeys.mechanism(WINDOWS) == "pynput"
    assert hotkeys.why_silent(WINDOWS) is None


def test_the_grab_listener_takes_both_combinations(monkeypatch, x_server):
    x_server(record=False)
    seen: dict = {}

    class FakeListener:
        def __init__(self, bindings):
            seen["bindings"] = bindings

        def start(self):
            seen["started"] = True

    monkeypatch.setattr(hotkeys_x11, "Listener", FakeListener)
    config = Config()

    hotkeys.listen(config, lambda as_gif: None, X11)

    assert set(seen["bindings"]) == {config.hotkey_mp4, config.hotkey_gif}
    assert seen["started"]


def test_a_combination_taken_by_someone_else_is_explained(monkeypatch, x_server):
    """Занятая комбинация — обычное дело: её мог перехватить рабочий стол."""
    x_server(record=False)

    class Busy:
        def __init__(self, bindings):
            pass

        def start(self):
            raise hotkeys_x11.GrabError("комбинацию уже занял кто-то другой")

    monkeypatch.setattr(hotkeys_x11, "Listener", Busy)

    with pytest.raises(hotkeys.HotkeyError) as failure:
        hotkeys.listen(Config(), lambda as_gif: None, X11)

    assert "уже занял" in str(failure.value)


def test_a_refused_binding_is_reported_by_the_probe(monkeypatch, x_server):
    """Отказ виден только по итогу привязки: заранее его знать неоткуда."""
    x_server(record=False)

    class Busy:
        def __init__(self, bindings):
            pass

        def start(self):
            raise hotkeys_x11.GrabError("комбинацию уже кто-то держит")

    monkeypatch.setattr(hotkeys_x11, "Listener", Busy)

    assert hotkeys.why_silent(X11) is None  # заранее известных причин нет
    assert "уже кто-то держит" in hotkeys.probe(Config(), X11)


def test_a_working_binding_leaves_the_probe_silent(monkeypatch, x_server):
    x_server(record=False)
    stopped = []

    class Fine:
        def __init__(self, bindings):
            pass

        def start(self):
            pass

        def stop(self):
            stopped.append("отпустили")

    monkeypatch.setattr(hotkeys_x11, "Listener", Fine)

    assert hotkeys.probe(Config(), X11) is None
    assert stopped, "проба обязана отпускать комбинации за собой"


def test_the_probe_repeats_a_reason_it_already_knows():
    """В Wayland пробовать нечего — ответ известен заранее."""
    assert "Wayland" in hotkeys.probe(Config(), WAYLAND)


def test_the_locks_do_not_break_a_combination():
    """Num Lock и Caps Lock живут в тех же битах, что и модификаторы.

    Без перехвата всех их сочетаний хоткей переставал бы работать от одного
    нажатия Num Lock — и объяснить это человеку было бы нечем.
    """
    pytest.importorskip("Xlib")
    from Xlib import X

    masks = hotkeys_x11._slop_masks()

    assert 0 in masks
    assert X.LockMask in masks
    assert X.Mod2Mask in masks
    assert X.LockMask | X.Mod2Mask in masks

"""Перехват комбинаций на живом X-сервере: ловится ли нажатие на самом деле.

Маркер `gui`, потому что нужен настоящий сервер: `xvfb-run pytest -m gui`.
Слушателя pynput так не проверить — он читает поток RECORD, куда события от
XTEST не попадают, — а вот `XGrabKey` синтетические нажатия видит, и это
единственный способ убедиться, что комбинация действительно ловится, не
нажимая её руками.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time

import pytest

pytestmark = pytest.mark.gui


@pytest.fixture
def listener_class():
    try:
        from snapreel.hotkeys_x11 import Listener
    except Exception as exc:  # без python-xlib и без дисплея проверять нечего
        pytest.skip(f"перехват X11 недоступен: {exc}")
    return Listener


def _press(combination: str) -> None:
    if not shutil.which("xdotool"):
        pytest.skip("нужен xdotool, чтобы послать нажатие")
    subprocess.run(["xdotool", "key", combination], check=True, timeout=10)


def test_the_grabbed_combination_reaches_us(listener_class):
    caught = threading.Event()
    listener = listener_class({"<ctrl>+<shift>+<alt>+r": caught.set})
    listener.start()
    try:
        time.sleep(0.3)  # серверу нужно принять захват
        _press("ctrl+shift+alt+r")

        assert caught.wait(5), "нажатие до нас не дошло"
    finally:
        listener.stop()


def test_other_keys_do_not_reach_us(listener_class):
    """Перехват берёт только заявленное — чужой ввод snapreel не видит."""
    seen: list[str] = []
    listener = listener_class({"<ctrl>+<shift>+<alt>+r": lambda: seen.append("наша")})
    listener.start()
    try:
        time.sleep(0.3)
        _press("a")
        _press("ctrl+shift+alt+g")
        time.sleep(0.5)

        assert seen == []
    finally:
        listener.stop()


def test_a_stopped_listener_lets_the_combination_go(listener_class):
    seen: list[str] = []
    listener = listener_class({"<ctrl>+<shift>+<alt>+r": lambda: seen.append("наша")})
    listener.start()
    time.sleep(0.3)
    listener.stop()

    assert not listener.is_alive()
    _press("ctrl+shift+alt+r")
    time.sleep(0.5)

    assert seen == []

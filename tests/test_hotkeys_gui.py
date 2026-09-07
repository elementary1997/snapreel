"""Перехват комбинаций на живом X-сервере: ловится ли нажатие на самом деле.

Маркер `gui`, потому что нужен настоящий сервер и `xdotool`, которым сюда
шлются нажатия: `xvfb-run pytest -m gui`.
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


@pytest.fixture
def press():
    """Посылает нажатие; без xdotool пропускает тест целиком, до начала.

    Пропуск именно здесь, а не внутри теста: `pytest.skip` бросает
    исключение, и брошенное из `finally` оно съедает всё, что за ним, — в
    прошлой версии так терялось снятие захвата, и следующие тесты падали с
    «комбинацию уже кто-то держит». Проверять снаружи дешевле, чем помнить
    об этом в каждом `finally`.
    """
    if not shutil.which("xdotool"):
        pytest.skip("нужен xdotool, чтобы послать нажатие")

    def send(combination: str) -> None:
        subprocess.run(["xdotool", "key", combination], check=True, timeout=10)

    return send


def test_the_grabbed_combination_reaches_us(listener_class, press):
    caught = threading.Event()
    listener = listener_class({"<ctrl>+<shift>+<alt>+r": caught.set})
    listener.start()
    try:
        time.sleep(0.3)  # серверу нужно принять захват
        press("ctrl+shift+alt+r")

        assert caught.wait(5), "нажатие до нас не дошло"
    finally:
        listener.stop()


def test_other_keys_do_not_reach_us(listener_class, press):
    """Перехват берёт только заявленное — чужой ввод snapreel не видит."""
    seen: list[str] = []
    listener = listener_class({"<ctrl>+<shift>+<alt>+r": lambda: seen.append("наша")})
    listener.start()
    try:
        time.sleep(0.3)
        press("a")
        press("ctrl+shift+alt+g")
        time.sleep(0.5)

        assert seen == []
    finally:
        listener.stop()


def test_num_lock_does_not_eat_the_combination(listener_class, press):
    """Замки клавиатуры живут в тех же битах, что и модификаторы.

    Без снятия замков нажатие с включённым Num Lock не совпадало с
    перехваченным — и при этом пропадало: до активного окна оно тоже не
    доходит, комбинацию-то мы забрали.
    """
    caught = threading.Event()
    listener = listener_class({"<ctrl>+<shift>+<alt>+r": caught.set})
    listener.start()
    try:
        time.sleep(0.3)
        press("Num_Lock")  # включили
        time.sleep(0.2)
        press("ctrl+shift+alt+r")

        assert caught.wait(5), "с включённым Num Lock комбинация не дошла"
    finally:
        listener.stop()  # первым: за ним в этом блоке может не выполниться ничего
        press("Num_Lock")  # вернули как было


def test_the_combination_can_be_taken_again_right_after_it_was_dropped(listener_class, press):
    """Так трей перевешивает комбинации после правки конфига — сразу за снятием.

    Сервер отпускает прежний захват не в тот же миг, и первая попытка
    натыкается на него же: без повторов это отказ «комбинацию уже кто-то
    держит» на ровном месте.
    """
    first = listener_class({"<ctrl>+<shift>+<alt>+r": lambda: None})
    first.start()
    first.stop()

    caught = threading.Event()
    second = listener_class({"<ctrl>+<shift>+<alt>+r": caught.set})
    second.start()  # без пауз, как это делает `TrayApp.reload`
    try:
        time.sleep(0.3)
        press("ctrl+shift+alt+r")

        assert caught.wait(5), "перевешенная комбинация не сработала"
    finally:
        second.stop()


def test_a_combination_taken_for_a_moment_is_waited_out(listener_class, press):
    """Одной попытки мало: сосед мог взять комбинацию на мгновение.

    Так это и выглядит при перевешивании — прежний захват ещё у сервера, а
    новый уже просят. Здесь роль прежнего играет отдельный слушатель,
    который отпускает комбинацию через мгновение после нашей попытки.
    """
    rival = listener_class({"<ctrl>+<shift>+<alt>+r": lambda: None})
    rival.start()
    threading.Timer(0.2, rival.stop).start()

    caught = threading.Event()
    ours = listener_class({"<ctrl>+<shift>+<alt>+r": caught.set})
    ours.start()  # первая попытка упрётся в соседа, следующая пройдёт
    try:
        time.sleep(0.3)
        press("ctrl+shift+alt+r")

        assert caught.wait(5)
    finally:
        ours.stop()


def test_a_key_that_is_not_on_the_layout_closes_the_connection(listener_class):
    """Отказ разбора — не повод оставлять соединение с X висеть.

    Открытых соединений у процесса столько же, сколько было: считаем их по
    дескрипторам, потому что счётчика у Xlib нет.
    """
    import os

    from snapreel.hotkeys_x11 import GrabError

    def handles() -> int:
        return len(os.listdir("/proc/self/fd"))

    before = handles()
    for _ in range(5):
        with pytest.raises(GrabError):
            listener_class({"<ctrl>+<alt>+f24": lambda: None}).start()

    assert handles() <= before + 1  # +1 — запас на служебные файлы самого теста


def test_a_stopped_listener_lets_the_combination_go(listener_class, press):
    seen: list[str] = []
    listener = listener_class({"<ctrl>+<shift>+<alt>+r": lambda: seen.append("наша")})
    listener.start()
    time.sleep(0.3)
    listener.stop()

    assert not listener.is_alive()
    press("ctrl+shift+alt+r")
    time.sleep(0.5)

    assert seen == []

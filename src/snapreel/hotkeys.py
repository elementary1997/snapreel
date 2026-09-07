"""Демон с глобальным хоткеем.

Работает на Windows, X11 и macOS (последней нужно разрешение «Универсальный
доступ»). В Wayland глобальный перехват клавиш композитором запрещён — там
вешайте `snapreel record` на системный хоткей окружения.
"""

from __future__ import annotations

import queue
import sys
from collections.abc import Callable

from .config import Config
from .platform_info import Environment, Platform, detect


class HotkeyError(RuntimeError):
    pass


def listen(config: Config, handler: Callable[[bool], None], env: Environment | None = None):
    """Вешает обе комбинации и сразу отдаёт слушателя, ничего не ожидая.

    `handler(as_gif)` вызывается в потоке pynput, а окна оттуда трогать
    нельзя: трей на нажатие лишь передаёт действие в главный поток
    (`TrayApp._on_main`). Кому нужен главный поток целиком, тому `run`.
    """
    env = env or detect()
    if env.platform is Platform.LINUX_WAYLAND:
        raise HotkeyError(
            "в Wayland глобальные хоткеи перехватить нельзя. Назначьте "
            "`snapreel record` на комбинацию в настройках окружения."
        )
    try:
        from pynput import keyboard
    except ImportError as exc:
        raise HotkeyError("нужен pynput: pip install 'snapreel[daemon]'") from exc

    try:
        listener = keyboard.GlobalHotKeys(
            {
                config.hotkey_mp4: lambda: handler(False),
                config.hotkey_gif: lambda: handler(True),
            }
        )
        listener.start()
    except ValueError as exc:
        # pynput разбирает комбинации сам и бросает своё ValueError; наружу
        # такое значение конфига обязано выходить объяснимой ошибкой, а не
        # трейсбеком — конфиг правится руками, и испортить его несложно
        raise HotkeyError(
            f"комбинацию из конфига не разобрать ({exc}). "
            "Поправьте её в окне настроек или командой `snapreel hotkey set`."
        ) from exc
    except Exception as exc:
        # у каждой платформы свой отказ: нет дисплея, нет разрешения на
        # мониторинг ввода, нет прав. Все они значат одно — клавиши слушать
        # не выйдет, и знать про них должен вызывающий, а не трейсбек
        raise HotkeyError(f"не перехватить горячие клавиши: {exc}") from exc
    return listener


def run(config: Config, handler: Callable[[bool], None], env: Environment | None = None) -> int:
    """Слушает хоткеи и вызывает `handler(as_gif)` в главном потоке.

    Tk обязан жить в главном потоке, поэтому слушатель pynput только кладёт
    заявку в очередь, а запись запускается здесь.
    """
    requests: queue.Queue[bool] = queue.Queue()
    listener = listen(config, lambda as_gif: requests.put(as_gif), env)

    print(f"snapreel: {config.hotkey_mp4} — MP4, {config.hotkey_gif} — GIF, Ctrl+C — выход")
    try:
        while True:
            as_gif = requests.get()
            _drain(requests)
            try:
                handler(as_gif)
            except Exception as exc:  # одна неудачная запись не гасит демон
                print(f"snapreel: {exc}", file=sys.stderr)
            _drain(requests)  # нажатия во время записи не копим
    except KeyboardInterrupt:
        return 0
    finally:
        listener.stop()


def _drain(requests: queue.Queue) -> None:
    while True:
        try:
            requests.get_nowait()
        except queue.Empty:
            return

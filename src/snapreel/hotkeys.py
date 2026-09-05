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


def run(config: Config, handler: Callable[[bool], None], env: Environment | None = None) -> int:
    """Слушает хоткеи и вызывает `handler(as_gif)` в главном потоке.

    Tk обязан жить в главном потоке, поэтому слушатель pynput только кладёт
    заявку в очередь, а запись запускается здесь.
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

    requests: queue.Queue[bool] = queue.Queue()
    bindings = {
        config.hotkey_mp4: lambda: requests.put(False),
        config.hotkey_gif: lambda: requests.put(True),
    }
    listener = keyboard.GlobalHotKeys(bindings)
    listener.start()

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

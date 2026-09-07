"""Глобальные комбинации: кто их ловит и почему иногда не ловит никто.

Работает на Windows, X11 и macOS (последней нужно разрешение «Универсальный
доступ»). В Wayland глобальный перехват клавиш композитором запрещён — там
вешайте `snapreel record` на системный хоткей окружения.

Ловцов два. Обычно это pynput, но он слушает X11 через расширение RECORD, а
оно есть не везде: без него поток pynput умирает молча, и человек видит лишь
то, что ни одна комбинация не работает. Поэтому на таком сервере в дело идёт
`hotkeys_x11` — штатный `XGrabKey`, которому RECORD не нужен.

Почему в этой сессии комбинаций не будет, отвечает `why_silent`: этот ответ
показывают окно настроек и `doctor`, а не только `stderr`.
"""

from __future__ import annotations

import queue
import sys
from collections.abc import Callable
from importlib import util

from .config import Config
from .platform_info import Environment, Platform, detect


class HotkeyError(RuntimeError):
    pass


def why_silent(env: Environment | None = None) -> str | None:
    """Почему комбинации не будут слышны в этой сессии. `None` — будут.

    Отдельная функция, потому что ответ нужен не только при запуске: окно
    настроек и `doctor` обязаны сказать это человеку словами. Раньше причина
    уходила в `stderr`, которого у оконной сборки нет, и «не работает ни один
    хоткей» выглядело беспричинным.
    """
    env = env or detect()
    if env.platform is Platform.LINUX_WAYLAND and not env.is_wsl:
        # композитор глобальных клавиш приложению не отдаёт, и обойти это
        # нечем: XWayland видит только собственные окна
        return (
            "В Wayland глобальные комбинации приложению не отдаются. Назначьте "
            "команду «snapreel record» на комбинацию средствами рабочего стола — "
            "иконку в трее при этом можно не закрывать."
        )
    if util.find_spec("pynput") is None and not _grab_possible(env):
        return "Нет pynput — ставится командой pip install 'snapreel[ui]'."
    if env.is_linux and not _grab_possible(env) and _record_missing():
        return (
            "X-сервер без расширения RECORD, а перехватить клавиши напрямую не "
            "вышло. Назначьте команду «snapreel record» на комбинацию средствами "
            "рабочего стола."
        )
    return None


def probe(config: Config, env: Environment | None = None) -> str | None:
    """Пробует встать на комбинации и сразу отпускает. `None` — встали.

    `why_silent` перечисляет причины, известные заранее, и главного не знает:
    встал ли слушатель. А не встать он может и по месту — комбинацию мог
    занять рабочий стол, а клавиши в ней может не оказаться на раскладке.
    Такой отказ человек обязан увидеть, а не догадаться о нём по тишине.
    """
    refusal = why_silent(env)
    if refusal is not None:
        return refusal
    try:
        listener = listen(config, lambda as_gif: None, env)
    except HotkeyError as exc:
        return str(exc)
    try:
        listener.stop()
    except Exception:  # отпустить не вышло — на ответ это не влияет
        pass
    return None


def mechanism(env: Environment | None = None) -> str:
    """Чем именно ловятся нажатия — для `doctor` и окна настроек."""
    env = env or detect()
    if why_silent(env) is not None:
        return "не слушаются"
    return "перехват X11" if _needs_grab(env) else "pynput"


def _needs_grab(env: Environment) -> bool:
    """На X11 без RECORD слушатель pynput умирает молча — ловим сами.

    Расширение есть почти везде, и трогать проверенный путь без нужды
    незачем: свой перехват включается ровно там, где чужой не работает.
    """
    if not env.is_linux:
        return False
    if util.find_spec("pynput") is None:
        return _grab_possible(env)
    return _record_missing() and _grab_possible(env)


def _record_missing() -> bool:
    """`True` — точно нет; «не знаем» считается «есть»: pynput провереннее."""
    from . import hotkeys_x11

    return hotkeys_x11.has_record() is False


def _grab_possible(env: Environment) -> bool:
    if not env.is_linux:
        return False
    try:
        from . import hotkeys_x11

        return hotkeys_x11.available()
    except Exception:  # нет python-xlib — значит и перехватывать нечем
        return False


def _grab(config: Config, handler: Callable[[bool], None]):
    from . import hotkeys_x11

    listener = hotkeys_x11.Listener(
        {
            config.hotkey_mp4: lambda: handler(False),
            config.hotkey_gif: lambda: handler(True),
        }
    )
    try:
        listener.start()
    except hotkeys_x11.GrabError as exc:
        raise HotkeyError(str(exc)) from exc
    return listener


def listen(config: Config, handler: Callable[[bool], None], env: Environment | None = None):
    """Вешает обе комбинации и сразу отдаёт слушателя, ничего не ожидая.

    `handler(as_gif)` вызывается в потоке pynput, а окна оттуда трогать
    нельзя: трей на нажатие лишь передаёт действие в главный поток
    (`TrayApp._on_main`). Кому нужен главный поток целиком, тому `run`.
    """
    env = env or detect()
    refusal = why_silent(env)
    if refusal is not None:
        raise HotkeyError(refusal)

    if _needs_grab(env):
        return _grab(config, handler)

    try:
        from pynput import keyboard
    except ModuleNotFoundError as exc:
        raise HotkeyError("нужен pynput: pip install 'snapreel[ui]'") from exc
    except ImportError as exc:
        # pynput подключается к оконной системе прямо на импорте и о неудаче
        # сообщает обычным ImportError. «Пакета нет» тут было бы враньём —
        # он есть, и `doctor` двумя строками выше сам это написал
        raise HotkeyError(f"pynput не поднялся: {exc}") from exc

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

    Окна обязаны жить в главном потоке, поэтому слушатель только кладёт
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

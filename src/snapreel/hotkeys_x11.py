"""Перехват комбинаций средствами самого X11, без расширения RECORD.

pynput слушает клавиатуру через расширение RECORD: оно отдаёт приложению
поток всех событий ввода, а комбинации оно отбирает уже у себя. Расширение
есть не везде — на серверах, где его выключили, поток слушателя pynput
умирает с `AttributeError: record_create_context`, и снаружи это выглядит
как «ни один хоткей не работает», без единого слова о причине.

Здесь то же самое делается штатным `XGrabKey`: сервер присылает только те
нажатия, о которых мы просили. Побочная выгода — snapreel перестаёт видеть
чужой ввод вовсе, а занятая другим приложением комбинация становится
объяснимым отказом (`BadAccess`), а не тишиной.

Модуль подтягивается лениво и только на X11: `python-xlib` приезжает вместе
с pynput, отдельной зависимостью его не заводили.
"""

from __future__ import annotations

import select
import threading
from collections.abc import Callable

# Клавиши, у которых имя X отличается от нашего. Остальные (буквы, цифры,
# F1–F24) переводятся в keysym как есть.
KEYSYMS = {
    "esc": "Escape",
    "escape": "Escape",
    "enter": "Return",
    "space": "space",
    "tab": "Tab",
    "insert": "Insert",
    "delete": "Delete",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "print": "Print",
    "printscreen": "Print",
}


class GrabError(RuntimeError):
    """Комбинацию не перехватить: нет такой клавиши или её уже занял сосед."""


def available() -> bool:
    """Есть ли на этом дисплее то, что нужно для перехвата."""
    try:
        display = _open()
    except GrabError:
        return False
    display.close()
    return True


def has_record() -> bool | None:
    """Есть ли расширение RECORD — то, чем слушает pynput.

    `None` — спросить не вышло (нет python-xlib или нет дисплея); это не
    «нет», а «не знаем», и решать по такому ответу нельзя.
    """
    try:
        display = _open()
    except GrabError:
        return None
    try:
        return display.query_extension("RECORD") is not None
    finally:
        display.close()


class Listener:
    """Слушатель с теми же именами методов, что и у pynput.

    Трею и демону всё равно, кто именно ловит нажатия, поэтому наружу
    выставлены ровно `start`, `stop` и `is_alive`.
    """

    def __init__(self, bindings: dict[str, Callable[[], None]]):
        self._bindings = bindings
        self._display = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._grabs: dict[tuple[int, int], Callable[[], None]] = {}

    # --- жизненный цикл ---------------------------------------------------

    def start(self) -> None:
        from Xlib import X, error

        self._display = _open()
        root = self._display.screen().root
        slop_masks = _slop_masks()
        catcher = error.CatchError(error.BadAccess)
        for spec, action in self._bindings.items():
            code, mask = _combination(self._display, spec)
            self._grabs[(code, mask)] = action
            for slop in slop_masks:
                root.grab_key(
                    code,
                    mask | slop,
                    True,
                    X.GrabModeAsync,
                    X.GrabModeAsync,
                    onerror=catcher,
                )
        self._display.sync()
        if catcher.get_error() is not None:
            self.stop()
            raise GrabError(
                "комбинацию уже занял кто-то другой — рабочий стол или другое "
                "приложение. Выберите другую в настройках."
            )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        display, self._display = self._display, None
        if display is not None:
            try:
                display.close()
            except Exception:  # соединение могло оборваться само
                pass

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def running(self) -> bool:
        return self.is_alive()

    # --- цикл событий -----------------------------------------------------

    def _loop(self) -> None:
        """Ждёт нажатий на сокете дисплея, а не крутится вхолостую.

        Срок у ожидания нужен только затем, чтобы заметить просьбу
        остановиться: событий тут может не быть часами.
        """
        from Xlib import X

        display = self._display
        while not self._stopping.is_set():
            try:
                ready, _, _ = select.select([display], [], [], 0.2)
            except (OSError, ValueError):
                return  # дисплей закрыли из-под нас — это и есть остановка
            if not ready:
                continue
            try:
                for _ in range(display.pending_events()):
                    event = display.next_event()
                    if event.type != X.KeyPress:
                        continue
                    action = self._grabs.get((event.detail, _significant(event.state)))
                    if action is not None:
                        action()
            except Exception:
                return  # сервер ушёл — слушать больше нечего


# --- разбор комбинации ----------------------------------------------------


def _combination(display, spec: str) -> tuple[int, int]:
    """Из нашей записи комбинации — в код клавиши и маску модификаторов."""
    from Xlib import XK

    from .autostart import HotkeySetupError, parse_hotkey

    try:
        modifiers, key = parse_hotkey(spec)
    except HotkeySetupError as exc:
        raise GrabError(str(exc)) from exc

    name = KEYSYMS.get(key, key.upper() if len(key) > 1 else key)
    keysym = XK.string_to_keysym(name)
    if not keysym:
        raise GrabError(f"клавиша {key!r} серверу X неизвестна")
    code = display.keysym_to_keycode(keysym)
    if not code:
        raise GrabError(f"клавиши {key!r} нет на этой раскладке")

    mask = 0
    for modifier in modifiers:
        bit = _masks().get(modifier)
        if bit is None:
            raise GrabError(f"модификатор {modifier!r} серверу X неизвестен")
        mask |= bit
    return code, mask


def _masks() -> dict[str, int]:
    from Xlib import X

    return {
        "ctrl": X.ControlMask,
        "shift": X.ShiftMask,
        "alt": X.Mod1Mask,
        "super": X.Mod4Mask,
    }


def _significant(state: int) -> int:
    """Снимает с состояния то, что к комбинации отношения не имеет."""
    from Xlib import X

    return state & (X.ControlMask | X.ShiftMask | X.Mod1Mask | X.Mod4Mask)


def _open():
    from Xlib import display

    try:
        return display.Display()
    except Exception as exc:  # нет DISPLAY, нет python-xlib, сервер отказал
        raise GrabError(f"не подключиться к X-серверу: {exc}") from exc


def _slop_masks() -> list[int]:
    """Замки клавиатуры входят в состояние события и ломали бы совпадение.

    Caps Lock, Num Lock и Scroll Lock живут в тех же битах, что и модификаторы,
    поэтому каждая комбинация перехватывается во всех их сочетаниях — иначе
    хоткей переставал бы работать при включённом Num Lock.
    """
    from Xlib import X

    locks = [0, X.LockMask, X.Mod2Mask, X.Mod5Mask]
    masks = {0}
    for first in locks:
        for second in locks:
            masks.add(first | second)
    return sorted(masks)

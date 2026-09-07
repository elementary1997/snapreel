"""Свой владелец буфера обмена в X11 — с точным списком типов.

Зачем это поверх `xclip`: он отдаёт буфер ровно в одном типе, а спрашивают
разное. Chromium и всё, что на Electron — Cursor, Claude, Slack, — берут файл
из `x-special/gnome-copied-files`, файловые менеджеры и Telegram — из
`text/uri-list`. Один тип означает «где-то вставится файл, а где-то ничего».

Почему не силами Qt, у которого буфер тоже многоформатный: он выводит из
`text/uri-list` ещё и `text/plain` с адресом файла, и убрать это нечем
(проверено на живом сервере: в TARGETS всё равно приезжают `text/plain` и
`text/x-moz-url`). Ровно из-за такой строки чат и вставляет путь вместо
вложения — то есть делает обратное тому, ради чего всё затевалось. Свой
владелец объявляет только то, что мы велели, и ничего сверх.

Здесь же и второе удобство: соединение с X своё, поэтому владеть буфером
может любой поток — упаковка клипа идёт не в главном.

Владение живёт, пока жив процесс: так устроен X11, у `xclip` ровно так же.
Поэтому путь этот только для резидента (трея), а на выходе он отдаёт
содержимое переживающему нас `xclip` — `hand_off`.
"""

from __future__ import annotations

import select
import threading
from pathlib import Path

from .posix import ClipboardError, file_uri

# Типы, которыми мы объявляем один и тот же файл. Порядок важен только для
# человека, читающего TARGETS: приложения выбирают сами.
URI_LIST = "text/uri-list"
GNOME_FILES = "x-special/gnome-copied-files"

_current: Owner | None = None
_lock = threading.Lock()


def available() -> bool:
    """Есть ли X-сервер, у которого можно забрать буфер."""
    try:
        _open().close()
    except ClipboardError:
        return False
    return True


def copy_files(paths: list[Path]) -> None:
    """Забирает буфер себе и отдаёт файл всем, кто спросит."""
    global _current

    uris = [file_uri(path) for path in paths]
    owner = Owner(paths, payload(uris))
    owner.start()
    with _lock:
        previous, _current = _current, owner
    if previous is not None:
        previous.stop()  # прежнее содержимое больше не наше


def hand_off() -> None:
    """Отдаёт буфер тому, кто нас переживёт.

    Зовётся на выходе: с концом процесса владение пропадает, и человек,
    закрывший иконку сразу после записи, остался бы с пустым буфером.
    """
    with _lock:
        owner, current = _current, None
        globals()["_current"] = current
    if owner is None or not owner.owns():
        return
    from .posix import x11_copy_files

    try:
        x11_copy_files(owner.paths)
    except ClipboardError:
        pass  # нет xclip — содержимое пропадёт вместе с нами, но выход не ждёт
    finally:
        owner.stop()


def payload(uris: list[str]) -> dict[str, bytes]:
    """Один и тот же файл в двух видах — по одному на семейство программ."""
    return {
        URI_LIST: ("\r\n".join(uris) + "\r\n").encode("utf-8"),
        # «copy» вместо «cut» — иначе файловый менеджер после вставки удалит
        # исходник, а он лежит в каталоге клипов и нужен человеку
        GNOME_FILES: ("copy\n" + "\n".join(uris)).encode("utf-8"),
    }


class Owner:
    """Владелец селекции CLIPBOARD: отвечает на запросы, пока его не сменят."""

    def __init__(self, paths: list[Path], data: dict[str, bytes]):
        self.paths = list(paths)
        self.data = data
        self._display = None
        self._window = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        from Xlib import X

        self._display = _open()
        display = self._display
        self._window = display.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
        self._window.set_selection_owner(display.intern_atom("CLIPBOARD"), X.CurrentTime)
        display.sync()
        if not self.owns():
            self.stop()
            raise ClipboardError("буфером обмена завладеть не вышло")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def owns(self) -> bool:
        display, window = self._display, self._window
        if display is None or window is None:
            return False
        try:
            return display.get_selection_owner(display.intern_atom("CLIPBOARD")) == window
        except Exception:  # соединение оборвалось — значит уже не наше
            return False

    def stop(self) -> None:
        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        display, self._display = self._display, None
        self._window = None
        if display is not None:
            try:
                display.close()
            except Exception:  # закрывать нечего — соединения уже нет
                pass

    # --- цикл событий -----------------------------------------------------

    def _loop(self) -> None:
        """Ждёт запросов на сокете дисплея; срок — чтобы заметить остановку."""
        from Xlib import X

        display = self._display
        while not self._stopping.is_set():
            try:
                ready, _, _ = select.select([display], [], [], 0.2)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                for _ in range(display.pending_events()):
                    event = display.next_event()
                    if event.type == X.SelectionRequest:
                        self._answer(event)
                    elif event.type == X.SelectionClear:
                        return  # буфер забрал кто-то другой, и это его право
            except Exception:
                return  # сервер ушёл — отвечать больше некому

    def _answer(self, request) -> None:
        """Отдаёт запрошенный тип или честно отказывает."""
        from Xlib import X
        from Xlib.protocol import event

        display = self._display
        # старые клиенты присылают property=0 и ждут ответа в сам target
        prop = request.property if request.property != 0 else request.target
        name = display.get_atom_name(request.target)

        if name == "TARGETS":
            atoms = [display.intern_atom(key) for key in self.data]
            atoms += [display.intern_atom("TARGETS"), display.intern_atom("TIMESTAMP")]
            request.requestor.change_property(prop, display.intern_atom("ATOM"), 32, atoms)
        elif name == "TIMESTAMP":
            request.requestor.change_property(
                prop, display.intern_atom("INTEGER"), 32, [X.CurrentTime]
            )
        elif name in self.data:
            request.requestor.change_property(prop, request.target, 8, self.data[name])
        else:
            prop = 0  # такого типа у нас нет — так и говорим

        request.requestor.send_event(
            event.SelectionNotify(
                time=request.time,
                requestor=request.requestor,
                selection=request.selection,
                target=request.target,
                property=prop,
            )
        )
        display.flush()


def _open():
    try:
        from Xlib import display
    except Exception as exc:  # python-xlib приезжает с pynput, но мог и не приехать
        raise ClipboardError(f"нет python-xlib: {exc}") from exc
    try:
        return display.Display()
    except Exception as exc:
        raise ClipboardError(f"не подключиться к X-серверу: {exc}") from exc

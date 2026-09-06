"""Иконка в настоящем трее: встаёт ли она туда вообще.

Проверка требует живого X-сервера и потому помечена `gui`: в обычный прогон
не входит, гоняется отдельно — `xvfb-run pytest -m gui`, как и окно настроек.
Без неё дефект «процесс запустился, а иконки нет» виден только у пользователя:
pystray в такой ситуации не бросает исключение, а молча пишет себе в лог.

Роль трея играет свой минимальный менеджер XEmbed — поднимать целую панель
рабочего стола ради одного окна незачем. Qt на X11 встаёт в него так же, как
любое другое приложение.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.gui

DOCK_REQUEST = 0  # SYSTEM_TRAY_REQUEST_DOCK


class SystemTray:
    """Минимальный менеджер трея: владеет селекцией и принимает заявки на док."""

    def __init__(self):
        from Xlib import X, display
        from Xlib.protocol import event

        self._X = X
        self._event = event
        self.display = display.Display()
        screen = self.display.screen()
        self.window = screen.root.create_window(-1, -1, 1, 1, 0, screen.root_depth)
        self.window.change_attributes(event_mask=X.StructureNotifyMask | X.SubstructureNotifyMask)

        number = self.display.get_default_screen()
        self.selection = self.display.intern_atom(f"_NET_SYSTEM_TRAY_S{number}")
        self.opcode = self.display.intern_atom("_NET_SYSTEM_TRAY_OPCODE")
        manager = self.display.intern_atom("MANAGER")

        self.window.set_selection_owner(self.selection, X.CurrentTime)
        self.display.sync()
        if self.display.get_selection_owner(self.selection) != self.window:
            raise RuntimeError("селекция трея занята кем-то ещё")

        # так менеджер объявляет о себе: приложения ждут именно этого события
        screen.root.send_event(
            event.ClientMessage(
                window=screen.root,
                client_type=manager,
                data=(32, [X.CurrentTime, self.selection, self.window.id, 0, 0]),
            ),
            event_mask=X.StructureNotifyMask,
        )
        self.display.flush()

    def wait_for_icon(self, timeout: float = 20.0) -> int | None:
        """Идентификатор окна иконки или None, если никто так и не пришёл."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while self.display.pending_events():
                message = self.display.next_event()
                if not isinstance(message, self._event.ClientMessage):
                    continue
                if message.client_type != self.opcode:
                    continue
                _stamp, opcode, icon_window, *_ = message.data[1]
                if opcode == DOCK_REQUEST:
                    return icon_window
            time.sleep(0.05)
        return None

    def close(self) -> None:
        try:
            self.window.destroy()
            self.display.close()
        except Exception:  # дисплей мог уйти раньше — тесту это уже неважно
            pass


@pytest.fixture
def system_tray(need):
    if not os.environ.get("DISPLAY"):
        pytest.skip("нужен X-сервер: xvfb-run pytest -m gui")
    need("Xlib")  # python-xlib: им написан сам менеджер
    need("PySide6")
    tray = SystemTray()
    yield tray
    tray.close()


def test_the_tray_icon_docks_into_a_system_tray(system_tray, tmp_path):
    environment = dict(os.environ)
    environment.pop("WAYLAND_DISPLAY", None)  # проверяем именно путь X11
    environment["XDG_SESSION_TYPE"] = "x11"
    environment["XDG_CONFIG_HOME"] = str(tmp_path)
    # у Qt в этой сессии может быть выбор, а нам нужен именно X11: иначе окно
    # и иконка уедут в чужой композитор, и менеджер их не увидит
    environment["QT_QPA_PLATFORM"] = "xcb"

    process = subprocess.Popen(
        [sys.executable, "-m", "snapreel", "tray"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    try:
        icon_window = system_tray.wait_for_icon(20.0)
        assert icon_window is not None, "иконка не встала в трей"
        assert process.poll() is None, "трей вышел, не дождавшись ничего"
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:  # pragma: no cover — трей завис
            process.kill()
            raise

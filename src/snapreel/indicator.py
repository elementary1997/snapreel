"""Индикатор записи: рамка вокруг области, таймер и кнопка остановки.

Рамка собрана из четырёх тонких окон по краям области — так она не
перекрывает то, что записывается, и не попадает в кадр сама. Панель с
таймером стоит рядом с областью, а не внутри неё, по той же причине.
"""

from __future__ import annotations

from collections.abc import Callable

from .platform_info import Environment, Platform, detect
from .region import Region

ACCENT = "#ff4d4f"
PANEL_BG = "#1b1d23"
PANEL_FG = "#f2f4f8"
BORDER = 3
POLL_MS = 100
PANEL_W = 220
PANEL_H = 44


def format_seconds(value: float) -> str:
    total = int(value)
    return f"{total // 60}:{total % 60:02d}"


class RecordingIndicator:
    """Крутит цикл Qt, пока идёт запись.

    Класс собирается лениво (`_widgets`), потому что модуль обязан
    импортироваться и там, где Qt нет: `doctor` и запись с `--no-indicator`
    работают без него.
    """

    def __init__(
        self,
        region: Region,
        *,
        max_seconds: float,
        min_seconds: float,
        is_finished: Callable[[], bool],
        elapsed: Callable[[], float],
        request_stop: Callable[[], None],
        env: Environment | None = None,
    ) -> None:
        self.region = region
        self.max_seconds = max_seconds
        self.min_seconds = min_seconds
        self.is_finished = is_finished
        self.elapsed = elapsed
        self.request_stop = request_stop
        self.env = env or detect()
        self.stop_requested = False
        self._done = False
        self._loop = None

        from .qt import application
        from .selector import _logical

        application()
        self._logical = _logical
        self.windows: list[object] = []
        if self.env.platform is not Platform.MACOS:
            self._build_border()
        self._build_panel()

    # --- окна -------------------------------------------------------------

    def _floating(self, width: int, height: int, x: int, y: int, colour: str):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QWidget

        window = QWidget()
        window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.setStyleSheet(f"background: {colour};")
        window.setGeometry(
            self._logical(x, self.env),
            self._logical(y, self.env),
            max(self._logical(width, self.env), 1),
            max(self._logical(height, self.env), 1),
        )
        window.show()
        self.windows.append(window)
        return window

    def _build_border(self) -> None:
        r, b = self.region, BORDER
        edges = [
            (r.width + 2 * b, b, r.x - b, r.y - b),  # верх
            (r.width + 2 * b, b, r.x - b, r.bottom),  # низ
            (b, r.height, r.x - b, r.y),  # лево
            (b, r.height, r.right, r.y),  # право
        ]
        for width, height, x, y in edges:
            self._floating(width, height, x, y, ACCENT)

    def _build_panel(self) -> None:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

        x = self.region.x
        y = self.region.y - BORDER - PANEL_H - 6
        if y < 0:
            y = self.region.bottom + BORDER + 6
        panel = self._floating(PANEL_W, PANEL_H, x, y, PANEL_BG)

        row = QHBoxLayout(panel)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(8)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {ACCENT}; background: transparent;")
        row.addWidget(self.dot)

        self.label = QLabel(f"0:00 / {format_seconds(self.max_seconds)}")
        self.label.setStyleSheet(f"color: {PANEL_FG}; background: transparent; font-weight: 600;")
        row.addWidget(self.label, 1)

        self.button = QPushButton("Стоп (Esc)")
        self.button.setStyleSheet(
            f"QPushButton {{ background: #2c3038; color: {PANEL_FG}; border: none;"
            " border-radius: 6px; padding: 4px 10px; }"
            "QPushButton:hover { background: #3a3f49; }"
            "QPushButton:disabled { color: #8b93a1; }"
        )
        self.button.clicked.connect(self._on_stop)
        row.addWidget(self.button)

        # Esc работает и когда фокуса нет: нажимать в окно, которое намеренно
        # не забирает фокус, человеку неоткуда
        from PySide6.QtGui import QKeySequence, QShortcut

        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), panel)
        shortcut.activated.connect(self._on_stop)

        self.panel = panel
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(POLL_MS)

    # --- цикл -------------------------------------------------------------

    def _blink(self, seconds: float) -> None:
        colour = ACCENT if int(seconds * 2) % 2 == 0 else PANEL_BG
        self.dot.setStyleSheet(f"color: {colour}; background: transparent;")

    def _tick(self) -> None:
        if self.is_finished():
            self._finish()
            return
        seconds = self.elapsed()
        if seconds >= self.max_seconds:
            # бэкенды без собственного лимита (wf-recorder) останавливает этот таймер
            self.request_stop()
            self._finish()
            return
        self._blink(seconds)
        self.label.setText(f"{format_seconds(seconds)} / {format_seconds(self.max_seconds)}")
        remaining = self.min_seconds - seconds
        if remaining > 0:
            self.button.setEnabled(False)
            self.button.setText(f"Стоп через {int(remaining) + 1}")
        elif not self.button.isEnabled():
            self.button.setEnabled(True)
            self.button.setText("Стоп (Esc)")

    def _on_stop(self) -> None:
        if self.elapsed() < self.min_seconds:
            return
        self.stop_requested = True
        self.request_stop()
        self._finish()

    def _finish(self) -> None:
        self._done = True
        self.timer.stop()
        if self._loop is not None:
            self._loop.quit()

    def run(self) -> bool:
        """Возвращает True, если остановил пользователь, а не таймер.

        Ожидание держит вложенный цикл событий, а не опрос в холостую:
        рамка висит десятками секунд, и всё это время процессор был занят
        ничем. Из трея цикл к тому же вкладывается в уже работающий, и
        иконка продолжает отвечать.
        """
        from PySide6.QtCore import QEventLoop

        self._loop = QEventLoop()
        try:
            if not self._done:
                self._loop.exec()
        finally:
            self._loop = None
            self.close()
        return self.stop_requested

    def close(self) -> None:
        for window in self.windows:
            try:
                window.close()
                window.deleteLater()
            except RuntimeError:  # окно уже убрано самим Qt
                pass
        self.windows = []

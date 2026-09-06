"""Полноэкранный оверлей выделения области — на Qt.

Окно накрывает весь виртуальный рабочий стол, затемняет его и вырезает
светлое окно там, где человек ведёт мышью: так видно, что именно попадёт в
кадр. Наружу отдаются те же координаты, что и раньше, — в системе, в которой
их ждут gdigrab и x11grab.
"""

from __future__ import annotations

from .errors import OverlayUnavailable, SelectionCancelled
from .platform_info import Environment, Platform, detect, virtual_desktop
from .region import Region, from_corners, normalize

__all__ = ["OverlayUnavailable", "SelectionCancelled", "select_region"]

DIM = (16, 16, 20, 96)  # затемнение поверх экрана
ACCENT = "#4ea1ff"
HINT = "Выделите область мышью    ·    Esc — отмена"


def select_region(env: Environment | None = None, min_side: int = 16) -> Region:
    """Показывает затемнённый оверлей и возвращает выделенный прямоугольник."""
    env = env or detect()
    from .qt import application

    application()
    overlay = _overlay_class()(virtual_desktop(env), env, min_side)
    return overlay.run()


def physical(point, env: Environment) -> tuple[int, int]:
    """Из координат Qt — в те, которыми меряет захват экрана.

    Qt считает в логических точках, а gdigrab и x11grab — в физических
    пикселях: на Windows со масштабом 125% разница видна сразу, запишется не
    та область. На macOS перевод не нужен и вреден — там масштаб Retina
    добавляет `recorder` по пробному кадру (ADR и гочи о том же).
    """
    if env.platform is Platform.MACOS:
        return point.x(), point.y()
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    return round(point.x() * ratio), round(point.y() * ratio)


def _logical(value: int, env: Environment) -> int:
    """Обратный перевод: столько же пикселей, но в мерках Qt."""
    if env.platform is Platform.MACOS:
        return value
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    return round(value / ratio)


def _overlay_class():
    """Класс окна собирается лениво: без Qt модуль обязан импортироваться."""
    try:
        from PySide6.QtCore import QPoint, QRect, Qt
        from PySide6.QtGui import QColor, QFont, QPainter, QPen
        from PySide6.QtWidgets import QWidget
    except ImportError as exc:
        raise OverlayUnavailable(
            "нет PySide6 — не показать выделение области "
            "(pip install 'snapreel[ui]'). Либо задайте её флагом --region WxH+X+Y."
        ) from exc

    class Overlay(QWidget):
        def __init__(self, desktop: Region, env: Environment, min_side: int):
            super().__init__()
            self.desktop = desktop
            self.env = env
            self.min_side = min_side
            self.result: Region | None = None
            self.start: QPoint | None = None
            self.current: QPoint | None = None

            self.setWindowTitle("snapreel")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setGeometry(
                _logical(desktop.x, env),
                _logical(desktop.y, env),
                _logical(desktop.width, env),
                _logical(desktop.height, env),
            )

        # --- рисование ---------------------------------------------------

        def paintEvent(self, event) -> None:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(*DIM))
            if self.start is None or self.current is None:
                self._draw_hint(painter)
                return

            box = QRect(self.start, self.current).normalized()
            # выделенное не затемняем: человек должен видеть, что снимает
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(box, QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.drawRect(box)
            self._draw_size(painter, box)

        def _draw_hint(self, painter) -> None:
            painter.setPen(QColor("#f2f4f8"))
            font = QFont(self.font())
            font.setPointSize(15)
            painter.setFont(font)
            painter.drawText(
                self.rect().adjusted(0, 0, 0, -self.height() // 4),
                Qt.AlignmentFlag.AlignCenter,
                HINT,
            )

        def _draw_size(self, painter, box) -> None:
            ratio = 1 if self.env.platform is Platform.MACOS else self.devicePixelRatio()
            text = f"{round(box.width() * ratio)}×{round(box.height() * ratio)}"
            font = QFont(self.font())
            font.setPointSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffffff"))
            top = box.top() - 8 if box.top() > 24 else box.bottom() + 20
            painter.drawText(box.left() + 2, top, text)

        # --- события -----------------------------------------------------

        def mousePressEvent(self, event) -> None:
            if event.button() is Qt.MouseButton.RightButton:
                self._cancel()
                return
            self.start = event.position().toPoint()
            self.current = self.start
            self.update()

        def mouseMoveEvent(self, event) -> None:
            if self.start is None:
                return
            self.current = event.position().toPoint()
            self.update()

        def mouseReleaseEvent(self, event) -> None:
            if self.start is None:
                return
            first = physical(event.globalPosition().toPoint(), self.env)
            origin = self._origin_global()
            region = from_corners(origin[0], origin[1], first[0], first[1])
            if region.width < self.min_side or region.height < self.min_side:
                # случайный клик — оставляем оверлей открытым
                self.start = self.current = None
                self.update()
                return
            self.result = region
            self.close()

        def keyPressEvent(self, event) -> None:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel()

        def _origin_global(self) -> tuple[int, int]:
            """Точка нажатия в тех же координатах, что и точка отпускания."""
            return physical(self.mapToGlobal(self.start), self.env)

        def _cancel(self) -> None:
            self.result = None
            self.close()

        # --- запуск ------------------------------------------------------

        def run(self) -> Region:
            from PySide6.QtWidgets import QApplication

            self.show()
            self.raise_()
            self.activateWindow()
            app = QApplication.instance()
            while self.isVisible():
                app.processEvents()
            if self.result is None:
                raise SelectionCancelled("выделение отменено")
            return normalize(self.result, self.min_side)

    return Overlay

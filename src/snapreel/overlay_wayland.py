"""Живое выделение и панель записи на слое Wayland, отдельно от захвата."""

from __future__ import annotations

import ctypes as C
import os
import queue
import select
import threading

from .indicator_wayland import LayerClient
from .portal import PortalError
from .region import Region
from .wayland import Interface


def panel_position(region, screens, width=260, height=44):
    ordered = sorted(screens, key=lambda screen: not (screen.x <= region.x < screen.right))
    for screen in ordered:
        x = max(screen.x, min(region.x, screen.right - width))
        for y in (region.y - height - 8, region.bottom + 8):
            if screen.y <= y and y + height <= screen.bottom and screen.width >= width:
                return Region(x, y, width, height)
    return None


class SurfaceClient(LayerClient):
    def __init__(self, owner):
        super().__init__(owner.screens, owner.region)
        self.owner = owner
        self.surfaces = {}
        self.pointer_surface = None
        self.pointer_position = (0, 0)

    def edges(self, bounds):
        return [
            edge
            for edge in self.owner.rectangles
            if bounds.x <= edge.x
            and bounds.y <= edge.y
            and edge.right <= bounds.right
            and edge.bottom <= bounds.bottom
        ]

    def interactive(self):
        return True

    def keyboard(self):
        return self.owner.selection

    def created(self, surface, edge, shm):
        self.surfaces[surface] = (edge, shm)

    def paint(self, edge):
        with self.owner.lock:
            return self.owner.pixels[edge]

    def start(self):
        super().start()
        p, u, i = C.c_void_p, C.c_uint32, C.c_int
        seat = self._bind_global(self.registry, self.globals["wl_seat"][0], self.seat_interface, 1)
        pointer_interface = Interface.in_dll(self.lib, "wl_pointer_interface")
        keyboard_interface = Interface.in_dll(self.lib, "wl_keyboard_interface")

        def capabilities(data, proxy, caps):
            if caps & 1:
                pointer = self._marshal(seat, 0, pointer_interface, 1, p())

                def enter(data, proxy, serial, surface, x, y):
                    self.pointer_surface = surface
                    self.pointer_position = (x / 256, y / 256)

                def motion(data, proxy, timestamp, x, y):
                    self.pointer_position = (x / 256, y / 256)
                    self.emit("motion")

                def button(data, proxy, serial, timestamp, button, state):
                    self.emit("press" if state else "release", button)

                self._listen(
                    pointer,
                    [
                        ([u, p, i, i], enter),
                        ([u, p], lambda *args: setattr(self, "pointer_surface", None)),
                        ([u, i, i], motion),
                        ([u, u, u, u], button),
                        ([u, u, i], lambda *args: None),
                    ],
                )
            if caps & 2 and self.owner.selection:
                keyboard = self._marshal(seat, 1, keyboard_interface, 1, p())
                self._listen(
                    keyboard,
                    [
                        ([u, i, u], lambda data, proxy, fmt, fd, size: os.close(fd)),
                        ([u, p, p], lambda *args: None),
                        ([u, p], lambda *args: None),
                        (
                            [u, u, u, u],
                            lambda data, proxy, serial, timestamp, key, state: (
                                self.owner.events.put(("cancel", 0, 0, 0))
                                if key == 1 and state
                                else None
                            ),
                        ),
                        ([u, u, u, u, u], lambda *args: None),
                    ],
                )

        self._listen(seat, [([u], capabilities)])
        self.lib.wl_display_roundtrip(self.display)

    def emit(self, kind, button=0):
        if self.pointer_surface in self.surfaces:
            edge, _ = self.surfaces[self.pointer_surface]
            x, y = self.pointer_position
            self.owner.events.put((kind, edge.x + int(x), edge.y + int(y), button))

    def update(self):
        if self.owner.dirty.is_set():
            self.owner.dirty.clear()
            for surface, (edge, shm) in self.surfaces.items():
                self.attach(surface, shm, edge)


class Surfaces:
    def __init__(self, rectangles, *, selection=False):
        from PySide6.QtGui import QGuiApplication

        self.screens = {
            s.name(): Region(g.x(), g.y(), g.width(), g.height())
            for s in QGuiApplication.screens()
            for g in [s.geometry()]
        }
        self.rectangles = rectangles
        self.region = rectangles[0]
        self.selection = selection
        self.events = queue.SimpleQueue()
        self.pixels = {}
        self.lock = threading.Lock()
        self.dirty = threading.Event()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.error = None

    def image(self, rectangle, image):
        with self.lock:
            self.pixels[rectangle] = bytes(image.constBits())
        self.dirty.set()

    def start(self):
        from .screencast import wait_ready

        self.thread = threading.Thread(target=self.run, name="snapreel-overlay", daemon=True)
        self.thread.start()
        wait_ready(self.ready)
        if self.error:
            self.close()
            raise PortalError(str(self.error)) from self.error

    def run(self):
        client = None
        try:
            client = SurfaceClient(self)
            client.start()
            self.dirty.clear()
            self.ready.set()
            fd = client.lib.wl_display_get_fd(client.display)
            while not self.stopped.is_set() and not client.cancelled:
                client.update()
                if client.lib.wl_display_dispatch_pending(client.display) < 0:
                    break
                client.lib.wl_display_flush(client.display)
                if (
                    select.select([fd], [], [], 0.03)[0]
                    and client.lib.wl_display_dispatch(client.display) < 0
                ):
                    break
        except Exception as exc:
            self.error = exc
        finally:
            if client:
                client.close()
            self.ready.set()

    def close(self):
        self.stopped.set()
        if hasattr(self, "thread"):
            self.thread.join(timeout=3)


def select_live(capture):
    from PySide6.QtCore import QEventLoop, QRect, Qt, QTimer
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen

    from .errors import SelectionCancelled
    from .indicator_wayland import desktop_region
    from .region import from_corners, normalize

    screens = [
        Region(g.x(), g.y(), g.width(), g.height())
        for s in QGuiApplication.screens()
        for g in [s.geometry()]
    ]
    desktop = Region(
        min(r.x for r in screens),
        min(r.y for r in screens),
        max(r.right for r in screens) - min(r.x for r in screens),
        max(r.bottom for r in screens) - min(r.y for r in screens),
    )
    if desktop_region(capture, capture, desktop) is None:
        raise PortalError("Неизвестно положение разрешённых экранов.")
    surfaces = Surfaces(screens, selection=True)
    origin = None
    current = None
    result = None
    loop = QEventLoop()

    def render():
        for screen in screens:
            image = QImage(screen.width, screen.height, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(16, 16, 20, 65))
            painter = QPainter(image)
            if origin and current:
                r = from_corners(*origin, *current)
                box = QRect(r.x - screen.x, r.y - screen.y, r.width, r.height)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(box, Qt.GlobalColor.transparent)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                painter.setPen(QPen(QColor("#63aaff"), 2))
                painter.drawRect(box)
            else:
                painter.setPen(QColor("#ffffff"))
                painter.drawText(
                    QRect(0, 0, screen.width, 120),
                    Qt.AlignmentFlag.AlignCenter,
                    "Выделите область мышью · Esc — отмена",
                )
            painter.end()
            surfaces.image(screen, image)

    def tick():
        nonlocal origin, current, result
        changed = False
        while not surfaces.events.empty():
            kind, x, y, button = surfaces.events.get()
            if kind == "cancel" or (kind == "press" and button == 273):
                loop.quit()
                return
            if kind == "press" and button == 272:
                origin = (x, y)
                current = origin
                changed = True
            if kind == "motion" and origin:
                current = (x, y)
                changed = True
            if kind == "release" and button == 272 and origin:
                r = from_corners(*origin, x, y)
                if r.width >= 16 and r.height >= 16:
                    result = normalize(
                        Region(
                            capture.x + r.x - desktop.x,
                            capture.y + r.y - desktop.y,
                            r.width,
                            r.height,
                        )
                    )
                    loop.quit()
                    return
                origin = None
                current = None
                changed = True
        if surfaces.error or not surfaces.thread.is_alive():
            loop.quit()
        if changed:
            render()

    render()
    surfaces.start()
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(30)
    try:
        loop.exec()
    finally:
        timer.stop()
        surfaces.close()
    if result is None:
        raise SelectionCancelled("выделение отменено")
    return result


class Panel:
    def __init__(self, region, max_seconds):
        from PySide6.QtGui import QGuiApplication

        screens = [
            Region(g.x(), g.y(), g.width(), g.height())
            for s in QGuiApplication.screens()
            for g in [s.geometry()]
        ]
        rectangle = panel_position(region, screens)
        if rectangle is None:
            raise PortalError("Нет места для панели снаружи области.")
        self.rectangle = rectangle
        self.max_seconds = max_seconds
        self.surfaces = Surfaces([rectangle])
        self.update(0, False)
        self.surfaces.start()

    def update(self, seconds, enabled):
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtGui import QColor, QImage, QPainter

        from .indicator import format_seconds

        r = self.rectangle
        image = QImage(r.width, r.height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#1b1d23"))
        painter = QPainter(image)
        painter.setPen(QColor("#f2f4f8"))
        painter.drawText(
            QRect(12, 0, 155, r.height),
            Qt.AlignmentFlag.AlignVCenter,
            f"● {format_seconds(seconds)} / {format_seconds(self.max_seconds)}",
        )
        painter.fillRect(QRect(170, 6, 82, 32), QColor("#3a3f49" if enabled else "#282b33"))
        painter.setPen(QColor("#ffffff" if enabled else "#8b93a1"))
        painter.drawText(QRect(170, 6, 82, 32), Qt.AlignmentFlag.AlignCenter, "Стоп")
        painter.end()
        self.surfaces.image(r, image)
        stop = False
        while not self.surfaces.events.empty():
            kind, x, y, button = self.surfaces.events.get()
            if (
                kind == "release"
                and button == 272
                and r.x + 170 <= x < r.right - 8
                and r.y + 6 <= y < r.bottom - 6
            ):
                stop = True
        return stop and enabled

    def close(self):
        self.surfaces.close()

"""Тонкая рамка layer-shell лежит выше полноэкранных окон и снаружи клипа."""

from __future__ import annotations

import ctypes as C
import os
import select
import threading

from .portal import PortalError
from .region import Region
from .wayland import Interface, NativeClient

COLOUR = "#63aaff"
THICKNESS = 2


def border_edges(region: Region, thickness: int = THICKNESS) -> list[Region]:
    r, b = region, thickness
    return [
        Region(r.x - b, r.y - b, r.width + 2 * b, b),
        Region(r.x - b, r.bottom, r.width + 2 * b, b),
        Region(r.x - b, r.y, b, r.height),
        Region(r.right, r.y, b, r.height),
    ]


def visible_border_edges(region: Region, screens: list[Region]) -> list[Region]:
    pieces = []
    for edge in border_edges(region):
        for screen in screens:
            left, top = max(edge.x, screen.x), max(edge.y, screen.y)
            right, bottom = min(edge.right, screen.right), min(edge.bottom, screen.bottom)
            if right > left and bottom > top:
                pieces.append(Region(left, top, right - left, bottom - top))
    return pieces


def desktop_region(region: Region, capture: Region, desktop: Region) -> Region | None:
    if (capture.width, capture.height) != (desktop.width, desktop.height):
        return None  # у выбранного подмножества экранов положение может быть неизвестно
    return Region(
        desktop.x + region.x - capture.x,
        desktop.y + region.y - capture.y,
        region.width,
        region.height,
    )


class Border:
    def __init__(self, region: Region):
        from PySide6.QtGui import QGuiApplication

        self.screens = {
            screen.name(): Region(g.x(), g.y(), g.width(), g.height())
            for screen in QGuiApplication.screens()
            for g in [screen.geometry()]
        }
        self.region = region
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="snapreel-border", daemon=True)
        self.thread.start()
        from .screencast import wait_ready

        try:
            wait_ready(self.ready)
            if self.error:
                raise PortalError(str(self.error)) from self.error
        except BaseException:
            self.close()
            raise

    def _run(self):
        client = None
        try:
            client = LayerClient(self.screens, self.region)
            client.start()
            self.ready.set()
            fd = client.lib.wl_display_get_fd(client.display)
            while not self.stopped.is_set() and not client.cancelled:
                if client.lib.wl_display_dispatch_pending(client.display) < 0:
                    break
                client.lib.wl_display_flush(client.display)
                if select.select([fd], [], [], 0.1)[0]:
                    if client.lib.wl_display_dispatch(client.display) < 0:
                        break
        except Exception as exc:
            self.error = exc
        finally:
            if client:
                client.close()
            self.ready.set()

    def close(self):
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)


class LayerClient(NativeClient):
    def __init__(self, screens: dict[str, Region], region: Region):
        super().__init__(PortalError)
        self.screens = screens
        self.region = region
        self.outputs = {}
        self.output_names = {}
        self.cancelled = False
        self.configured = 0
        self.error = None
        for name in ("compositor", "surface", "region", "output", "shm", "shm_pool", "buffer"):
            setattr(
                self, name + "_interface", Interface.in_dll(self.lib, "wl_" + name + "_interface")
            )
        self.shell = Interface()
        self.layer = Interface()
        self._define(
            self.shell,
            "zwlr_layer_shell_v1",
            1,
            [
                (
                    "get_layer_surface",
                    "no?ous",
                    [self.layer, self.surface_interface, self.output_interface, None, None],
                )
            ],
            [],
        )
        self._define(
            self.layer,
            "zwlr_layer_surface_v1",
            1,
            [
                ("set_size", "uu", [None, None]),
                ("set_anchor", "u", [None]),
                ("set_exclusive_zone", "i", [None]),
                ("set_margin", "iiii", [None, None, None, None]),
                ("set_keyboard_interactivity", "u", [None]),
                ("get_popup", "o", [None]),
                ("ack_configure", "u", [None]),
                ("destroy", "", []),
            ],
            [("configure", "uuu", [None, None, None]), ("closed", "", [])],
        )

    def _bind_global(self, registry, name, interface, version):
        p, u, s = C.c_void_p, C.c_uint32, C.c_char_p
        return self._marshal(
            registry,
            0,
            interface,
            version,
            u(name),
            s(interface.name),
            u(version),
            p(),
        )

    def start(self):
        p, u, i, s = C.c_void_p, C.c_uint32, C.c_int, C.c_char_p
        self.display = self.lib.wl_display_connect(None)
        if not self.display:
            raise PortalError("Нет доступа к Wayland для подсветки области.")
        registry = self._marshal(self.display, 1, self.registry_interface, 1, p())
        self.registry = registry

        def global_(data, proxy, name, interface, version):
            text = interface.decode()
            self.globals[text] = (name, version)
            if text != "wl_output" or version < 4:
                return
            output = self._bind_global(registry, name, self.output_interface, 4)
            self.outputs[name] = output
            self._listen(
                output,
                [
                    ([i, i, i, i, i, s, s, i], lambda *args: None),
                    ([u, i, i, i], lambda *args: None),
                    ([], lambda *args: None),
                    ([i], lambda *args: None),
                    (
                        [s],
                        lambda data, proxy, value: self.output_names.__setitem__(
                            name, value.decode()
                        ),
                    ),
                    ([s], lambda *args: None),
                ],
            )

        self._listen(registry, [([u, s, u], global_), ([u], lambda *args: self._cancel())])
        for _ in range(2):
            if self.lib.wl_display_roundtrip(self.display) < 0:
                raise PortalError("Wayland не ответил на запрос подсветки области.")
        if "zwlr_layer_shell_v1" not in self.globals:
            raise PortalError("Композитор не поддерживает layer-shell для рамки.")
        compositor = self._bind_global(
            registry, self.globals["wl_compositor"][0], self.compositor_interface, 3
        )
        shm = self._bind_global(registry, self.globals["wl_shm"][0], self.shm_interface, 1)
        self._listen(shm, [([u], lambda *args: None)])
        shell = self._bind_global(registry, self.globals["zwlr_layer_shell_v1"][0], self.shell, 1)
        count = 0
        for name, output in self.outputs.items():
            bounds = self.screens.get(self.output_names.get(name))
            if bounds is None:
                continue
            for edge in self.edges(bounds):
                self._surface(compositor, shm, shell, output, edge, bounds)
                count += 1
        if count == 0:
            raise PortalError("Нет места для рамки снаружи выбранной области.")
        if (
            self.lib.wl_display_roundtrip(self.display) < 0
            or self.configured != count
            or self.error
        ):
            raise PortalError(self.error or "Wayland не подтвердил размеры рамки.")
        self.lib.wl_display_flush(self.display)

    def edges(self, bounds):
        return visible_border_edges(self.region, [bounds])

    def interactive(self):
        return False

    def keyboard(self):
        return False

    def paint(self, edge):
        return bytes(
            (C.c_uint32 * (edge.width * edge.height))(*([0xFF63AAFF] * (edge.width * edge.height)))
        )

    def created(self, surface, edge, shm):
        pass

    def _cancel(self):
        self.cancelled = True

    def _surface(self, compositor, shm, shell, output, edge, bounds):
        p, u, i, s = C.c_void_p, C.c_uint32, C.c_int, C.c_char_p
        surface = self._marshal(compositor, 0, self.surface_interface, 3, p())
        self._listen(surface, [([p], lambda *args: None), ([p], lambda *args: None)])
        if not self.interactive():
            empty = self._marshal(compositor, 1, self.region_interface, 1, p())
            self._marshal(surface, 5, None, 3, p(empty))
        self.created(surface, edge, shm)
        layer = self._marshal(
            shell, 0, self.layer, 1, p(), p(surface), p(output), u(3), s(b"snapreel-border")
        )
        self._marshal(layer, 0, None, 1, u(edge.width), u(edge.height))
        self._marshal(layer, 1, None, 1, u(5))  # верхний левый угол
        self._marshal(layer, 2, None, 1, i(-1))  # не сдвигать окна и не обходить панель
        self._marshal(layer, 3, None, 1, i(edge.y - bounds.y), i(0), i(0), i(edge.x - bounds.x))
        self._marshal(layer, 4, None, 1, u(1 if self.keyboard() else 0))

        def configure(data, proxy, serial, width, height):
            self._marshal(layer, 6, None, 1, u(serial))
            if width != edge.width or height != edge.height:
                self.error = "Композитор изменил размеры рамки — подсветка отключена."
                return
            try:
                self.attach(surface, shm, edge)
                self.configured += 1
            except OSError as exc:
                self.error = str(exc)

        self._listen(layer, [([u, u, u], configure), ([], lambda *args: self._cancel())])
        self._marshal(surface, 6, None, 3)

    def destroy(self, proxy, opcode, version=1):
        self.lib.wl_proxy_marshal_flags(proxy, opcode, None, version, 1)

    def attach(self, surface, shm, edge):
        p, u, i = C.c_void_p, C.c_uint32, C.c_int
        size = edge.width * edge.height * 4
        pixels = self.paint(edge)
        fd = os.memfd_create("snapreel-overlay", os.MFD_CLOEXEC)
        try:
            os.ftruncate(fd, size)
            import mmap

            with mmap.mmap(fd, size) as memory:
                memory[:] = pixels
            pool = self._marshal(shm, 0, self.shm_pool_interface, 1, p(), i(fd), i(size))
            buffer = self._marshal(
                pool,
                0,
                self.buffer_interface,
                1,
                p(),
                i(0),
                i(edge.width),
                i(edge.height),
                i(edge.width * 4),
                u(0),
            )
            self._listen(buffer, [([], lambda *args: self.destroy(buffer, 0))])
            self.destroy(pool, 1)
            self._marshal(surface, 1, None, 3, p(buffer), i(0), i(0))
            self._marshal(surface, 2, None, 3, i(0), i(0), i(edge.width), i(edge.height))
            self._marshal(surface, 6, None, 3)
            self.lib.wl_display_flush(self.display)
        finally:
            os.close(fd)

"""Полноэкранный оверлей выделения области — на Tk, чтобы работал везде."""

from __future__ import annotations

from .errors import OverlayUnavailable, SelectionCancelled
from .platform_info import Environment, Platform, detect, enable_dpi_awareness, virtual_desktop
from .region import Region, from_corners, normalize

__all__ = ["OverlayUnavailable", "SelectionCancelled", "select_region"]

DIM = "#101014"
ACCENT = "#4ea1ff"
HINT = "Выделите область мышью    ·    Esc — отмена"


def _import_tk():
    """tkinter отсутствует в части сборок Python, а без дисплея он не стартует."""
    try:
        import tkinter
    except ImportError as exc:
        raise OverlayUnavailable(
            "нет tkinter — не показать выделение области "
            "(Debian/Ubuntu: sudo apt install python3-tk). "
            "Либо задайте область флагом --region WxH+X+Y."
        ) from exc
    return tkinter


def select_region(env: Environment | None = None, min_side: int = 16) -> Region:
    """Показывает затемнённый оверлей и возвращает выделенный прямоугольник.

    Координаты берутся из `x_root`/`y_root`, то есть в системе виртуального
    рабочего стола — той же, в которой их ждут gdigrab и x11grab.
    """
    env = env or detect()
    enable_dpi_awareness()
    _import_tk()
    desktop = virtual_desktop(env)
    overlay = _Overlay(desktop, env, min_side)
    return overlay.run()


class _Overlay:
    def __init__(self, desktop: Region, env: Environment, min_side: int) -> None:
        self.desktop = desktop
        self.env = env
        self.min_side = min_side
        self.result: Region | None = None
        self.start: tuple[int, int] | None = None

        tk = _import_tk()
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("snapreel")
        self._configure_window()

        self.canvas = tk.Canvas(
            self.root,
            highlightthickness=0,
            bd=0,
            bg=DIM,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)
        self._draw_hint()
        self._bind()

    def _configure_window(self) -> None:
        root = self.root
        root.attributes("-topmost", True)
        if self.env.platform is Platform.MACOS:
            # у Aqua нет надёжного overrideredirect с произвольной геометрией
            root.attributes("-fullscreen", True)
            root.attributes("-alpha", 0.35)
        else:
            root.overrideredirect(True)
            root.geometry(
                f"{self.desktop.width}x{self.desktop.height}+{self.desktop.x}+{self.desktop.y}"
            )
            root.attributes("-alpha", 0.35)
        root.configure(bg=DIM)
        root.focus_force()

    def _draw_hint(self) -> None:
        self.hint_id = self.canvas.create_text(
            self.desktop.width // 2,
            max(40, self.desktop.height // 2 - 40),
            text=HINT,
            fill="#f2f4f8",
            font=("TkDefaultFont", 16),
        )

    def _bind(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda _event: self._cancel())
        self.root.bind("<Button-3>", lambda _event: self._cancel())

    # --- координаты -------------------------------------------------------

    def _to_canvas(self, event) -> tuple[int, int]:
        """Из глобальных координат в координаты холста."""
        return event.x_root - self.desktop.x, event.y_root - self.desktop.y

    # --- события ----------------------------------------------------------

    def _on_press(self, event) -> None:
        self.start = (event.x_root, event.y_root)
        self.canvas.delete("selection")
        if self.hint_id:
            self.canvas.delete(self.hint_id)
            self.hint_id = None

    def _on_drag(self, event) -> None:
        if not self.start:
            return
        x0, y0 = self.start[0] - self.desktop.x, self.start[1] - self.desktop.y
        x1, y1 = self._to_canvas(event)
        self.canvas.delete("selection")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=ACCENT, width=2, tags="selection")
        width, height = abs(x1 - x0), abs(y1 - y0)
        label_x, label_y = min(x0, x1) + 4, min(y0, y1) - 14
        if label_y < 8:
            label_y = min(y0, y1) + 14
        self.canvas.create_text(
            label_x,
            label_y,
            text=f"{width}×{height}",
            fill="#ffffff",
            anchor="w",
            font=("TkDefaultFont", 12, "bold"),
            tags="selection",
        )

    def _on_release(self, event) -> None:
        if not self.start:
            return
        region = from_corners(self.start[0], self.start[1], event.x_root, event.y_root)
        if region.width < self.min_side or region.height < self.min_side:
            # случайный клик — оставляем оверлей открытым
            self.start = None
            self.canvas.delete("selection")
            self._draw_hint()
            return
        self.result = region
        self.root.quit()

    def _cancel(self) -> None:
        self.result = None
        self.root.quit()

    # --- запуск -----------------------------------------------------------

    def run(self) -> Region:
        try:
            self.root.mainloop()
        finally:
            try:
                self.root.destroy()
            except self.tk.TclError:
                pass
        if self.result is None:
            raise SelectionCancelled("выделение отменено")
        return normalize(self.result, self.min_side)

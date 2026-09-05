"""Индикатор записи: рамка вокруг области, таймер и кнопка остановки."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from .platform_info import Environment, Platform, detect
from .region import Region

ACCENT = "#ff4d4f"
PANEL_BG = "#1b1d23"
PANEL_FG = "#f2f4f8"
BORDER = 3
POLL_MS = 100


def format_seconds(value: float) -> str:
    total = int(value)
    return f"{total // 60}:{total % 60:02d}"


class RecordingIndicator:
    """Крутит Tk-цикл, пока идёт запись.

    Рамка собрана из четырёх тонких окон по краям области: так она не
    перекрывает то, что записывается, и не попадает в кадр сама.
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

        self.root = tk.Tk()
        self.root.withdraw()
        self.bars: list[tk.Toplevel] = []
        if self.env.platform is not Platform.MACOS:
            self._build_border()
        self._build_panel()
        self.root.after(POLL_MS, self._tick)

    # --- окна -------------------------------------------------------------

    def _floating(self, width: int, height: int, x: int, y: int, bg: str) -> tk.Toplevel:
        window = tk.Toplevel(self.root, bg=bg)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.geometry(f"{max(width, 1)}x{max(height, 1)}+{x}+{y}")
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
            self.bars.append(self._floating(width, height, x, y, ACCENT))

    def _build_panel(self) -> None:
        panel_w, panel_h = 210, 42
        x = self.region.x
        y = self.region.y - BORDER - panel_h - 6
        if y < 0:
            y = self.region.bottom + BORDER + 6
        self.panel = self._floating(panel_w, panel_h, x, y, PANEL_BG)

        self.dot = tk.Label(self.panel, text="●", fg=ACCENT, bg=PANEL_BG)
        self.dot.pack(side="left", padx=(10, 4))
        self.label = tk.Label(
            self.panel,
            text=f"0:00 / {format_seconds(self.max_seconds)}",
            fg=PANEL_FG,
            bg=PANEL_BG,
            font=("TkDefaultFont", 11, "bold"),
        )
        self.label.pack(side="left")
        self.button = tk.Button(
            self.panel,
            text="Стоп (Esc)",
            command=self._on_stop,
            relief="flat",
            bg="#2c3038",
            fg=PANEL_FG,
            activebackground="#3a3f49",
            activeforeground=PANEL_FG,
            bd=0,
            padx=8,
        )
        self.button.pack(side="right", padx=8, pady=6)

        self.panel.bind("<Escape>", lambda _e: self._on_stop())
        self.root.bind("<Escape>", lambda _e: self._on_stop())
        self.panel.focus_force()

    # --- цикл -------------------------------------------------------------

    def _blink(self, seconds: float) -> None:
        self.dot.configure(fg=ACCENT if int(seconds * 2) % 2 == 0 else PANEL_BG)

    def _tick(self) -> None:
        if self.is_finished():
            self.root.quit()
            return
        seconds = self.elapsed()
        if seconds >= self.max_seconds:
            # бэкенды без собственного лимита (wf-recorder) останавливает этот таймер
            self.request_stop()
            self.root.quit()
            return
        self._blink(seconds)
        self.label.configure(text=f"{format_seconds(seconds)} / {format_seconds(self.max_seconds)}")
        remaining = self.min_seconds - seconds
        if remaining > 0:
            self.button.configure(state="disabled", text=f"Стоп через {int(remaining) + 1}")
        elif self.button["state"] == "disabled":
            self.button.configure(state="normal", text="Стоп (Esc)")
        self.root.after(POLL_MS, self._tick)

    def _on_stop(self) -> None:
        if self.elapsed() < self.min_seconds:
            return
        self.stop_requested = True
        self.request_stop()
        self.root.quit()

    def run(self) -> bool:
        """Возвращает True, если остановил пользователь, а не таймер."""
        try:
            self.root.mainloop()
        finally:
            self.close()
        return self.stop_requested

    def close(self) -> None:
        for window in [*self.bars, getattr(self, "panel", None)]:
            if window is not None:
                try:
                    window.destroy()
                except tk.TclError:
                    pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

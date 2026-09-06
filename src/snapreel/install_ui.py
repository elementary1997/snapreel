"""Окно установки: одно на всю жизнь программы.

Скачанный бинарник спрашивает один раз — куда он ляжет и поднимать ли его
при входе в систему. Дальше он живёт в трее и это окно больше не показывает.

Модуль импортируется лениво, как и остальной GUI: без tkinter установка
делается командой `snapreel install`.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import install, resources, theme
from .config import Config
from .errors import OverlayUnavailable
from .platform_info import Platform, detect, enable_dpi_awareness
from .settings_ui import PAD, _Switch

WIDTH = 380


class InstallWindow:
    """Путь, переключатель автозапуска и две кнопки — больше здесь нечему быть."""

    def __init__(self, config: Config):
        self.plan = install.plan()
        self.installed = False
        self.palette = theme.resolve(getattr(config, "theme", "auto"))

        self.root = tk.Tk()
        self.root.title("snapreel")
        self.fonts = theme.apply(self.root, self.palette)
        self._set_icon()
        self._build()
        self.root.resizable(False, False)

    def run(self) -> bool:
        """True — установили и подняли установленную копию."""
        self.root.mainloop()
        return self.installed

    # --- сборка ----------------------------------------------------------

    def _set_icon(self) -> None:
        try:
            if detect().platform is Platform.WINDOWS:
                path = resources.windows_icon()
                if path is not None:
                    self.root.iconbitmap(default=str(path))
                    return
            png = resources.icon()
            if png is not None:
                self._icon_image = tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, self._icon_image)
        except tk.TclError:
            pass

    def _build(self) -> None:
        frame = ttk.Frame(self.root, style="Card.TFrame", padding=(PAD * 2, PAD * 2))
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text="Установить snapreel",
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frame,
            text="Программа будет лежать здесь и подниматься сама:",
            style="Hint.TLabel",
            wraplength=WIDTH,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, PAD))

        path = tk.Label(
            frame,
            text=str(self.plan.target),
            background=self.palette.field,
            foreground=self.palette.text,
            highlightbackground=self.palette.border,
            highlightthickness=1,
            anchor="w",
            justify="left",
            padx=7,
            pady=4,
            wraplength=WIDTH,
        )
        path.grid(row=2, column=0, columnspan=2, sticky="we")

        self.autostart = _Switch(frame, True, self.palette)
        self.autostart.grid(row=3, column=0, sticky="w", pady=(PAD + 2, 0))
        ttk.Label(frame, text="Запускать при входе в систему", style="Card.TLabel").grid(
            row=3, column=1, sticky="w", padx=(PAD, 0), pady=(PAD + 2, 0)
        )
        frame.columnconfigure(1, weight=1)

        self.status = ttk.Label(frame, text="", style="Hint.TLabel", wraplength=WIDTH)
        self.status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(PAD, 0))

        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(PAD + 2, 0))
        ttk.Button(buttons, text="Не сейчас", style="Card.TButton", command=self.root.destroy).grid(
            row=0, column=0
        )
        self.button = ttk.Button(
            buttons, text="Установить", style="Accent.TButton", command=self._install
        )
        self.button.grid(row=0, column=1, padx=(PAD // 2, 0))

    # --- действие --------------------------------------------------------

    def _install(self) -> None:
        self.button.configure(state="disabled")
        self.status.configure(text="Устанавливаю…", foreground=self.palette.muted)
        self.root.update_idletasks()

        with_autostart = bool(self.autostart.variable.get())
        outcome = install.install(with_autostart=with_autostart)
        if not outcome.ok:
            self.status.configure(text=outcome.message, foreground=self.palette.danger)
            self.button.configure(state="normal")
            return

        self.installed = install.launch(self.plan.target, autostarted=with_autostart)
        if not self.installed:
            # поставили, но поднять не смогли: пусть человек запустит сам,
            # а не гадает, почему иконки нет
            self.status.configure(
                text=f"{outcome.message}. Запустите его из меню «Пуск» или сами.",
                foreground=self.palette.ok,
            )
            self.button.configure(text="Готово", state="normal", command=self.root.destroy)
            return
        self.root.destroy()


def ask_install(config: Config) -> bool:
    """Показывает окно установки. True — установили и подняли новую копию."""
    enable_dpi_awareness()
    try:
        window = InstallWindow(config)
    except tk.TclError as exc:
        raise OverlayUnavailable(
            f"не открыть окно установки: {exc}. Установите командой `snapreel install`."
        ) from exc
    return window.run()

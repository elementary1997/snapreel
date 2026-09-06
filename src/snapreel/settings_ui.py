"""Окно настроек на tkinter.

Единственное место, где настройки меняются без терминала и без правки TOML
руками. Комбинацию тут не печатают, а нажимают, — ради этого окно и затевалось.

Вёрстка плотная намеренно: настроек три десятка, и человек приходит сюда
поменять одну, а не читать. Разделы слева, поля справа, подсказка мелким
шрифтом под полем — и никакого заголовка во весь экран.

Модуль импортируется лениво: tkinter есть не в каждой сборке Python, а
`doctor` и запись по `--region` обязаны работать и без него.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import autostart, resources, settings, theme, updates
from . import config as config_module
from .config import Config
from .errors import OverlayUnavailable
from .platform_info import Platform, detect, enable_dpi_awareness

PAD = 8
LABEL_WIDTH = 21
UPDATE_GROUP = "Обновление"


class _HotkeyEntry(ttk.Frame):
    """Поле, которое запоминает нажатую комбинацию.

    Модификаторы считаются по нажатиям и отпусканиям, а не по битовой маске
    события: маска у каждой оконной системы своя, а имена клавиш одинаковы
    везде, где есть tkinter.
    """

    def __init__(self, master, value: str, font=None, palette: theme.Palette = theme.LIGHT):
        super().__init__(master, style="Card.TFrame")
        self.value = value
        self._font = font
        self._palette = palette
        self._held: list[str] = []
        self._capturing = False

        # Обычный tk.Label, а не ttk: рамку в один пиксель у ttk-подписи не
        # задать, а поле должно выглядеть полем, как соседние строки формы.
        self._label = tk.Label(
            self,
            background=palette.field,
            highlightbackground=palette.border,
            highlightthickness=1,
            anchor="w",
            padx=7,
            pady=3,
        )
        self._label.grid(row=0, column=0, sticky="we")
        self._button = ttk.Button(
            self, text="Изменить", width=10, style="Card.TButton", command=self._start
        )
        self._button.grid(row=0, column=1, padx=(PAD // 2, 0))
        self.columnconfigure(0, weight=1)
        self._show()

    def get(self) -> str:
        return self.value

    def _show(self, text: str | None = None, bad: bool = False) -> None:
        self._label.configure(
            text=text or autostart.describe_safe(self.value),
            font=self._font,
            foreground=self._palette.danger if bad else self._palette.text,
        )

    def _start(self) -> None:
        if self._capturing:
            self._stop()
            return
        self._capturing = True
        self._held = []
        self._show("нажмите комбинацию…")
        self._button.configure(text="Отмена")
        # Клавиши ловятся на всём окне, а не этим полем: подпись фокус
        # клавиатуры не принимает, и <KeyPress> на ней молча не сработает.
        self.winfo_toplevel().bind_all("<KeyPress>", self._press)
        self.winfo_toplevel().bind_all("<KeyRelease>", self._release)

    def _stop(self) -> None:
        self._capturing = False
        self._held = []
        self._button.configure(text="Изменить")
        self.winfo_toplevel().unbind_all("<KeyPress>")
        self.winfo_toplevel().unbind_all("<KeyRelease>")
        self._show()

    def _press(self, event) -> str:
        if not self._capturing:
            return "break"
        modifier = settings.modifier_of(event.keysym)
        if modifier:
            if modifier not in self._held:
                self._held.append(modifier)
            self._show("+".join(self._held) + "+…")
            return "break"
        try:
            self.value = settings.combo(self._held, event.keysym)
        except autostart.HotkeySetupError as exc:
            # частый случай — клавиша без модификатора; объясняем и ждём дальше
            self._show(str(exc).split(".")[0], bad=True)
            return "break"
        self._stop()
        return "break"

    def _release(self, event) -> str:
        modifier = settings.modifier_of(event.keysym)
        if self._capturing and modifier in self._held:
            self._held.remove(modifier)
        return "break"


class _Switch(tk.Canvas):
    """Переключатель вместо галочки.

    Рисуется вручную: у ttk нет ни переключателя, ни возможности собрать его
    из существующих элементов, а галочка `clam` выглядит как из девяностых.
    """

    WIDTH = 36
    HEIGHT = 20

    def __init__(self, master, value: bool, palette: theme.Palette = theme.LIGHT):
        super().__init__(
            master,
            width=self.WIDTH,
            height=self.HEIGHT,
            background=palette.surface,
            highlightthickness=0,
            cursor="hand2",
        )
        self._palette = palette
        self.variable = tk.BooleanVar(value=value)
        self.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _toggle(self, _event=None) -> None:
        self.variable.set(not self.variable.get())

    def _draw(self) -> None:
        self.delete("all")
        on = self.variable.get()
        track = self._palette.accent if on else self._palette.border
        knob = self._palette.surface if on else self._palette.muted
        radius = self.HEIGHT // 2
        # дорожка — прямоугольник с двумя полукружиями по краям
        self.create_oval(0, 0, self.HEIGHT, self.HEIGHT, fill=track, outline=track)
        self.create_oval(
            self.WIDTH - self.HEIGHT, 0, self.WIDTH, self.HEIGHT, fill=track, outline=track
        )
        self.create_rectangle(
            radius, 0, self.WIDTH - radius, self.HEIGHT, fill=track, outline=track
        )
        left = self.WIDTH - self.HEIGHT + 3 if on else 3
        self.create_oval(left, 3, left + self.HEIGHT - 6, self.HEIGHT - 3, fill=knob, outline=knob)


class _DirEntry(ttk.Frame):
    """Строка с путём и кнопкой выбора каталога."""

    def __init__(self, master, value: str):
        super().__init__(master, style="Card.TFrame")
        self.variable = tk.StringVar(value=value)
        ttk.Entry(self, textvariable=self.variable).grid(row=0, column=0, sticky="we")
        ttk.Button(self, text="Обзор", width=8, style="Card.TButton", command=self._pick).grid(
            row=0, column=1, padx=(PAD // 2, 0)
        )
        self.columnconfigure(0, weight=1)

    def get(self) -> str:
        return self.variable.get()

    def _pick(self) -> None:
        start = self.variable.get() or str(Config().resolved_output_dir())
        chosen = filedialog.askdirectory(initialdir=start, title="Куда складывать клипы")
        if chosen:
            self.variable.set(chosen)


class _UpdatePanel(ttk.Frame):
    """Версия, проверка и установка обновления одной кнопкой.

    Сеть и скачивание идут в отдельном потоке, а виджеты трогает только
    главный: Tk не потокобезопасен, поэтому поток лишь складывает сообщения,
    а забирает их таймер окна.
    """

    def __init__(self, master, config_path: Path | None, palette: theme.Palette = theme.LIGHT):
        super().__init__(master, style="Card.TFrame")
        self._config_path = config_path
        self._palette = palette
        self._release = None
        self._mailbox: list[tuple[str, object]] = []
        self.columnconfigure(1, weight=1)

        from . import __version__

        ttk.Label(self, text="Версия", style="Card.TLabel", width=LABEL_WIDTH, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, PAD)
        )
        ttk.Label(self, text=__version__, style="Combo.TLabel").grid(row=0, column=1, sticky="w")

        self.button = ttk.Button(
            self, text="Проверить обновления", style="Card.TButton", command=self.check
        )
        self.button.grid(row=1, column=1, sticky="w", pady=(PAD, 2))
        self.status = ttk.Label(self, text="", style="Hint.TLabel", wraplength=320)
        self.status.grid(row=2, column=1, sticky="w")

        self.after(200, self._drain)

    # --- действия --------------------------------------------------------

    def check(self) -> None:
        self._work("Спрашиваю github…", self._check)

    def install(self) -> None:
        self._work(f"Скачиваю {self._release.name}…", self._install)

    def _work(self, message: str, job) -> None:
        self.button.configure(state="disabled")
        self._say(message)
        threading.Thread(target=job, daemon=True).start()

    def _check(self) -> None:
        try:
            directory = (self._config_path or config_module.config_path()).parent
            release = updates.check(directory, force=True)
        except updates.UpdateError as exc:
            self._post("error", str(exc))
            return
        self._post("checked", release)

    def _install(self) -> None:
        try:
            path = updates.update(self._release, progress=self._progress)
        except updates.UpdateError as exc:
            self._post("error", str(exc))
            return
        self._post("installed", path)

    def _progress(self, done: int, total: int) -> None:
        if total:
            self._post("progress", done * 100 // total)

    # --- обмен с потоком -------------------------------------------------

    def _post(self, kind: str, payload: object) -> None:
        self._mailbox.append((kind, payload))

    def _drain(self) -> None:
        try:
            while self._mailbox:
                kind, payload = self._mailbox.pop(0)
                self._handle(kind, payload)
            self.after(200, self._drain)
        except tk.TclError:
            # окно закрыли, пока поток ещё качал: докладывать больше некому,
            # и это нормальный исход, а не ошибка
            return

    def _handle(self, kind: str, payload) -> None:
        if kind == "progress":
            self._say(f"Скачиваю… {payload}%")
            return
        if kind == "error":
            self._say(str(payload), bad=True)
        elif kind == "checked" and payload is None:
            self._say("Установлена последняя версия", ok=True)
        elif kind == "checked":
            self._release = payload
            self.button.configure(text=f"Обновить до {payload.name}", command=self.install)
            self._say(f"Есть версия {payload.name}")
        elif kind == "installed":
            self._say("Обновлено. Изменения вступят в силу при следующем запуске.", ok=True)
            self.button.configure(text="Проверить обновления", command=self.check)
        self.button.configure(state="normal")

    def _say(self, text: str, ok: bool = False, bad: bool = False) -> None:
        colour = self._palette.ok if ok else self._palette.danger if bad else self._palette.muted
        self.status.configure(text=text, foreground=colour)


class SettingsWindow:
    """Окно целиком: разделы слева, поля справа, сохранение внизу."""

    def __init__(self, config: Config, path: Path | None = None):
        self.config = config
        self.path = path
        self.saved = False
        self._widgets: dict[str, object] = {}
        self._errors: dict[str, ttk.Label] = {}
        self._pages: dict[str, ttk.Frame] = {}
        self._buttons: dict[str, ttk.Button] = {}

        self.root = tk.Tk()
        self.root.title("snapreel")
        self.palette = theme.resolve(getattr(config, "theme", "auto"))
        self.fonts = theme.apply(self.root, self.palette)
        self._set_icon()
        self._build()
        self.root.minsize(600, 420)

    def run(self) -> bool:
        """Показывает окно; True — настройки сохранены."""
        self.root.mainloop()
        return self.saved

    # --- сборка ----------------------------------------------------------

    def _set_icon(self) -> None:
        """Иконка окна: `.ico` на Windows, PNG везде остальное.

        Своей иконки может и не быть — собранный без неё бинарник обязан
        открыть окно так же, просто со значком по умолчанию.
        """
        try:
            if detect().platform is Platform.WINDOWS:
                path = resources.windows_icon()
                if path is not None:
                    self.root.iconbitmap(default=str(path))
                    return
            png = resources.icon()
            if png is not None:
                # ссылку держим сами: Tk не считает её за владение картинкой
                self._icon_image = tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, self._icon_image)
        except tk.TclError:
            pass

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        body = ttk.Frame(self.root, padding=(PAD, PAD, PAD, 0))
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(body, style="Sidebar.TFrame")
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, PAD))

        card = tk.Frame(body, background=self.palette.border)  # рамка в один пиксель
        card.grid(row=0, column=1, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        inner = ttk.Frame(card, style="Card.TFrame", padding=(PAD + 4, PAD + 2))
        inner.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)

        values = settings.values_of(self.config)
        for index, group in enumerate(settings.GROUPS):
            page = ttk.Frame(inner, style="Card.TFrame")
            page.columnconfigure(1, weight=1)
            self._pages[group.title] = page
            for row, field in enumerate(group.fields):
                self._add_field(page, row, field, values[field.name])

            button = ttk.Button(
                sidebar,
                text=group.title,
                style="Side.TButton",
                # ширина по самому длинному названию: обрезанный «Горячие
                # клавиш» — первое, что видит человек, открывший окно
                width=max(len(item.title) for item in settings.GROUPS) + 1,
                command=lambda title=group.title: self._select(title),
            )
            button.grid(row=index, column=0, sticky="we", pady=(0, 1))
            self._buttons[group.title] = button

            if group.title == UPDATE_GROUP:
                self.updates = _UpdatePanel(page, self.path, self.palette)
                self.updates.grid(
                    row=len(group.fields) * 2, column=0, columnspan=2, sticky="we", pady=(PAD, 0)
                )

        self._inner = inner
        self._select(settings.GROUPS[0].title)

        footer = ttk.Frame(self.root, padding=(PAD, PAD, PAD, PAD))
        footer.grid(row=1, column=0, sticky="we")
        footer.columnconfigure(0, weight=1)
        self._status = ttk.Label(footer, text="", style="Status.TLabel", wraplength=380)
        self._status.grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Закрыть", command=self.root.destroy).grid(row=0, column=1)
        ttk.Button(footer, text="Сохранить", style="Accent.TButton", command=self._save).grid(
            row=0, column=2, padx=(PAD // 2, 0)
        )

    def _select(self, title: str) -> None:
        for name, page in self._pages.items():
            page.grid_forget() if name != title else page.grid(row=0, column=0, sticky="nsew")
        for name, button in self._buttons.items():
            button.configure(style="SideActive.TButton" if name == title else "Side.TButton")

    def _add_field(self, page, row: int, field: settings.Field, value) -> None:
        line = row * 2
        ttk.Label(page, text=field.label, style="Card.TLabel", width=LABEL_WIDTH, anchor="w").grid(
            row=line, column=0, sticky="w", pady=(0, 1), padx=(0, PAD)
        )

        if field.kind == "hotkey":
            widget = _HotkeyEntry(page, str(value), self.fonts["mono"], self.palette)
        elif field.kind == "bool":
            widget = _Switch(page, bool(value), self.palette)
        elif field.kind == "choice":
            variable = tk.StringVar(value=str(value))
            widget = ttk.Combobox(
                page,
                textvariable=variable,
                values=list(field.choices),
                state="readonly",
                width=16,
            )
            widget.variable = variable
        elif field.kind == "dir":
            widget = _DirEntry(page, str(value))
        else:
            variable = tk.StringVar(value=str(value))
            widget = ttk.Entry(page, textvariable=variable)
            widget.variable = variable

        sticky = "w" if field.kind in ("bool", "choice") else "we"
        widget.grid(row=line, column=1, sticky=sticky, pady=(0, 1))
        self._widgets[field.name] = widget

        # Подсказка живёт под полем и там же показывается ошибка. Пустую
        # строку не резервируем: полей три десятка, и пустые полосы между
        # ними — это ещё один экран прокрутки на ровном месте.
        note = ttk.Label(page, text=field.hint, style="Hint.TLabel", wraplength=330)
        note.grid(row=line + 1, column=1, sticky="w", pady=(0, PAD if field.hint else PAD // 2))
        self._errors[field.name] = note

    # --- сохранение ------------------------------------------------------

    def _collect(self) -> dict[str, object]:
        raw: dict[str, object] = {}
        for name, widget in self._widgets.items():
            if isinstance(widget, (_HotkeyEntry, _DirEntry)):
                raw[name] = widget.get()
            else:
                raw[name] = widget.variable.get()
        return raw

    def _save(self) -> None:
        config, errors = settings.parse(self._collect(), self.config)
        self._show_errors(errors)
        if errors:
            self._tell("Не сохранено: поправьте отмеченное красным.", bad=True)
            self._select(_first_group_with(errors))
            return

        try:
            config_module.save(config, self.path)
        except OSError as exc:
            self._tell(f"Не записать конфиг: {exc}", bad=True)
            return

        self.config = config
        self.saved = True
        self._tell(f"Сохранено. {self._apply_hotkey(config)}", ok=True)

    def _apply_hotkey(self, config: Config) -> str:
        """Хоткей ставится системой и не везде автоматически — так и говорим."""
        try:
            return autostart.install(config.hotkey_mp4).message
        except autostart.HotkeySetupError as exc:
            return f"Хоткей назначить не вышло: {exc}"

    def _tell(self, text: str, ok: bool = False, bad: bool = False) -> None:
        style = "StatusOk.TLabel" if ok else "StatusBad.TLabel" if bad else "Status.TLabel"
        self._status.configure(text=text, style=style)

    def _show_errors(self, errors: dict[str, str]) -> None:
        for field in settings.FIELDS:
            note = self._errors[field.name]
            if field.name in errors:
                note.configure(text=errors[field.name], style="Error.TLabel")
            else:
                note.configure(text=field.hint, style="Hint.TLabel")


def _first_group_with(errors: dict[str, str]) -> str:
    """Раздел, который надо показать: ошибку прячет тот, кто её не открывает."""
    for group in settings.GROUPS:
        if any(field.name in errors for field in group.fields):
            return group.title
    return settings.GROUPS[0].title


def open_settings(config: Config, path: Path | None = None) -> bool:
    """Показывает окно настроек. True — пользователь сохранил изменения."""
    enable_dpi_awareness()  # иначе на Windows со масштабом 125% окно будет мыльным
    try:
        window = SettingsWindow(config, path)
    except tk.TclError as exc:
        raise OverlayUnavailable(
            f"не открыть окно настроек: {exc}. Настройте через `snapreel hotkey set` "
            "или правкой конфига."
        ) from exc
    return window.run()

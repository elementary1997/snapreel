"""Оформление окон snapreel: палитры, шрифты и стили ttk.

Палитры две, светлая и тёмная, и по умолчанию берётся та, в которой сидит
система: светлое окно посреди тёмного рабочего стола выглядит чужой
программой. О теме спрашивается `platform_info.prefers_dark` — сам tkinter
про системную тему ничего не знает.

За основу берётся `clam` — единственная встроенная тема ttk, которой можно
задать цвета целиком. Родные `vista` и `aqua` красивее по-своему, но
перекрасить их нельзя, и окно выглядело бы в трёх системах тремя разными
приложениями.

Сам tkinter подтягивается внутри функций: за палитрой сюда ходит и трей, у
которого своя оконная система, и модуль обязан импортироваться там, где
tkinter не собран.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .platform_info import Environment, prefers_dark

if TYPE_CHECKING:
    from tkinter import font as tkfont


@dataclass(frozen=True)
class Palette:
    """Цвета окна. Имена — по роли, а не по оттенку: роли у тем общие."""

    bg: str  # фон окна и колонки разделов
    surface: str  # карточка с полями
    field: str  # поле ввода
    border: str
    text: str
    muted: str  # подписи и подсказки
    accent: str
    accent_hover: str
    on_accent: str  # текст на акцентной кнопке
    danger: str
    ok: str
    button: str
    button_hover: str
    hover: str  # подсветка строки в списке разделов
    dark: bool


LIGHT = Palette(
    bg="#f3f5f8",
    surface="#ffffff",
    field="#ffffff",
    border="#ccd4e0",
    text="#1b2028",
    muted="#6b7684",
    accent="#2f6feb",
    accent_hover="#255ac7",
    on_accent="#ffffff",
    danger="#c62828",
    ok="#1b7f4b",
    button="#eaeef5",
    button_hover="#dde4ee",
    hover="#e7ecf4",
    dark=False,
)

DARK = Palette(
    bg="#1c1e24",
    surface="#24272f",
    field="#1b1e25",
    border="#353a46",
    text="#e7eaf0",
    muted="#98a1b0",
    accent="#4c8dff",
    accent_hover="#6ba0ff",
    on_accent="#0f1116",
    danger="#ff6b6b",
    ok="#4ade80",
    button="#2c313c",
    button_hover="#343a47",
    hover="#2a2f3a",
    dark=True,
)

# Шрифты перечислены по предпочтению, а не по платформам: берётся первый
# установленный. Спрашивать систему через `platform` здесь незачем — важно
# наличие шрифта, а не имя ОС.
_FAMILIES = (
    "Segoe UI Variable Text",
    "Segoe UI",
    "SF Pro Text",
    "Helvetica Neue",
    "Inter",
    "Cantarell",
    "Ubuntu",
    "Noto Sans",
    "DejaVu Sans",
)


def resolve(mode: str = "auto", env: Environment | None = None) -> Palette:
    """Палитра по настройке: `auto` спрашивает систему, остальное — приказ."""
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return DARK if prefers_dark(env) else LIGHT


def family(root) -> str:
    from tkinter import font as tkfont

    available = set(tkfont.families(root))
    for name in _FAMILIES:
        if name in available:
            return name
    return str(tkfont.nametofont("TkDefaultFont").cget("family"))


def apply(root, palette: Palette = LIGHT) -> dict[str, tkfont.Font]:
    """Красит окно и возвращает шрифты, которые пригодятся при вёрстке."""
    from tkinter import font as tkfont
    from tkinter import ttk

    name = family(root)
    fonts = {
        "base": tkfont.Font(root=root, family=name, size=9),
        "hint": tkfont.Font(root=root, family=name, size=8),
        "title": tkfont.Font(root=root, family=name, size=11, weight="bold"),
        "section": tkfont.Font(root=root, family=name, size=9, weight="bold"),
        "mono": tkfont.Font(root=root, family=name, size=9, weight="bold"),
    }

    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=palette.bg)

    style.configure(".", background=palette.bg, foreground=palette.text, font=fonts["base"])
    style.configure("TFrame", background=palette.bg)
    style.configure("Card.TFrame", background=palette.surface)
    style.configure("Sidebar.TFrame", background=palette.bg)

    style.configure("TLabel", background=palette.bg, foreground=palette.text)
    style.configure("Card.TLabel", background=palette.surface, foreground=palette.text)
    style.configure(
        "Hint.TLabel", background=palette.surface, foreground=palette.muted, font=fonts["hint"]
    )
    style.configure(
        "Error.TLabel", background=palette.surface, foreground=palette.danger, font=fonts["hint"]
    )
    style.configure(
        "Title.TLabel", background=palette.bg, foreground=palette.text, font=fonts["title"]
    )
    style.configure(
        "CardTitle.TLabel",
        background=palette.surface,
        foreground=palette.text,
        font=fonts["title"],
    )
    style.configure(
        "Status.TLabel", background=palette.bg, foreground=palette.muted, font=fonts["hint"]
    )
    style.configure(
        "StatusOk.TLabel", background=palette.bg, foreground=palette.ok, font=fonts["hint"]
    )
    style.configure(
        "StatusBad.TLabel", background=palette.bg, foreground=palette.danger, font=fonts["hint"]
    )
    style.configure(
        "Combo.TLabel", background=palette.surface, foreground=palette.text, font=fonts["mono"]
    )

    # Плоские поля: рамка в один цвет вместо вдавленного бордюра `clam`
    style.configure(
        "TEntry",
        fieldbackground=palette.field,
        background=palette.field,
        foreground=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        insertcolor=palette.text,
        padding=4,
        relief="flat",
    )
    style.map(
        "TEntry", bordercolor=[("focus", palette.accent)], lightcolor=[("focus", palette.accent)]
    )

    style.configure(
        "TCombobox",
        fieldbackground=palette.field,
        background=palette.button,
        foreground=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        arrowcolor=palette.muted,
        selectbackground=palette.field,
        selectforeground=palette.text,
        padding=3,
    )
    # `clam` держит для readonly свою карту цветов, и без перекрытия поле
    # выбора остаётся светло-бежевым — в тёмной теме на нём не прочитать текст
    style.map(
        "TCombobox",
        bordercolor=[("focus", palette.accent)],
        fieldbackground=[("readonly", palette.field), ("disabled", palette.bg)],
        foreground=[("readonly", palette.text), ("disabled", palette.muted)],
        background=[("readonly", palette.button), ("active", palette.button_hover)],
        selectbackground=[("readonly", palette.field)],
        selectforeground=[("readonly", palette.text)],
        arrowcolor=[("readonly", palette.muted)],
    )
    # выпадающий список у комбобокса — не ttk-виджет, красится опциями Tk
    root.option_add("*TCombobox*Listbox.background", palette.field)
    root.option_add("*TCombobox*Listbox.foreground", palette.text)
    root.option_add("*TCombobox*Listbox.selectBackground", palette.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", palette.on_accent)

    style.configure(
        "TButton",
        background=palette.button,
        foreground=palette.text,
        bordercolor=palette.border,
        # светлая и тёмная грани тоже красятся в цвет рамки: иначе на карточке
        # кнопка сливается с фоном и выглядит просто текстом
        lightcolor=palette.border,
        darkcolor=palette.border,
        focuscolor=palette.border,
        padding=(10, 4),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", palette.border), ("active", palette.button_hover)],
        bordercolor=[("active", palette.accent)],
    )

    # Кнопка внутри карточки: роль та же, фон под ней другой
    style.configure(
        "Card.TButton",
        background=palette.button,
        foreground=palette.text,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        focuscolor=palette.border,
        padding=(8, 3),
        relief="flat",
    )
    style.map(
        "Card.TButton",
        background=[("pressed", palette.border), ("active", palette.button_hover)],
        bordercolor=[("active", palette.accent)],
    )

    style.configure(
        "Accent.TButton",
        background=palette.accent,
        foreground=palette.on_accent,
        bordercolor=palette.accent,
        lightcolor=palette.accent,
        darkcolor=palette.accent,
        focuscolor=palette.accent,
        padding=(12, 4),
        relief="flat",
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", palette.accent_hover), ("active", palette.accent_hover)],
        bordercolor=[("active", palette.accent_hover)],
        foreground=[("disabled", palette.muted)],
    )

    # Разделы слева: строка без рамки, выбранная подсвечена цветом карточки
    style.configure(
        "Side.TButton",
        background=palette.bg,
        foreground=palette.muted,
        bordercolor=palette.bg,
        lightcolor=palette.bg,
        darkcolor=palette.bg,
        focuscolor=palette.bg,
        padding=(10, 5),
        anchor="w",
        relief="flat",
    )
    style.map(
        "Side.TButton",
        background=[("active", palette.hover)],
        foreground=[("active", palette.text)],
    )
    style.configure(
        "SideActive.TButton",
        background=palette.surface,
        foreground=palette.accent,
        bordercolor=palette.surface,
        lightcolor=palette.surface,
        darkcolor=palette.surface,
        focuscolor=palette.surface,
        padding=(10, 5),
        anchor="w",
        font=fonts["section"],
        relief="flat",
    )
    style.map("SideActive.TButton", background=[("active", palette.surface)])

    style.configure("Card.TSeparator", background=palette.border)
    return fonts

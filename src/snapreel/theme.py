"""Оформление окон snapreel: палитра, шрифты и стили ttk.

Вид одинаков на всех платформах и не зависит от системной темы: за основу
берётся `clam` — единственная встроенная тема ttk, которой можно задать цвета
целиком. Родные `vista` и `aqua` красивее по-своему, но перекрасить их нельзя,
и окно выглядело бы в трёх системах тремя разными приложениями.
"""

from __future__ import annotations

from tkinter import font as tkfont
from tkinter import ttk

# Светлая палитра: спокойный фон, белые карточки, один акцентный цвет.
BG = "#eef1f5"
SURFACE = "#ffffff"
BORDER = "#d9dee5"
TEXT = "#1b2028"
MUTED = "#6b7684"
ACCENT = "#2f6feb"
ACCENT_HOVER = "#255ac7"
DANGER = "#c62828"
OK = "#1b7f4b"

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


def family(root) -> str:
    available = set(tkfont.families(root))
    for name in _FAMILIES:
        if name in available:
            return name
    return str(tkfont.nametofont("TkDefaultFont").cget("family"))


def apply(root) -> dict[str, tkfont.Font]:
    """Красит окно и возвращает шрифты, которые пригодятся при вёрстке."""
    name = family(root)
    fonts = {
        "base": tkfont.Font(root=root, family=name, size=10),
        "hint": tkfont.Font(root=root, family=name, size=9),
        "title": tkfont.Font(root=root, family=name, size=15, weight="bold"),
        "section": tkfont.Font(root=root, family=name, size=10, weight="bold"),
        "mono": tkfont.Font(root=root, family=name, size=10, weight="bold"),
    }

    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=BG)

    style.configure(".", background=BG, foreground=TEXT, font=fonts["base"])
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("Sidebar.TFrame", background=BG)

    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Hint.TLabel", background=SURFACE, foreground=MUTED, font=fonts["hint"])
    style.configure("Error.TLabel", background=SURFACE, foreground=DANGER, font=fonts["hint"])
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=fonts["title"])
    style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=fonts["hint"])
    style.configure("Status.TLabel", background=BG, foreground=MUTED, font=fonts["hint"])
    style.configure("StatusOk.TLabel", background=BG, foreground=OK, font=fonts["hint"])
    style.configure("StatusBad.TLabel", background=BG, foreground=DANGER, font=fonts["hint"])
    style.configure("Combo.TLabel", background=SURFACE, foreground=TEXT, font=fonts["mono"])

    # Плоские поля: рамка в один цвет вместо вдавленного бордюра `clam`
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        insertcolor=TEXT,
        padding=6,
        relief="flat",
    )
    style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])

    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        arrowcolor=MUTED,
        padding=5,
    )
    style.map("TCombobox", bordercolor=[("focus", ACCENT)])

    style.configure(
        "TButton",
        background=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        # светлая и тёмная грани тоже красятся в цвет рамки: иначе на белой
        # карточке кнопка сливается с фоном и выглядит просто текстом
        lightcolor=BORDER,
        darkcolor=BORDER,
        focuscolor=BORDER,
        padding=(14, 7),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", BG), ("active", "#f3f5f8")],
        bordercolor=[("active", ACCENT)],
    )

    # Кнопка внутри белой карточки: на белом фоне белая кнопка читается как
    # текст, поэтому у неё своя, чуть притенённая заливка
    style.configure(
        "Card.TButton",
        background="#f1f4f8",
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        focuscolor=BORDER,
        padding=(12, 6),
        relief="flat",
    )
    style.map(
        "Card.TButton",
        background=[("pressed", "#e2e8f0"), ("active", "#e8edf4")],
        bordercolor=[("active", ACCENT)],
    )

    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="#ffffff",
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        focuscolor=ACCENT,
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", ACCENT_HOVER), ("active", ACCENT_HOVER)],
        bordercolor=[("active", ACCENT_HOVER)],
        foreground=[("disabled", "#e6ecfa")],
    )

    # Разделы слева: кнопка без рамки, выбранная подсвечена акцентом
    style.configure(
        "Side.TButton",
        background=BG,
        foreground=MUTED,
        bordercolor=BG,
        lightcolor=BG,
        darkcolor=BG,
        focuscolor=BG,
        padding=(12, 9),
        anchor="w",
        relief="flat",
    )
    style.map("Side.TButton", background=[("active", "#e3e8ef")], foreground=[("active", TEXT)])
    style.configure(
        "SideActive.TButton",
        parent="Side.TButton",
        background=SURFACE,
        foreground=ACCENT,
        bordercolor=SURFACE,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        focuscolor=SURFACE,
        padding=(12, 9),
        anchor="w",
        font=fonts["section"],
        relief="flat",
    )
    style.map("SideActive.TButton", background=[("active", SURFACE)])

    style.configure(
        "TCheckbutton",
        background=SURFACE,
        foreground=TEXT,
        indicatorbackground=SURFACE,
        indicatorforeground=ACCENT,
        bordercolor=BORDER,
        focuscolor=SURFACE,
        padding=2,
    )
    style.map(
        "TCheckbutton",
        indicatorbackground=[("selected", ACCENT), ("active", SURFACE)],
        indicatorforeground=[("selected", "#ffffff")],
    )

    style.configure("Card.TSeparator", background=BORDER)
    return fonts

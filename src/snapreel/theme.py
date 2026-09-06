"""Оформление окон snapreel: палитры и таблица стилей Qt.

Палитры две, светлая и тёмная, и по умолчанию берётся та, в которой сидит
система: светлое окно посреди тёмного рабочего стола выглядит чужой
программой. Тему спрашивает `platform_info.prefers_dark`.

Вид задаётся таблицей стилей (QSS) целиком — от скруглений до цвета рамки в
фокусе, — чтобы на всех трёх системах окно выглядело одинаково и предсказуемо.
Сам Qt сюда не импортируется на уровне модуля: за палитрой ходят и тесты, и
трей, а таблица стилей — обычная строка.
"""

from __future__ import annotations

from dataclasses import dataclass

from .platform_info import Environment, prefers_dark


@dataclass(frozen=True)
class Palette:
    """Цвета окна. Имена — по роли, а не по оттенку: роли у тем общие."""

    bg: str  # фон окна
    surface: str  # панель с содержимым
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
    hover: str  # подсветка строки под курсором
    dark: bool


LIGHT = Palette(
    bg="#f5f6f8",
    surface="#ffffff",
    field="#ffffff",
    border="#e2e6ec",
    text="#12151a",
    muted="#6b7480",
    accent="#2f6feb",
    accent_hover="#255ac7",
    on_accent="#ffffff",
    danger="#d13b3b",
    ok="#177245",
    button="#eef1f6",
    button_hover="#e2e7ef",
    hover="#eef1f6",
    dark=False,
)

DARK = Palette(
    bg="#17191d",
    surface="#1f2228",
    field="#252931",
    border="#2e333c",
    text="#eceef2",
    muted="#98a0ad",
    accent="#4c8dff",
    accent_hover="#6ba0ff",
    on_accent="#0c0e12",
    danger="#ff6b6b",
    ok="#4ade80",
    button="#282d36",
    button_hover="#313742",
    hover="#232830",
    dark=True,
)

# Шрифт выбирается по наличию, а не по имени системы: важно, что он есть.
FAMILIES = (
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

RADIUS = 8  # скругление кнопок, полей и строк списка


def resolve(mode: str = "auto", env: Environment | None = None) -> Palette:
    """Палитра по настройке: `auto` спрашивает систему, остальное — приказ."""
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return DARK if prefers_dark(env) else LIGHT


def stylesheet(palette: Palette) -> str:
    """Вид всего окна одной таблицей — так его можно прочитать целиком."""
    return f"""
    QWidget {{
        background: {palette.bg};
        color: {palette.text};
        font-size: 10pt;
    }}
    /* подписи не красят фон: они лежат и на окне, и на панели, и на строке */
    QLabel {{ background: transparent; }}
    QLabel[role="title"] {{ font-size: 14pt; font-weight: 600; padding-bottom: 2px; }}
    QLabel[role="hint"] {{ color: {palette.muted}; font-size: 9pt; }}
    QLabel[role="error"] {{ color: {palette.danger}; font-size: 9pt; }}
    QLabel[role="ok"] {{ color: {palette.ok}; font-size: 9pt; }}
    QLabel[role="value"] {{ color: {palette.muted}; font-size: 9pt; }}

    QFrame#Sidebar {{ background: {palette.bg}; border: none; }}
    QFrame#Panel {{
        background: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: {RADIUS + 2}px;
    }}
    QFrame#Divider {{ background: {palette.border}; border: none; max-height: 1px; }}

    QListWidget {{
        background: {palette.bg};
        border: none;
        outline: none;
        padding: 2px;
    }}
    QListWidget::item {{
        padding: 7px 10px;
        border-radius: {RADIUS}px;
        color: {palette.muted};
    }}
    QListWidget::item:hover {{ background: {palette.hover}; color: {palette.text}; }}
    QListWidget::item:selected {{
        background: {palette.surface};
        color: {palette.accent};
        font-weight: 600;
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {palette.field};
        border: 1px solid {palette.border};
        border-radius: {RADIUS - 2}px;
        padding: 5px 8px;
        min-height: 20px;
        selection-background-color: {palette.accent};
        selection-color: {palette.on_accent};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {palette.accent};
    }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: {RADIUS - 2}px;
        padding: 4px;
        selection-background-color: {palette.accent};
        selection-color: {palette.on_accent};
        outline: none;
    }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; }}

    QPushButton {{
        background: {palette.button};
        border: 1px solid {palette.border};
        border-radius: {RADIUS}px;
        padding: 6px 14px;
        min-height: 20px;
    }}
    QPushButton:hover {{ background: {palette.button_hover}; }}
    QPushButton:pressed {{ background: {palette.border}; }}
    QPushButton:disabled {{ color: {palette.muted}; }}
    QPushButton[role="accent"] {{
        background: {palette.accent};
        border-color: {palette.accent};
        color: {palette.on_accent};
        font-weight: 600;
    }}
    QPushButton[role="accent"]:hover {{
        background: {palette.accent_hover};
        border-color: {palette.accent_hover};
    }}
    QPushButton[role="quiet"] {{ background: transparent; border-color: transparent; }}
    QPushButton[role="quiet"]:hover {{ background: {palette.hover}; }}

    QScrollArea {{ background: {palette.surface}; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: {palette.surface}; }}
    QWidget#Page, QWidget#Page > QWidget {{ background: {palette.surface}; }}
    QWidget#Row {{ background: {palette.surface}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {palette.border};
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {palette.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QToolTip {{
        background: {palette.surface};
        color: {palette.text};
        border: 1px solid {palette.border};
        padding: 4px 6px;
    }}
    """


def apply(app, palette: Palette) -> None:
    """Красит приложение целиком и выбирает первый установленный шрифт."""
    from PySide6.QtGui import QFont, QFontDatabase

    available = set(QFontDatabase.families())
    for name in FAMILIES:
        if name in available:
            app.setFont(QFont(name, 10))
            break
    app.setStyle("Fusion")  # единственный стиль Qt, который красится целиком
    app.setStyleSheet(stylesheet(palette))

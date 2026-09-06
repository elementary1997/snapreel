"""Файлы, лежащие рядом с кодом: сейчас это иконки приложения.

Ищутся в двух местах: рядом с модулем при обычной установке и в каталоге,
куда PyInstaller распаковал приложенное, — в собранном бинарнике никакого
`snapreel/assets` на диске нет.

Отсутствие иконки не ошибка: окно и трей обязаны подняться и без неё.
"""

from __future__ import annotations

from pathlib import Path

from . import bundled

ASSETS = "assets"


def _find(name: str) -> Path | None:
    candidates = [Path(__file__).resolve().parent / ASSETS / name]
    base = bundled.root()
    if base is not None:
        # так их кладёт snapreel.spec: путь внутри бандла повторяет пакет
        candidates.append(base / "snapreel" / ASSETS / name)
        candidates.append(base / ASSETS / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def icon(recording: bool = False) -> Path | None:
    """PNG иконки: обычная и красная, которой трей показывает запись."""
    return _find("icon-recording.png" if recording else "icon.png")


def windows_icon() -> Path | None:
    """ICO со всеми размерами: её просит окно на Windows и сам exe."""
    return _find("icon.ico")

"""Утилиты, вшитые в собранный бинарник.

PyInstaller распаковывает приложенные файлы во временный каталог и кладёт
путь к нему в `sys._MEIPASS`. Из исходников snapreel запускается без всякого
бандла, и тогда здесь пусто — значит ffmpeg берётся из PATH, как и раньше.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def root() -> Path | None:
    """Каталог с вшитыми утилитами; None — запуск не из собранного бинарника."""
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def binary(name: str) -> str | None:
    """Путь к вшитой утилите или None, если её не приложили.

    Имя проверяется с расширением и без: платформу здесь спрашивать незачем,
    а `.exe` есть только у windows-сборки.
    """
    base = root()
    if base is None:
        return None
    for candidate in (base / name, base / f"{name}.exe"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None

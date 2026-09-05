"""Куда класть клипы, как их называть и когда убирать старые."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from . import naming
from .config import Config


def new_path(config: Config, suffix: str = ".mp4", now: datetime | None = None) -> Path:
    """Свободный путь для нового клипа; при совпадении имён добавляется счётчик."""
    directory = config.resolved_output_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stem = (now or datetime.now()).strftime(config.filename_template)
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def prune(config: Config, now: float | None = None) -> list[Path]:
    """Удаляет собственные клипы старше keep_days. Ноль означает «хранить всё».

    Чужие файлы в каталоге не трогает, даже если они видео: имя обязано
    совпасть с шаблоном, по которому snapreel сам называет записи.
    """
    if config.keep_days <= 0:
        return []
    directory = config.resolved_output_dir()
    if not directory.is_dir():
        return []
    pattern = naming.pattern(config.filename_template)
    cutoff = (now or time.time()) - config.keep_days * 86400
    removed: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file() or not pattern.fullmatch(path.name):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except OSError:
            continue
    return removed

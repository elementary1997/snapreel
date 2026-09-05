"""Куда класть клипы, как их называть и когда убирать старые."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

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
    """Удаляет клипы старше keep_days. Ноль означает «хранить всё»."""
    if config.keep_days <= 0:
        return []
    directory = config.resolved_output_dir()
    if not directory.is_dir():
        return []
    cutoff = (now or time.time()) - config.keep_days * 86400
    removed: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in (".mp4", ".gif"):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except OSError:
            continue
    return removed

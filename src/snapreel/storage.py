"""Куда класть клипы, как их называть и когда убирать старые."""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from .config import Config

SUFFIXES = (".mp4", ".gif")

# во что превращаются директивы strftime, когда из шаблона имени строится
# регулярное выражение для обратного узнавания «своих» файлов
_STRFTIME_TO_REGEX = {
    "%Y": r"\d{4}",
    "%y": r"\d{2}",
    "%m": r"\d{2}",
    "%d": r"\d{2}",
    "%H": r"\d{2}",
    "%I": r"\d{2}",
    "%M": r"\d{2}",
    "%S": r"\d{2}",
    "%j": r"\d{3}",
    "%f": r"\d+",
    "%p": r"\w+",
    "%a": r"\w+",
    "%A": r"\w+",
    "%b": r"\w+",
    "%B": r"\w+",
    "%Z": r"\w*",
    "%z": r"[+-]\d{4}",
    "%%": r"%",
}


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


def name_pattern(template: str) -> re.Pattern[str]:
    """Регулярка, узнающая имена, которые породил бы `new_path` с этим шаблоном.

    Нужна, чтобы `prune` трогал только свои клипы: каталог вывода пользователь
    вправе назначить любой, в том числе общий `~/Videos` с чужими записями.
    """
    parts: list[str] = []
    index = 0
    while index < len(template):
        token = template[index : index + 2]
        if token in _STRFTIME_TO_REGEX:
            parts.append(_STRFTIME_TO_REGEX[token])
            index += 2
            continue
        if template[index] == "%" and index + 1 < len(template):
            # неизвестная директива: считаем её непустой последовательностью без разделителей
            parts.append(r"[^/\\]+")
            index += 2
            continue
        parts.append(re.escape(template[index]))
        index += 1
    suffixes = "|".join(suffix.lstrip(".") for suffix in SUFFIXES)
    return re.compile(rf"^{''.join(parts)}(-\d+)?\.({suffixes})$")


def is_ours(name: str, config: Config) -> bool:
    return bool(name_pattern(config.filename_template).fullmatch(name))


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
    pattern = name_pattern(config.filename_template)
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

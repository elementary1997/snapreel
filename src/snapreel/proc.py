"""Запуск внешних процессов — единственное место на весь пакет.

Причина в Windows. Релизный exe собран оконным: своей консоли у него нет, и
каждый консольный ребёнок — ffmpeg, ffprobe, powershell, cmd — заводит себе
новую. Человек видит, как поверх работы на секунду выскакивает чёрное окно:
после каждой записи, при открытии настроек, при каждом уведомлении. Флаг
`CREATE_NO_WINDOW` это снимает, но добавлять его в каждый вызов вручную
бессмысленно — забытый всплывёт не на тестах, а у человека на экране.
Поэтому `subprocess.run` и `subprocess.Popen` в пакете зовутся только
отсюда, а тест это проверяет.

На macOS и Linux флага нет, и обёртки просто передают вызов дальше.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any


def hidden() -> dict[str, Any]:
    """Аргументы запуска, при которых окна консоли не будет."""
    flag = getattr(subprocess, "CREATE_NO_WINDOW", None)
    return {"creationflags": flag} if flag is not None else {}


def run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run` без мигающей консоли."""
    return subprocess.run(list(command), **_merged(kwargs))


def popen(command: Sequence[str], **kwargs: Any) -> subprocess.Popen:
    """`subprocess.Popen` без мигающей консоли."""
    return subprocess.Popen(list(command), **_merged(kwargs))


def _merged(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Флаги вызывающего не теряются: наш добавляется к ним, а не вместо них."""
    flags = kwargs.pop("creationflags", 0)
    options = hidden()
    if "creationflags" in options:
        options["creationflags"] |= flags
    elif flags:
        options["creationflags"] = flags
    return {**kwargs, **options}

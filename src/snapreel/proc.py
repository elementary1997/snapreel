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

import base64
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


def powershell(script: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """Выполняет скрипт PowerShell, передавая его в кодировке, а не как текст.

    Обычной командной строкой (`-Command`) скрипт до PowerShell не доезжает:
    её он читает в кодировке ANSI системы, и на английской Windows кириллица
    превращается в «?». Это не косметика — в имени ярлыка автозапуска есть
    русское слово, а «?» Windows в именах файлов не разрешает, и автозапуск
    не прописывался вовсе: `Unable to save shortcut ... (????).lnk`. Нашлось
    приёмкой на раннере с английской системой; на русской всё работало.

    `-EncodedCommand` берёт тот же скрипт в base64 от UTF-16LE: по командной
    строке едет один ASCII, а PowerShell разбирает его обратно сам. Файлом
    отдавать нельзя, хоть это и напрашивается: у `-File` другой код возврата
    — при ошибке внутри скрипта он остаётся нулевым, и сорвавшаяся запись
    ярлыка выглядела бы успехом; плюс запуск файла упирается в политику
    выполнения скриптов, которую групповая политика может запретить совсем.

    Ограничение — длина командной строки (около 32 тысяч знаков): скрипты
    здесь на порядки короче, но бесконечно длинный так не передать.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )

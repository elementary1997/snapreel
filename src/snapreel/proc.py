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

import os
import subprocess
import tempfile
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
    """Выполняет скрипт PowerShell, передавая его файлом, а не строкой.

    Через `-Command` скрипт уезжает командной строкой, а её PowerShell читает
    в кодировке ANSI системы: на английской Windows кириллица превращается в
    «?». Это не косметика — в имени ярлыка автозапуска есть русское слово, а
    «?» Windows в именах файлов не разрешает, и автозапуск просто не
    прописывался: `Unable to save shortcut ... (????).lnk`. Нашлось приёмкой
    на раннере, где система английская; на русской машине всё работало.

    Файл пишется с меткой UTF-8 (BOM) — по ней PowerShell 5.1 узнаёт
    кодировку без всяких настроек, а `-ExecutionPolicy Bypass` нужен затем,
    что политика по умолчанию неподписанные файлы запускать не даёт.
    """
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".ps1", encoding="utf-8-sig", delete=False, newline="\r\n"
    )
    try:
        handle.write(script)
        handle.close()
        return run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                handle.name,
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    finally:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass  # файл во временном каталоге — уберёт система

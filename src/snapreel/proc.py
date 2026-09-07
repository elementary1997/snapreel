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
import os
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


# Что дописывается к каждому скрипту. Кодировка вывода — чтобы русские
# буквы вернулись целыми; хвост — чтобы причина отказа пришла словами:
# `-EncodedCommand` отдаёт поток ошибок в CLIXML, и человеку иначе приезжает
# страница XML вместо «Не удаётся сохранить ярлык …».
#
# Заворачивать скрипт в try/catch нельзя, хотя это и напрашивается: внутри
# `try` первая же ошибка обрывает остаток блока, а скрипт уведомления на
# этом и держится — WinRT ругается на `AppendChild`, но тост показывает.
# Снаружи такая ошибка обрывает только свой оператор.
_PROLOGUE = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
_EPILOGUE = "\nif (-not $?) { [Console]::Out.WriteLine($Error[0].Exception.Message); exit 1 }"

# Так начинается сериализованный поток ошибок PowerShell.
_CLIXML = "#< CLIXML"


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
    отдавать нельзя, хоть это и напрашивается: у `-File` при ошибке внутри
    скрипта код возврата остаётся нулевым (проверено на живом powershell
    5.1), и сорвавшаяся запись ярлыка выглядела бы успехом; плюс запуск
    файла упирается в политику выполнения скриптов, которую групповая
    политика может запретить совсем.

    Ограничение — длина командной строки (около 32 тысяч знаков): самый
    длинный скрипт проекта даёт 1,7 тысячи, но бесконечно длинный так не
    передать.
    """
    wrapped = _PROLOGUE + script + _EPILOGUE
    encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
    raw = run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        timeout=timeout,
    )
    return subprocess.CompletedProcess(
        raw.args, raw.returncode, _decode(raw.stdout), _decode(raw.stderr)
    )


def _decode(raw: bytes | None) -> str:
    """Расшифровывает вывод, не полагаясь на одну кодировку.

    Свою часть PowerShell отдаёт в UTF-8 — так велит первая строка скрипта.
    Но ошибку разбора он находит раньше этой строки и пишет её в кодировке
    консоли (на русской Windows это cp866): декодированная как UTF-8, она
    превращается в кракозябры, и человеку опять достаётся невнятица вместо
    причины. Поэтому пробуем сначала UTF-8, потом кодировку консоли — её
    Python знает под именем `oem`.
    """
    if not raw:
        return ""
    console = _console_codec()
    for codec in ("utf-8", *([console] if console else [])):
        try:
            return raw.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue  # не эта кодировка или кодека тут нет
    return raw.decode("utf-8", errors="replace")


def _console_codec() -> str | None:
    """Чем консоль Windows пишет то, что мы не успели перевести в UTF-8.

    `oem` — имя, под которым Python знает кодовую страницу консоли (на
    русской системе это cp866). За пределами Windows такого кодека нет, и
    гадать незачем: PowerShell там не запускается.
    """
    return "oem" if os.name == "nt" else None


def powershell_message(result: subprocess.CompletedProcess) -> str:
    """Причина отказа человеческими словами, чем бы её ни отдал PowerShell.

    Обычно её печатает хвост скрипта. Но ошибку разбора (скажем, апостроф
    там, где его не ждали) PowerShell находит до первой строки, и тогда
    остаётся только поток ошибок — а он в этом режиме приходит в CLIXML.
    Показывать человеку `<Objs Version="1.1.0.1" …` нельзя, поэтому текст
    из него достаётся, а если не достаётся — говорим общими словами.
    """
    spoken = (result.stdout or "").strip()
    if spoken:
        return spoken
    error = (result.stderr or "").strip()
    if not error:
        return "powershell вернул ошибку"
    if not error.startswith(_CLIXML):
        return error
    return _from_clixml(error) or "powershell не разобрал команду"


def _from_clixml(payload: str) -> str:
    """Достаёт человеческий текст из сериализованного потока ошибок."""
    import html
    import re

    pieces = []
    for chunk in re.findall(r"<S[^>]*>(.*?)</S>", payload, flags=re.DOTALL):
        text = html.unescape(chunk)
        # переводы строк PowerShell прячет за своими метками
        text = text.replace("_x000D_", "").replace("_x000A_", "\n")
        pieces.append(text.strip())
    return " ".join(piece for piece in pieces if piece).strip()


def ps_string(value: str) -> str:
    """Строка в кавычках, которую PowerShell прочитает целиком.

    Внутрь подставляются пути и текст уведомлений, то есть то, что выбрал
    человек. Апостроф в имени каталога — обычное дело («D:\\Ivan's tools»),
    и без удвоения он закрывает строку раньше времени: скрипт не выполняется
    вовсе, PowerShell спотыкается на разборе, а до объяснимой ошибки дело не
    доходит. Обратный слеш, в отличие от двойных кавычек, тут ничего не
    значит и трогать его не надо.
    """
    return "'" + value.replace("'", "''") + "'"

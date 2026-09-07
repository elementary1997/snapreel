"""Запуск внешних процессов: на Windows он обязан обходиться без консоли."""

from __future__ import annotations

import base64
import re
import subprocess
from pathlib import Path

import snapreel
from snapreel import proc

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW, как его называет Windows


def test_a_launch_asks_windows_not_to_open_a_console(monkeypatch):
    """Иначе поверх работы мигает чёрное окно: у оконного exe консоли нет."""
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", NO_WINDOW, raising=False)
    seen: dict = {}
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: seen.update(kwargs))

    proc.run(["ffprobe", "-version"], timeout=5)

    assert seen["creationflags"] == NO_WINDOW
    assert seen["timeout"] == 5  # остальные аргументы доходят как были


def test_the_callers_own_flags_are_not_lost(monkeypatch):
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", NO_WINDOW, raising=False)
    seen: dict = {}
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: seen.update(kwargs))

    proc.popen(["cmd", "/c", "del"], creationflags=0x00000200)

    assert seen["creationflags"] == NO_WINDOW | 0x00000200


def test_other_systems_get_no_windows_flags(monkeypatch):
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)

    assert proc.hidden() == {}


def test_the_package_starts_processes_only_through_proc():
    """Забытый вызов всплыл бы не здесь, а чёрным окном у человека на экране."""
    root = Path(snapreel.__file__).parent
    direct = re.compile(r"subprocess\.(run|Popen|call|check_output|check_call)\(")
    offenders = [
        f"{path.relative_to(root)}:{number}"
        for path in sorted(root.rglob("*.py"))
        if path.name != "proc.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if direct.search(line)
    ]

    assert offenders == []


def _catching(seen: dict):
    """Подмена запуска: запоминает команду и отвечает пустым выводом в байтах."""

    def run(command, **kwargs):
        seen["command"] = list(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    return run


def test_a_powershell_script_keeps_its_cyrillic(monkeypatch):
    """Командной строкой кириллица до PowerShell не доезжает.

    На английской Windows она превращается в «?», а «?» в имени файла
    система не разрешает — автозапуск не прописывался вовсе. Нашлось
    приёмкой на раннере: `Unable to save shortcut ... (????).lnk`.
    """
    seen: dict = {}
    monkeypatch.setattr(proc, "run", _catching(seen))

    proc.powershell("$link.Description = 'Snapreel — запись в буфер'")

    command = seen["command"]
    assert "-EncodedCommand" in command
    assert command[-1].isascii(), "по командной строке обязан ехать только ASCII"
    decoded = base64.b64decode(command[-1]).decode("utf-16-le")
    assert "$link.Description = 'Snapreel — запись в буфер'" in decoded


def test_a_powershell_failure_comes_back_as_words(monkeypatch):
    """В этом режиме PowerShell отдаёт поток ошибок как XML, а не как текст.

    Человек увидел бы страницу `<Objs Version=…>` вместо «Не удаётся
    сохранить ярлык …», поэтому причину печатает хвост самого скрипта.
    """
    seen: dict = {}
    monkeypatch.setattr(proc, "run", _catching(seen))

    proc.powershell("$s.Save()")

    decoded = base64.b64decode(seen["command"][-1]).decode("utf-16-le")
    assert "$Error[0].Exception.Message" in decoded
    assert "exit 1" in decoded
    # оборачивать в try нельзя: внутри него первая ошибка обрывает остаток
    # блока, а скрипт уведомления на этом и держится
    assert "try {" not in decoded


def test_console_words_are_decoded_even_before_our_encoding_takes_effect(monkeypatch):
    """Ошибку разбора PowerShell пишет раньше первой строки скрипта.

    То есть в кодировке консоли, а не в UTF-8: декодированная как UTF-8, она
    превращается в кракозябры — и человеку снова достаётся невнятица.
    """
    console = "Отсутствует признак конца строки.".encode("cp866")
    # на Windows это `oem`, но кодека с таким именем нет на других системах,
    # а проверять поведение надо в любом прогоне
    monkeypatch.setattr(proc, "_console_codec", lambda: "cp866")
    monkeypatch.setattr(
        proc, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, b"", console)
    )

    result = proc.powershell("$link = 'незакрытая")

    assert result.stderr == "Отсутствует признак конца строки."


def test_a_script_error_is_not_shown_as_xml():
    """Ошибку разбора PowerShell находит до первой строки — хвост не поможет."""
    clixml = (
        '#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell">'
        '<S S="Error">Строка:1 знак:60_x000D__x000A_</S>'
        '<S S="Error">Отсутствует признак конца строки.</S></Objs>'
    )
    result = subprocess.CompletedProcess(["powershell"], 1, "", clixml)

    message = proc.powershell_message(result)

    assert "Отсутствует признак конца строки" in message
    assert "<Objs" not in message and "CLIXML" not in message


def test_a_readable_error_is_left_alone():
    result = subprocess.CompletedProcess(["powershell"], 1, "", "не найден файл")

    assert proc.powershell_message(result) == "не найден файл"


def test_the_scripts_own_words_win():
    result = subprocess.CompletedProcess(
        ["powershell"], 1, "Не удается сохранить ярлык", "#< CLIXML"
    )

    assert proc.powershell_message(result) == "Не удается сохранить ярлык"


def test_the_package_calls_powershell_only_through_proc():
    """Забытый `-Command` вернул бы «?» вместо русских букв у человека."""
    root = Path(snapreel.__file__).parent
    direct = re.compile(r'"powershell(\.exe)?"')  # и с расширением, и без
    offenders = [
        f"{path.relative_to(root)}:{number}"
        for path in sorted(root.rglob("*.py"))
        if path.name != "proc.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if direct.search(line)
    ]

    assert offenders == []

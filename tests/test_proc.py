"""Запуск внешних процессов: на Windows он обязан обходиться без консоли."""

from __future__ import annotations

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

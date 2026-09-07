"""Короткие системные уведомления. Молча пропускаются, если способа нет."""

from __future__ import annotations

import shutil
import subprocess

from . import proc
from .platform_info import Environment, Platform, detect


def send(title: str, message: str, env: Environment | None = None) -> None:
    env = env or detect()
    try:
        if env.platform is Platform.MACOS:
            _macos(title, message)
        elif env.platform is Platform.WINDOWS:
            _windows(title, message)
        else:
            _linux(title, message)
    except (OSError, subprocess.SubprocessError):
        pass


def _macos(title: str, message: str) -> None:
    script = f'display notification "{_applescript(message)}" with title "{_applescript(title)}"'
    proc.run(["osascript", "-e", script], capture_output=True, timeout=10)


def _linux(title: str, message: str) -> None:
    if shutil.which("notify-send"):
        proc.run(
            ["notify-send", "--app-name=snapreel", "--expire-time=4000", title, message],
            capture_output=True,
            timeout=10,
        )


def _windows(title: str, message: str) -> None:
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
        f"$t.GetElementsByTagName('text')[0]"
        f".AppendChild($t.CreateTextNode({proc.ps_string(title)})) > $null;"
        f"$t.GetElementsByTagName('text')[1]"
        f".AppendChild($t.CreateTextNode({proc.ps_string(message)})) > $null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('snapreel')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    # текст уведомления русский, и командной строкой он до PowerShell не
    # доезжает: на английской Windows кириллица станет «?» (см. `proc`)
    proc.powershell(script, timeout=15)


def _applescript(text: str) -> str:
    """Экранирование для AppleScript: там строка в двойных кавычках.

    Windows этим пользоваться нельзя, хотя раньше так и было: в скрипте
    PowerShell строка одинарная, обратный слеш в ней ничего не значит — и
    удвоенный он таким и показывался. Человек видел «C:\\Users\\Иван\\…»
    вместо своего пути.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')

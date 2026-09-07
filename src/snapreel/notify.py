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
    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
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
        f".AppendChild($t.CreateTextNode('{_escape(title)}')) > $null;"
        f"$t.GetElementsByTagName('text')[1]"
        f".AppendChild($t.CreateTextNode('{_escape(message)}')) > $null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('snapreel')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    proc.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=15,
    )


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("'", "''")

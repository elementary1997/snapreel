"""Копирование файла в буфер обмена на macOS и Linux."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote


class ClipboardError(RuntimeError):
    pass


def file_uri(path: Path) -> str:
    return "file://" + quote(str(Path(path).resolve()))


# --- macOS ---------------------------------------------------------------


def macos_copy_files(paths: list[Path]) -> None:
    """AppleScript кладёт в буфер сам файл, а не его имя."""
    items = ", ".join(f'POSIX file "{Path(p).resolve()}"' for p in paths)
    script = (
        f"set the clipboard to {{{items}}}" if len(paths) > 1 else f"set the clipboard to {items}"
    )
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, errors="replace", timeout=15
    )
    if result.returncode != 0:
        raise ClipboardError(f"osascript не положил файл в буфер: {result.stderr.strip()}")


def macos_copy_text(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=15)


# --- Linux ---------------------------------------------------------------


def wayland_copy_files(paths: list[Path]) -> None:
    if not shutil.which("wl-copy"):
        raise ClipboardError("не найден wl-copy — установите wl-clipboard")
    payload = "\n".join(file_uri(p) for p in paths) + "\n"
    subprocess.run(
        ["wl-copy", "--type", "text/uri-list"],
        input=payload.encode("utf-8"),
        check=True,
        timeout=15,
    )


def wayland_copy_text(text: str) -> None:
    if not shutil.which("wl-copy"):
        raise ClipboardError("не найден wl-copy — установите wl-clipboard")
    subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True, timeout=15)


def x11_copy_files(paths: list[Path]) -> None:
    """xclip держит содержимое, пока жив его процесс, поэтому запускается фоном.

    Приложения GTK ждут `x-special/nautilus-clipboard`, остальные — `text/uri-list`.
    Один процесс xclip умеет отдавать только один тип, так что берём uri-list:
    его понимают Telegram, Chrome, Firefox, Thunar и Dolphin.
    """
    if not shutil.which("xclip"):
        raise ClipboardError("не найден xclip — установите xclip")
    payload = "\n".join(file_uri(p) for p in paths) + "\n"
    process = subprocess.Popen(
        ["xclip", "-selection", "clipboard", "-t", "text/uri-list"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    process.stdin.write(payload.encode("utf-8"))
    process.stdin.close()


def x11_copy_text(text: str) -> None:
    if not shutil.which("xclip"):
        raise ClipboardError("не найден xclip — установите xclip")
    process = subprocess.Popen(
        ["xclip", "-selection", "clipboard"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    process.stdin.write(text.encode("utf-8"))
    process.stdin.close()

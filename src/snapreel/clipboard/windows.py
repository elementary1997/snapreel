"""Копирование файла в буфер обмена Windows в формате CF_HDROP.

CF_HDROP — тот же формат, которым проводник кладёт файлы по Ctrl+C, поэтому
Ctrl+V срабатывает в Telegram, Slack, Discord, браузерах и в самом проводнике.
Реализовано на ctypes, чтобы не тянуть pywin32.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from pathlib import Path

CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
DROPEFFECT_COPY = 5


class _DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return kernel32


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    return user32


def _alloc(kernel32, payload: bytes):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise OSError(ctypes.get_last_error(), "GlobalAlloc не выделил память")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError(ctypes.get_last_error(), "GlobalLock не заблокировал память")
    ctypes.memmove(pointer, payload, len(payload))
    kernel32.GlobalUnlock(handle)
    return handle


def build_hdrop(paths: list[Path]) -> bytes:
    """DROPFILES + список путей UTF-16, каждый с нулём, и ещё один ноль в конце."""
    header = _DROPFILES()
    header.pFiles = ctypes.sizeof(_DROPFILES)
    header.pt.x = 0
    header.pt.y = 0
    header.fNC = False
    header.fWide = True
    names = "".join(f"{p}\0" for p in paths) + "\0"
    return bytes(header) + names.encode("utf-16-le")


def copy_files(paths: list[Path], retries: int = 10) -> None:
    """Кладёт файлы в буфер. Чужая блокировка буфера — обычное дело, поэтому повторы."""
    kernel32, user32 = _kernel32(), _user32()
    payload = build_hdrop([Path(p).resolve() for p in paths])
    effect = DROPEFFECT_COPY.to_bytes(4, "little")

    last_error: OSError | None = None
    for attempt in range(retries):
        if user32.OpenClipboard(None):
            break
        last_error = OSError(ctypes.get_last_error(), "буфер обмена занят другим окном")
        time.sleep(0.05 * (attempt + 1))
    else:
        raise last_error or OSError("не открыть буфер обмена")

    try:
        user32.EmptyClipboard()
        hdrop = _alloc(kernel32, payload)
        if not user32.SetClipboardData(CF_HDROP, hdrop):
            kernel32.GlobalFree(hdrop)
            raise OSError(ctypes.get_last_error(), "SetClipboardData(CF_HDROP) не сработал")
        # подсказка проводнику копировать, а не переносить файл
        fmt = user32.RegisterClipboardFormatW("Preferred DropEffect")
        if fmt:
            handle = _alloc(kernel32, effect)
            if not user32.SetClipboardData(fmt, handle):
                kernel32.GlobalFree(handle)
    finally:
        user32.CloseClipboard()


CF_UNICODETEXT = 13


def copy_text(text: str) -> None:
    kernel32, user32 = _kernel32(), _user32()
    payload = (text + "\0").encode("utf-16-le")
    if not user32.OpenClipboard(None):
        raise OSError(ctypes.get_last_error(), "не открыть буфер обмена")
    try:
        user32.EmptyClipboard()
        handle = _alloc(kernel32, payload)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise OSError(ctypes.get_last_error(), "SetClipboardData(CF_UNICODETEXT) не сработал")
    finally:
        user32.CloseClipboard()

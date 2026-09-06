"""Приложение Qt: что проверяется до его создания и почему.

Оба отказа Qt считает смертельными и вызывает abort, напечатав своё
английское сообщение. Перехватить это уже нечем, поэтому условия проверяются
заранее — и вот это здесь и держат.
"""

from __future__ import annotations

import pytest

from snapreel import qt
from snapreel.errors import OverlayUnavailable
from snapreel.platform_info import Environment, Platform

LINUX = Environment(platform=Platform.LINUX_X11, is_wsl=False)
WINDOWS = Environment(platform=Platform.WINDOWS, is_wsl=False)


def test_a_session_without_a_display_is_refused_in_words(monkeypatch):
    monkeypatch.setattr(qt, "detect", lambda: LINUX)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with pytest.raises(OverlayUnavailable) as failure:
        qt._refuse_early()

    assert "графической сессии" in str(failure.value)


def test_missing_xcb_libraries_are_named(monkeypatch):
    """Без них Qt не поднимает окно, и понять это по своему опыту нельзя."""
    from snapreel import deps

    monkeypatch.setattr(qt, "detect", lambda: LINUX)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(deps, "is_satisfied", lambda requirement, config=None: False)

    with pytest.raises(OverlayUnavailable) as failure:
        qt._refuse_early()

    assert "libxcb-cursor0" in str(failure.value)


def test_a_healthy_linux_session_passes(monkeypatch):
    from snapreel import deps

    monkeypatch.setattr(qt, "detect", lambda: LINUX)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(deps, "is_satisfied", lambda requirement, config=None: True)

    qt._refuse_early()  # молчит — значит можно заводить приложение


def test_other_systems_are_not_asked_about_x11(monkeypatch):
    """У Windows и macOS сессия есть всегда, а плагин Qt встроенный."""

    def forbidden(*args, **kwargs):
        raise AssertionError("на этой платформе про xcb спрашивать нечего")

    monkeypatch.setattr(qt, "detect", lambda: WINDOWS)
    monkeypatch.setattr("snapreel.deps.is_satisfied", forbidden)
    monkeypatch.delenv("DISPLAY", raising=False)

    qt._refuse_early()

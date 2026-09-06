"""Перевод координат оверлея: Qt меряет точками, захват экрана — пикселями.

Ошибка здесь не видна в окне и всплывает только в записанном клипе: на
Windows со масштабом 125% снимется не та область. Поэтому перевод проверяется
отдельно от самого окна.
"""

from __future__ import annotations

import pytest

from snapreel import selector
from snapreel.platform_info import Environment, Platform

WINDOWS = Environment(platform=Platform.WINDOWS, is_wsl=False)
MACOS = Environment(platform=Platform.MACOS, is_wsl=False)
X11 = Environment(platform=Platform.LINUX_X11, is_wsl=False)


class FakePoint:
    def __init__(self, x: int, y: int):
        self._x, self._y = x, y

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y


class FakeScreen:
    def __init__(self, ratio: float):
        self._ratio = ratio

    def devicePixelRatio(self) -> float:
        return self._ratio


@pytest.fixture
def ratio(monkeypatch):
    """Подменяет мнение Qt о масштабе экрана."""

    def set_to(value: float):
        class Guiapp:
            @staticmethod
            def screenAt(point):
                return FakeScreen(value)

            @staticmethod
            def primaryScreen():
                return FakeScreen(value)

        import PySide6.QtGui

        monkeypatch.setattr(PySide6.QtGui, "QGuiApplication", Guiapp)

    return set_to


def test_a_scaled_windows_desktop_gives_physical_pixels(ratio):
    """125% на Windows — это 1.25: gdigrab снимает физические пиксели."""
    ratio(1.25)

    assert selector.physical(FakePoint(400, 200), WINDOWS) == (500, 250)


def test_an_unscaled_desktop_changes_nothing(ratio):
    ratio(1.0)

    assert selector.physical(FakePoint(400, 200), X11) == (400, 200)


def test_macos_keeps_points_as_they_are():
    """Масштаб Retina добавляет recorder по пробному кадру — иначе он удвоится."""
    assert selector.physical(FakePoint(400, 200), MACOS) == (400, 200)


def test_the_way_back_matches_the_way_there(ratio):
    ratio(2.0)

    assert selector._logical(800, X11) == 400
    assert selector._logical(800, MACOS) == 800


def test_a_screenless_qt_does_not_break_the_conversion(monkeypatch):
    """Экран может не найтись — тогда считаем масштаб единичным, а не падаем."""

    class Guiapp:
        @staticmethod
        def screenAt(point):
            return None

        @staticmethod
        def primaryScreen():
            return None

    import PySide6.QtGui

    monkeypatch.setattr(PySide6.QtGui, "QGuiApplication", Guiapp)

    assert selector.physical(FakePoint(10, 20), X11) == (10, 20)
    assert selector._logical(10, X11) == 10

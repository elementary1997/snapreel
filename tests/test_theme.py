"""Палитра окна: что берётся из системы, а что приказано настройкой."""

from __future__ import annotations

import pytest

from snapreel import resources, theme
from snapreel.platform_info import Environment, Platform

WINDOWS = Environment(platform=Platform.WINDOWS, is_wsl=False)


def test_auto_follows_a_dark_system(monkeypatch):
    monkeypatch.setattr(theme, "prefers_dark", lambda env=None: True)
    assert theme.resolve("auto").dark


def test_auto_follows_a_light_system(monkeypatch):
    monkeypatch.setattr(theme, "prefers_dark", lambda env=None: False)
    assert not theme.resolve("auto").dark


def test_an_unknown_system_theme_stays_light(monkeypatch):
    """Спросить не вышло — берём светлую: она не ломается ни на каком фоне."""
    monkeypatch.setattr(theme, "prefers_dark", lambda env=None: None)
    assert not theme.resolve("auto").dark


@pytest.mark.parametrize("mode,dark", [("dark", True), ("light", False)])
def test_an_explicit_choice_never_asks_the_system(monkeypatch, mode, dark):
    def forbidden(env=None):
        raise AssertionError("тему задали явно, а систему всё равно спросили")

    monkeypatch.setattr(theme, "prefers_dark", forbidden)
    assert theme.resolve(mode).dark is dark


def test_both_palettes_define_every_colour():
    """Пропущенный цвет — это чёрный текст на чёрном фоне у кого-то одного."""
    for palette in (theme.LIGHT, theme.DARK):
        for name, value in vars(palette).items():
            if name == "dark":
                continue
            assert isinstance(value, str) and value.startswith("#"), name


def test_a_broken_system_answer_comes_back_as_unknown(monkeypatch):
    """Тема — украшение: её неудача не должна мешать окну открыться."""
    from snapreel import platform_info

    monkeypatch.setattr(platform_info, "_windows_dark", lambda: 1 / 0)

    assert platform_info.prefers_dark(WINDOWS) is None
    assert theme.resolve("auto", WINDOWS) is theme.LIGHT


# --- иконки ---------------------------------------------------------------


def test_the_icons_ship_with_the_package():
    assert resources.icon() is not None
    assert resources.icon(recording=True) is not None
    assert resources.windows_icon() is not None


def test_a_missing_icon_is_not_an_error(monkeypatch):
    """Урезанная сборка без иконки обязана поднимать окно и трей как обычно."""
    monkeypatch.setattr(resources, "_find", lambda name: None)
    assert resources.icon() is None
    assert resources.windows_icon() is None

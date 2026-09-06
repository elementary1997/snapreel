"""Окно настроек: модель формы, захват комбинации и путь первого запуска."""

from __future__ import annotations

import sys

import pytest

from snapreel import cli, settings
from snapreel import config as config_module
from snapreel.config import Config


def test_every_setting_is_shown():
    """Настройка, которой нет в окне, правится только руками в TOML."""
    shown = {field.name for field in settings.FIELDS}

    assert shown == set(Config.__dataclass_fields__)


def test_a_saved_form_round_trips():
    config, errors = settings.parse(settings.values_of(Config(fps=45, keep_days=7)))

    assert errors == {}
    assert config.fps == 45 and config.keep_days == 7


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"fps": "9000"}, "fps"),
        ({"crf": "ы"}, "crf"),
        ({"min_seconds": "30", "max_seconds": "10"}, "min_seconds"),
        ({"filename_template": "clip_%Q"}, "filename_template"),
        ({"hotkey_mp4": "j"}, "hotkey_mp4"),
        ({"preset": "турбо"}, "preset"),
    ],
)
def test_a_bad_value_is_blamed_on_its_own_field(raw, field):
    """Окно подсвечивает строку, поэтому ошибка обязана знать своё поле."""
    _, errors = settings.parse(raw)

    assert field in errors


def test_a_comma_decimal_is_accepted():
    """На русской раскладке запятая — обычный десятичный разделитель."""
    config, errors = settings.parse({"max_seconds": "12,5"})

    assert errors == {} and config.max_seconds == 12.5


@pytest.mark.parametrize(
    ("modifiers", "keysym", "expected"),
    [
        (["ctrl", "alt"], "5", "<ctrl>+<alt>+5"),
        (["ctrl", "shift"], "R", "<ctrl>+<shift>+r"),
        (["super"], "Escape", "<super>+<esc>"),
        (["ctrl"], "Prior", "<ctrl>+<pageup>"),
        (["alt"], "F9", "<alt>+<f9>"),
    ],
)
def test_a_pressed_combination_becomes_a_config_value(modifiers, keysym, expected):
    assert settings.combo(modifiers, keysym) == expected


def test_a_combination_without_a_modifier_is_refused():
    """Иначе клавиша перестанет печататься — то же правило, что и в CLI."""
    with pytest.raises(Exception, match="модификатор"):
        settings.combo([], "r")


@pytest.mark.parametrize(
    ("keysym", "modifier"),
    [("Control_L", "ctrl"), ("Alt_R", "alt"), ("Meta_L", "super"), ("a", None)],
)
def test_modifier_keys_are_told_from_ordinary_ones(keysym, modifier):
    assert settings.modifier_of(keysym) == modifier


# --- первый запуск ---------------------------------------------------------


def test_the_first_run_opens_the_settings_window(tmp_path, monkeypatch):
    """Скачал и щёлкнул: без конфига запись начинать некуда, надо настроить."""
    opened = []
    monkeypatch.setattr(cli, "_settings", lambda cfg, path: opened.append(path) or 0)
    monkeypatch.setattr(cli, "_record", lambda cfg, args: pytest.fail("вместо настроек записал"))

    assert cli.main(["--config", str(tmp_path / "config.toml")]) == 0
    assert opened


def test_a_configured_machine_records_at_once(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    config_module.save(Config(), path)
    monkeypatch.setattr(cli, "_settings", lambda cfg, p: pytest.fail("настройки уже есть"))
    monkeypatch.setattr(cli, "_record", lambda cfg, args: 0)

    assert cli.main(["--config", str(path)]) == 0


def test_settings_without_tkinter_explains_itself(tmp_path, monkeypatch, capsys):
    """`doctor` и запись обязаны жить без tkinter — окно просто объясняет, чем заменить."""
    monkeypatch.setitem(sys.modules, "snapreel.settings_ui", None)

    code = cli.main(["--config", str(tmp_path / "c.toml"), "settings"])

    assert code == 2
    assert "hotkey set" in capsys.readouterr().err


def test_an_explicit_record_never_opens_settings(tmp_path, monkeypatch):
    """Попросили записать — записываем, даже если конфига ещё нет."""
    monkeypatch.setattr(cli, "_settings", lambda cfg, path: pytest.fail("подменил запись окном"))
    monkeypatch.setattr(cli, "_record", lambda cfg, args: 0)

    assert cli.main(["--config", str(tmp_path / "нет.toml"), "record"]) == 0

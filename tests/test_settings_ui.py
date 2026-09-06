"""Само окно настроек: собирается, ловит комбинацию, сохраняет.

Тесты требуют дисплея и потому пропускаются в обычном headless-прогоне. В CI
их гоняет job `smoke` под Xvfb — иначе окно так и осталось бы непроверенным:
логику формы держит `test_settings.py`, а вот фокус, привязки и вёрстка живут
только внутри Tk.
"""

from __future__ import annotations

import tomllib

import pytest

from snapreel.config import Config

tk = pytest.importorskip("tkinter", reason="без tkinter окна нет")

pytestmark = pytest.mark.gui


@pytest.fixture
def window(tmp_path):
    from snapreel.settings_ui import SettingsWindow

    try:
        widget = SettingsWindow(Config(), tmp_path / "config.toml")
    except tk.TclError as exc:  # нет дисплея
        pytest.skip(f"нет дисплея: {exc}")
    widget.root.withdraw()
    yield widget
    try:
        widget.root.destroy()
    except tk.TclError:
        pass


def pump(window) -> None:
    window.root.update_idletasks()
    window.root.update()


def press(window, *keysyms: str) -> None:
    """Нажатия идут в окно, а не в поле: ловит их привязка уровня окна."""
    window.root.focus_force()
    for keysym in keysyms:
        window.root.event_generate(f"<KeyPress-{keysym}>")
        pump(window)


def test_the_window_shows_every_setting(window):
    pump(window)

    assert set(window._widgets) == set(Config.__dataclass_fields__)


def test_a_pressed_combination_lands_in_the_field(window):
    """Ради этого окно и затевалось: комбинацию нажимают, а не печатают."""
    window.root.deiconify()
    pump(window)
    field = window._widgets["hotkey_mp4"]
    field._start()
    pump(window)

    press(window, "Control_L", "Alt_L", "7")

    assert field.get() == "<ctrl>+<alt>+7"


def test_a_key_without_a_modifier_is_refused_in_place(window):
    window.root.deiconify()
    pump(window)
    field = window._widgets["hotkey_mp4"]
    before = field.get()
    field._start()
    pump(window)

    press(window, "9")

    assert field.get() == before
    assert "модификатор" in field._label.cget("text")


def test_saving_writes_the_config(window, tmp_path):
    window._widgets["fps"].variable.set("48")

    window._save()

    data = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert data["fps"] == 48
    assert window.saved is True


def test_a_bad_value_keeps_the_file_untouched(window, tmp_path):
    window._widgets["fps"].variable.set("9000")

    window._save()

    assert not (tmp_path / "config.toml").exists()
    assert "1..120" in window._errors["fps"].cget("text")
    assert window.saved is False

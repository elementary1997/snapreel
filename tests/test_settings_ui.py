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


# --- панель обновления -----------------------------------------------------


def drain(window, panel, tries: int = 50) -> None:
    """Крутит цикл окна, пока фоновый поток не доложит о результате."""
    import time

    for _ in range(tries):
        pump(window)
        panel._drain()
        if str(panel.button.cget("state")) == "normal":
            return
        time.sleep(0.02)


def test_the_panel_offers_the_newer_version(window, monkeypatch):
    from snapreel import updates

    release = updates.Release((9, 9, 9), "v9.9.9", "a", "https://d/a", "https://d/s", 1)
    monkeypatch.setattr(updates, "check", lambda directory, force=False: release)
    panel = window.updates

    panel.check()
    drain(window, panel)

    assert "9.9.9" in panel.button.cget("text")


def test_the_panel_says_when_nothing_is_newer(window, monkeypatch):
    from snapreel import updates

    monkeypatch.setattr(updates, "check", lambda directory, force=False: None)
    panel = window.updates

    panel.check()
    drain(window, panel)

    assert "последняя" in panel.status.cget("text")


def test_a_network_failure_stays_inside_the_panel(window, monkeypatch):
    """Окно настроек не должно падать оттого, что github недоступен."""
    from snapreel import updates

    def boom(directory, force=False):
        raise updates.UpdateError("не спросить github об обновлениях: нет сети")

    monkeypatch.setattr(updates, "check", boom)
    panel = window.updates

    panel.check()
    drain(window, panel)

    assert "нет сети" in panel.status.cget("text")
    assert str(panel.button.cget("state")) == "normal"

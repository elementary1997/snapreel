"""Само окно настроек: собирается, ловит комбинацию, сохраняет.

Qt умеет рисовать в память (`QT_QPA_PLATFORM=offscreen`), поэтому окно
проверяется в обычном прогоне, без дисплея и без отдельного job в CI. Логику
формы держит `test_settings.py`, а здесь — то, что живёт только внутри окна:
захват клавиш, сборка строк, сохранение.
"""

from __future__ import annotations

import tomllib

import pytest

from snapreel.config import Config

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit
except Exception as exc:
    # `importorskip` ловит только ModuleNotFoundError, а Qt падает на
    # отсутствующей системной библиотеке (libEGL) обычным ImportError
    pytest.skip(f"PySide6 недоступен: {exc}", allow_module_level=True)


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt_app, tmp_path):
    from snapreel.settings_ui import SettingsWindow

    widget = SettingsWindow(Config(), tmp_path / "config.toml")
    yield widget
    widget.close()  # закрытие дожидается фоновых потоков окна
    widget.deleteLater()
    qt_app.processEvents()


def press(widget, key: Qt.Key, text: str = "") -> None:
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text))


def release(widget, key: Qt.Key) -> None:
    widget.keyReleaseEvent(
        QKeyEvent(QEvent.Type.KeyRelease, key, Qt.KeyboardModifier.NoModifier, "")
    )


# --- сборка ---------------------------------------------------------------


def test_the_window_shows_every_setting(window):
    assert set(window._widgets) == set(Config.__dataclass_fields__)


def test_every_group_gets_its_own_page(window):
    from snapreel import settings

    assert window.nav.count() == len(settings.GROUPS)
    assert window.stack.count() == len(settings.GROUPS)


def test_choosing_a_section_switches_the_page(window):
    window.nav.setCurrentRow(2)
    assert window.stack.currentIndex() == 2


# --- захват комбинации ----------------------------------------------------


def test_a_pressed_combination_lands_in_the_field(window):
    """Ради этого окно и затевалось: комбинацию нажимают, а не печатают."""
    field = window._widgets["hotkey_mp4"]
    field._start()

    press(field, Qt.Key.Key_Control)
    press(field, Qt.Key.Key_Alt)
    press(field, Qt.Key.Key_5, "5")

    assert field.get() == "<ctrl>+<alt>+5"


def test_a_key_without_a_modifier_is_refused_in_place(window):
    field = window._widgets["hotkey_mp4"]
    before = field.get()
    field._start()

    press(field, Qt.Key.Key_9, "9")

    assert field.get() == before
    assert "модификатор" in field._label.text()


def test_releasing_a_modifier_forgets_it(window):
    field = window._widgets["hotkey_mp4"]
    field._start()

    press(field, Qt.Key.Key_Control)
    release(field, Qt.Key.Key_Control)
    press(field, Qt.Key.Key_5, "5")

    # ctrl отпустили до основной клавиши — комбинация вышла без модификатора
    assert "модификатор" in field._label.text()


def test_escape_stops_the_capture(window):
    field = window._widgets["hotkey_mp4"]
    before = field.get()
    field._start()

    press(field, Qt.Key.Key_Escape)

    assert field.get() == before
    assert field._button.text() == "Изменить"


def test_keys_are_ignored_until_the_capture_starts(window):
    """Иначе окно перестало бы слушаться обычной клавиатуры."""
    field = window._widgets["hotkey_mp4"]
    before = field.get()

    press(field, Qt.Key.Key_5, "5")

    assert field.get() == before


# --- сохранение -----------------------------------------------------------


def test_saving_writes_the_config(window, tmp_path, monkeypatch):
    from snapreel import autostart

    monkeypatch.setattr(autostart, "install", lambda hotkey, env=None: autostart.Outcome(True, ""))
    window._widgets["fps"].setText("48")

    window._save()

    saved = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert saved["fps"] == 48
    assert window.saved


def test_a_bad_value_keeps_the_file_untouched(window, tmp_path):
    window._widgets["fps"].setText("9000")

    window._save()

    assert not (tmp_path / "config.toml").exists()
    assert not window.saved
    # на месте подсказки теперь ошибка, и подписана она как ошибка
    assert window._hints["fps"].property("role") == "error"
    assert "fps" in window._hints["fps"].text()


def test_a_bad_value_opens_the_section_that_holds_it(window):
    window.nav.setCurrentRow(window.nav.count() - 1)
    window._widgets["fps"].setText("9000")

    window._save()

    assert window.nav.currentItem().text() == "Запись"


def test_switches_and_choices_come_back_as_values(window):
    window._widgets["capture_cursor"].setValue(False)
    window._widgets["preset"].setCurrentText("fast")

    raw = window._collect()

    assert raw["capture_cursor"] is False
    assert raw["preset"] == "fast"


def test_the_controls_match_the_kinds_of_the_fields(window):
    from snapreel.settings_ui import DirEdit, HotkeyEdit, Switch

    assert isinstance(window._widgets["hotkey_mp4"], HotkeyEdit)
    assert isinstance(window._widgets["capture_cursor"], Switch)
    assert isinstance(window._widgets["preset"], QComboBox)
    assert isinstance(window._widgets["output_dir"], DirEdit)
    assert isinstance(window._widgets["ffmpeg"], QLineEdit)


# --- панель обновления ----------------------------------------------------


def test_the_panel_offers_the_newer_version(window, monkeypatch):
    from snapreel import updates

    release = updates.Release((9, 9, 9), "v9.9.9", "asset", "https://d/a", None, 1)
    monkeypatch.setattr(updates, "check", lambda directory, force=False: release)

    window.updates.check()
    _settle(window)

    assert "9.9.9" in window.updates.button.text()


def test_the_panel_says_when_nothing_is_newer(window, monkeypatch):
    from snapreel import updates

    monkeypatch.setattr(updates, "check", lambda directory, force=False: None)

    window.updates.check()
    _settle(window)

    assert "последняя версия" in window.updates.status.text()


def test_a_network_failure_stays_inside_the_panel(window, monkeypatch):
    from snapreel import updates

    def fail(directory, force=False):
        raise updates.UpdateError("не спросить github об обновлениях")

    monkeypatch.setattr(updates, "check", fail)

    window.updates.check()
    _settle(window)

    assert "github" in window.updates.status.text()


def _settle(window, tries: int = 200) -> None:
    """Ждёт поток панели обновлений: он отвечает сигналом в главный поток."""
    app = QApplication.instance()
    for _ in range(tries):
        app.processEvents()
        if window.updates._thread is None and window.updates.button.isEnabled():
            return
    raise AssertionError("панель обновлений так и не ответила")


# --- выбор каталога -------------------------------------------------------


def test_the_clips_folder_is_picked_in_the_file_manager(window, monkeypatch):
    """Путь печатают руками только от безысходности — есть проводник."""
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: "/tmp/Клипы")
    )
    field = window._widgets["output_dir"]

    field._pick()

    assert field.get() == "/tmp/Клипы"


def test_a_cancelled_choice_keeps_the_old_path(window, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    field = window._widgets["output_dir"]
    field._edit.setText("/было/так")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))

    field._pick()

    assert field.get() == "/было/так"


# --- звуковые устройства --------------------------------------------------


def test_the_audio_field_offers_the_devices_of_this_machine(window):
    """Точное имя устройства человек по памяти не наберёт — оно из списка."""
    from snapreel.audio import Device
    from snapreel.settings_ui import AudioBox

    field = window._widgets["audio_device"]
    assert isinstance(field, AudioBox)

    field._fill([Device("alsa_input.pci", "Микрофон")])

    assert field.itemText(field.count() - 1) == "Микрофон"
    assert field.itemData(field.count() - 1) == "alsa_input.pci"


def test_the_chosen_device_goes_to_the_config_by_its_real_name(window):
    """В списке показывается человеческое имя, а в конфиг уходит то, что ждёт ffmpeg."""
    from snapreel.audio import Device

    field = window._widgets["audio_device"]
    field._fill([Device("2", "[2] Built-in Microphone")])
    field.setCurrentText("[2] Built-in Microphone")

    assert window._collect()["audio_device"] == "2"


def test_a_typed_device_is_kept_as_written(window):
    """Устройство может называться не так, как его показал ffmpeg."""
    field = window._widgets["audio_device"]
    field.setCurrentText("Стерео микшер")

    assert window._collect()["audio_device"] == "Стерео микшер"


def test_an_empty_device_means_no_sound(window):
    field = window._widgets["audio_device"]
    field.setCurrentText("")

    assert window._collect()["audio_device"] == ""


def test_closing_the_window_waits_for_the_audio_thread(window):
    """Поток, переживший окно, роняет всё приложение — и не там, где виноват."""
    field = window._widgets["audio_device"]

    window.close()

    assert not field._thread.isRunning()

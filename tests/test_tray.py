"""Поведение резидента с иконкой: без экрана, без pystray и без записи."""

from __future__ import annotations

import threading

import pytest

from snapreel import tray
from snapreel.config import Config
from snapreel.platform_info import Environment, Platform
from snapreel.updates import Release, UpdateError

ENV = Environment(platform=Platform.LINUX_X11, is_wsl=False)
RELEASE = Release(
    version=(9, 9, 9),
    tag="v9.9.9",
    asset="snapreel-linux-x86_64",
    url="https://example.invalid/snapreel",
    checksums_url=None,
    size=1024,
)


class FakeProcess:
    """Запущенный snapreel, который живёт ровно столько, сколько скажет тест."""

    def __init__(self, done: bool = False):
        self._done = threading.Event()
        self.returncode: int | None = None
        if done:
            self.finish()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._done.wait(timeout or 5)
        return self.returncode

    def finish(self):
        self.returncode = 0
        self._done.set()


class Harness:
    def __init__(self, app, launched, notes, state):
        self.app = app
        self.launched = launched
        self.notes = notes
        self.state = state

    def keys(self) -> list[str]:
        return [item.key for item in self.app.menu()]

    def item(self, key):
        for item in self.app.menu():
            if item.key == key:
                return item
        raise AssertionError(f"в меню нет пункта {key}")


@pytest.fixture
def tray_app(monkeypatch, tmp_path):
    """Трей, у которого запуск процессов, автозапуск и хоткеи — подставные."""
    launched: list[list[str]] = []
    notes: list[str] = []
    state = {"autostart": False, "bound": 0}

    def launcher(argv):
        """По умолчанию процесс завершается сразу: тесту важна сама команда."""
        launched.append(list(argv))
        return FakeProcess(done=True)

    app = tray.TrayApp(
        Config(),
        tmp_path / "config.toml",
        ENV,
        launcher=launcher,
        notifier=lambda title, message: notes.append(message),
    )

    monkeypatch.setattr(tray.autostart, "autostart_enabled", lambda env=None: state["autostart"])
    monkeypatch.setattr(app, "bind_hotkeys", lambda: state.__setitem__("bound", state["bound"] + 1))
    return Harness(app, launched, notes, state)


# --- меню -----------------------------------------------------------------


def test_the_menu_offers_recording_settings_and_quit(tray_app):
    keys = tray_app.keys()
    assert "record_mp4" in keys
    assert "record_gif" in keys
    assert "settings" in keys
    assert "quit" in keys


def test_the_menu_shows_the_hotkey_next_to_recording(tray_app):
    assert "Ctrl+Shift+Alt+R" in tray_app.item("record_mp4").label


def test_a_broken_hotkey_leaves_the_menu_item_readable(tray_app):
    tray_app.app.config.hotkey_mp4 = "мусор+"
    assert tray_app.item("record_mp4").label == "Записать MP4"


def test_the_autostart_item_shows_the_current_state(tray_app):
    assert tray_app.item("autostart").checked is False
    tray_app.state["autostart"] = True
    assert tray_app.item("autostart").checked is True


# --- запись ---------------------------------------------------------------


def test_recording_starts_the_record_command(tray_app):
    tray_app.app.record()
    tray_app.app.join()
    assert tray_app.launched[0][-1] == "record"


def test_the_gif_item_asks_for_a_gif(tray_app):
    tray_app.app.record_gif()
    tray_app.app.join()
    assert tray_app.launched[0][-1] == "--gif"


def test_a_second_recording_does_not_start_while_the_first_runs(tray_app, monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr(tray_app.app, "_launch", lambda argv: process)
    tray_app.app.record()
    assert tray_app.app.recording

    tray_app.app.record()
    assert tray_app.notes == ["запись уже идёт"]

    process.finish()
    assert not tray_app.app.recording


def test_the_menu_says_it_is_recording_and_comes_back_afterwards(tray_app, monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr(tray_app.app, "_launch", lambda argv: process)
    tray_app.app.record()
    assert tray_app.item("record_mp4").label == "Идёт запись…"
    assert not tray_app.item("record_gif").enabled

    process.finish()
    assert tray_app.item("record_mp4").enabled


def test_a_failed_launch_is_reported_and_does_not_raise(tray_app, monkeypatch):
    def refuse(argv):
        raise OSError("нет такого файла")

    monkeypatch.setattr(tray_app.app, "_launch", refuse)
    tray_app.app.record()
    assert "не запустить запись" in tray_app.notes[0]


# --- настройки ------------------------------------------------------------


def test_settings_open_in_a_separate_process(tray_app):
    tray_app.app.open_settings()
    tray_app.app.join()
    assert tray_app.launched[0][-1] == "settings"


def test_the_settings_item_is_disabled_while_the_window_is_open(tray_app, monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr(tray_app.app, "_launch", lambda argv: process)
    tray_app.app.open_settings()
    assert not tray_app.item("settings").enabled

    tray_app.app.open_settings()
    assert tray_app.launched == []  # второе окно не открывается

    process.finish()
    assert tray_app.item("settings").enabled


def test_closing_the_settings_window_rereads_the_config(tray_app):
    path = tray_app.app.config_path
    path.write_text('fps = 45\nhotkey_mp4 = "<ctrl>+<alt>+5"\n', encoding="utf-8")

    tray_app.app.reload()

    assert tray_app.app.config.fps == 45
    assert "Ctrl+Alt+5" in tray_app.item("record_mp4").label
    assert tray_app.state["bound"] == 1  # комбинации перевешены на новые


def test_a_broken_config_after_settings_does_not_kill_the_tray(tray_app):
    tray_app.app.config_path.write_text("fps = ", encoding="utf-8")
    tray_app.app.reload()
    assert "конфиг не прочитан" in tray_app.notes[0]


# --- автозапуск -----------------------------------------------------------


def test_the_autostart_item_installs_and_removes(tray_app, monkeypatch):
    calls = []
    ok = tray.autostart.Outcome(True, "готово")

    def record(what):
        calls.append(what)
        return ok

    monkeypatch.setattr(tray.autostart, "install_autostart", lambda env=None: record("in"))
    monkeypatch.setattr(tray.autostart, "remove_autostart", lambda env=None: record("out"))

    tray_app.app.toggle_autostart()
    tray_app.state["autostart"] = True
    tray_app.app.toggle_autostart()

    assert calls == ["in", "out"]


def test_a_refused_autostart_is_explained(tray_app, monkeypatch):
    bad = tray.autostart.Outcome(False, "не прописать автозапуск: отказано")
    monkeypatch.setattr(tray.autostart, "install_autostart", lambda env=None: bad)
    tray_app.app.toggle_autostart()
    assert tray_app.notes == ["не прописать автозапуск: отказано"]


# --- обновления -----------------------------------------------------------


def test_the_update_check_is_skipped_when_the_user_turned_it_off(tray_app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("check_updates = false, а в сеть всё равно пошли")

    monkeypatch.setattr(tray.updates, "check", forbidden)
    tray_app.app.config.check_updates = False
    tray_app.app.check_updates()
    tray_app.app.join()


def test_a_found_release_becomes_a_menu_item(tray_app, monkeypatch):
    monkeypatch.setattr(tray.updates, "supported", lambda: True)
    monkeypatch.setattr(tray.updates, "check", lambda directory, force=False: RELEASE)

    tray_app.app.check_updates()
    tray_app.app.join()

    assert tray_app.item("update").label == "Обновить до 9.9.9"
    assert any("9.9.9" in note for note in tray_app.notes)


def test_a_github_failure_does_not_break_the_tray(tray_app, monkeypatch):
    def fail(directory, force=False):
        raise UpdateError("не спросить github об обновлениях")

    monkeypatch.setattr(tray.updates, "supported", lambda: True)
    monkeypatch.setattr(tray.updates, "check", fail)

    tray_app.app.check_updates()
    tray_app.app.join()

    assert tray_app.app.release is None
    assert tray_app.item("update").label == "Проверить обновления"


def test_a_failed_install_keeps_the_update_offered(tray_app, monkeypatch):
    monkeypatch.setattr(tray.updates, "supported", lambda: True)
    monkeypatch.setattr(tray.updates, "check", lambda directory, force=False: RELEASE)
    monkeypatch.setattr(
        tray.updates,
        "update",
        lambda release, progress=None: (_ for _ in ()).throw(UpdateError("оборвалось")),
    )

    tray_app.app.check_updates()
    tray_app.app.join()
    tray_app.app.install_update()
    tray_app.app.join()

    assert tray_app.app.release is RELEASE
    assert any("обновление не удалось" in note for note in tray_app.notes)


# --- pystray --------------------------------------------------------------


def test_the_menu_translates_to_pystray(tray_app):
    pytest.importorskip("pystray", reason="иконка ставится экстрой .[tray]")
    menu = tray.build_menu(tray_app.app)
    labels = [str(entry.text) for entry in menu.items if entry.text]
    assert "Настройки…" in labels
    assert "Выйти" in labels


class FakeIcon:
    """Иконка pystray ровно в той части, которая нужна трею."""

    def __init__(self, visible: bool = True):
        self.visible = visible
        self.menu = None
        self.icon = None
        self.title = ""
        self.stopped = 0

    def update_menu(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped += 1


def test_a_tray_that_never_appears_says_why(tray_app, capsys):
    tray_app.app.attach(FakeIcon(visible=False))
    tray_app.app.watch_dock(timeout=0.01)
    tray_app.app.join()
    assert "нет системного трея" in capsys.readouterr().err


def test_a_docked_icon_stays_quiet(tray_app, capsys):
    tray_app.app.attach(FakeIcon(visible=True))
    tray_app.app.watch_dock(timeout=0.01)
    tray_app.app.join()
    assert capsys.readouterr().err == ""


def test_quitting_stops_the_icon_and_the_hotkeys(tray_app):
    icon = FakeIcon()
    tray_app.app.attach(icon)
    stopped = []
    tray_app.app._listener = type("L", (), {"stop": lambda self: stopped.append(1)})()

    tray_app.app.quit()

    assert icon.stopped == 1
    assert stopped == [1]


def make_icon(module: str, visible: bool = True):
    """Иконка, притворяющаяся конкретным бэкендом pystray."""
    kind = type("Icon", (FakeIcon,), {"__module__": module})
    return kind(visible=visible)


def test_an_x11_icon_without_a_tray_manager_is_not_really_visible(monkeypatch):
    monkeypatch.setattr(tray, "systray_manager_present", lambda: False)
    assert not tray.visible(make_icon("pystray._xorg"))


def test_an_x11_icon_with_a_tray_manager_is_visible(monkeypatch):
    monkeypatch.setattr(tray, "systray_manager_present", lambda: True)
    assert tray.visible(make_icon("pystray._xorg"))


def test_other_backends_are_taken_at_their_word(monkeypatch):
    def unexpected():
        raise AssertionError("селекцию X11 спрашивать не у кого")

    monkeypatch.setattr(tray, "systray_manager_present", unexpected)
    assert tray.visible(make_icon("pystray._appindicator"))
    assert not tray.visible(make_icon("pystray._appindicator", visible=False))


def test_the_cli_explains_a_missing_tray(monkeypatch, capsys, tmp_path):
    """Без pystray команда обязана назвать замену, а не упасть трейсбеком."""
    from snapreel import cli
    from snapreel.errors import TrayUnavailable

    def refuse(config, path=None):
        raise TrayUnavailable("нет pystray — иконку в трее показать нечем")

    monkeypatch.setattr(tray, "run", refuse)

    code = cli.main(["--config", str(tmp_path / "config.toml"), "tray"])

    assert code == 2
    printed = capsys.readouterr().err
    assert "нет pystray" in printed
    assert "daemon" in printed

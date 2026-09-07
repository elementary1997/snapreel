"""Поведение резидента с иконкой: без экрана, без Qt-трея и без записи."""

from __future__ import annotations

import threading
import time

import pytest

from snapreel import tray
from snapreel.backends import CaptureError
from snapreel.config import Config
from snapreel.errors import SelectionCancelled, TrayUnavailable
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


class Harness:
    def __init__(self, app, recorded, notes, state):
        self.app = app
        self.recorded = recorded
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
    """Трей, у которого запись, автозапуск и хоткеи — подставные."""
    recorded: list[bool] = []
    notes: list[str] = []
    state = {"autostart": False, "bound": 0}

    app = tray.TrayApp(
        Config(),
        tmp_path / "config.toml",
        ENV,
        record_fn=recorded.append,
        notifier=lambda title, message: notes.append(message),
    )

    monkeypatch.setattr(tray.autostart, "autostart_enabled", lambda env=None: state["autostart"])
    monkeypatch.setattr(app, "bind_hotkeys", lambda: state.__setitem__("bound", state["bound"] + 1))
    return Harness(app, recorded, notes, state)


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


def test_recording_happens_in_the_tray_itself(tray_app):
    """Отдельного процесса больше нет: он стоил секунд ожидания (ADR-0010)."""
    tray_app.app.record()

    assert tray_app.recorded == [False]
    assert not tray_app.app.recording


def test_the_packing_leaves_the_main_thread(tray_app):
    """ffmpeg дописывает файл и собирает GIF минутами — иконка ждать не может."""
    started = threading.Event()
    release = threading.Event()

    def pack():
        started.set()
        assert release.wait(5)

    tray_app.app._record_clip = lambda as_gif: pack
    tray_app.app.record()

    assert started.wait(5)  # главный поток уже вернулся, а хвост ещё идёт
    assert tray_app.app.recording  # и запись всё ещё считается идущей
    release.set()
    tray_app.app.join()
    assert not tray_app.app.recording


def test_a_failed_packing_is_reported_too(tray_app):
    """Сбой буфера или сборки GIF приходит человеку тем же уведомлением."""

    def refuse():
        raise CaptureError("запись не создала файл")

    tray_app.app._record_clip = lambda as_gif: refuse
    tray_app.app.record()
    tray_app.app.join()

    assert "запись не вышла" in tray_app.notes[0]
    assert not tray_app.app.recording


def test_the_gif_item_asks_for_a_gif(tray_app):
    tray_app.app.record_gif()

    assert tray_app.recorded == [True]


def test_a_second_recording_does_not_start_while_the_first_runs(tray_app, monkeypatch):
    def busy(as_gif):
        """Внутри записи трей отвечает на всё остальное — цикл событий общий."""
        assert tray_app.app.recording
        tray_app.app.record()

    monkeypatch.setattr(tray_app.app, "_record_clip", busy)
    tray_app.app.record()

    assert tray_app.notes == ["запись уже идёт"]
    assert not tray_app.app.recording


def test_the_menu_says_it_is_recording_and_comes_back_afterwards(tray_app, monkeypatch):
    labels = {}

    def busy(as_gif):
        labels["mp4"] = tray_app.item("record_mp4").label
        labels["gif"] = tray_app.item("record_gif").enabled

    monkeypatch.setattr(tray_app.app, "_record_clip", busy)
    tray_app.app.record()

    assert labels == {"mp4": "Идёт запись…", "gif": False}
    assert tray_app.item("record_mp4").enabled


def test_a_failed_recording_is_reported_and_does_not_raise(tray_app, monkeypatch):
    def refuse(as_gif):
        raise CaptureError("не найден ffmpeg")

    monkeypatch.setattr(tray_app.app, "_record_clip", refuse)
    tray_app.app.record()

    assert "запись не вышла" in tray_app.notes[0]
    assert "не найден ffmpeg" in tray_app.notes[0]
    assert not tray_app.app.recording


def test_no_recording_starts_over_the_settings_window(tray_app, monkeypatch):
    """Окно настроек модально: оверлей поверх него не получил бы ни мыши, ни Esc."""
    monkeypatch.setattr(
        "snapreel.settings_ui.open_settings",
        lambda config, path=None, hotkey_note=None: tray_app.app.record(),
    )

    tray_app.app.open_settings()

    assert tray_app.recorded == []
    assert tray_app.notes == ["сначала закройте окно настроек"]


def test_the_settings_window_stays_shut_during_a_recording(tray_app, monkeypatch):
    """Окно модально: открытое поверх записи, оно отняло бы у рамки «Стоп» и Esc."""
    seen = {}

    def busy(as_gif):
        seen["пункт"] = tray_app.item("settings").enabled
        tray_app.app.open_settings()  # а если позвали мимо меню
        seen["окно"] = tray_app.app.settings_open

    monkeypatch.setattr(tray_app.app, "_record_clip", busy)
    tray_app.app.record()

    assert seen == {"пункт": False, "окно": False}
    assert tray_app.notes == ["идёт запись — настройки откроются после неё"]
    assert tray_app.item("settings").enabled


def test_quitting_finishes_the_recording_instead_of_dropping_it(tray_app, monkeypatch):
    """«Выйти» посреди записи не теряет клип и не бросает ffmpeg писать экран."""
    stopped = []
    packed = []

    class FakeRecording:
        def stop(self, timeout=20):
            stopped.append(timeout)

    def fake_start(config, **kwargs):
        kwargs["on_started"](FakeRecording())
        tray_app.app.quit()  # человек выбрал «Выйти», пока висит рамка
        return "сессия"

    monkeypatch.setattr("snapreel.recorder.start", fake_start)
    monkeypatch.setattr("snapreel.recorder.finish", packed.append)
    monkeypatch.setattr(tray_app.app, "unbind_hotkeys", lambda: None)
    tray_app.app._record_clip = tray_app.app._record_here  # тут нужен настоящий путь

    tray_app.app.record()
    tray_app.app.join()

    assert stopped, "ffmpeg остался писать экран"
    assert packed == ["сессия"], "клип не упакован и не ушёл в буфер"
    assert not tray_app.app._threads[-1].daemon, "упаковку нельзя обрывать выходом"


def test_the_settings_window_takes_the_hotkeys_off(tray_app, monkeypatch):
    """Комбинацию в окне нажимают — глобальный слушатель принял бы это за запись."""
    bound = []
    monkeypatch.setattr(tray_app.app, "bind_hotkeys", lambda: bound.append("on"))
    monkeypatch.setattr(tray_app.app, "unbind_hotkeys", lambda: bound.append("off"))
    monkeypatch.setattr(
        "snapreel.settings_ui.open_settings",
        lambda config, path=None, hotkey_note=None: bound.append("окно"),
    )

    tray_app.app.open_settings()

    assert bound == ["off", "окно", "on"]


def test_a_broken_config_still_gets_the_hotkeys_back(tray_app, monkeypatch):
    """Иначе испорченный конфиг оставил бы человека вовсе без комбинаций."""
    bound = []
    monkeypatch.setattr(tray_app.app, "bind_hotkeys", lambda: bound.append("on"))
    tray_app.app.config_path.write_text("fps = ", encoding="utf-8")

    tray_app.app.reload()

    assert bound == ["on"]


def test_a_cancelled_selection_says_nothing(tray_app, monkeypatch):
    """Esc в оверлее — это ответ человека, а не сбой, о котором надо шуметь."""

    def cancel(as_gif):
        raise SelectionCancelled("выделение отменено")

    monkeypatch.setattr(tray_app.app, "_record_clip", cancel)
    tray_app.app.record()

    assert tray_app.notes == []


def test_the_bridge_delivers_that_recording_to_the_qt_loop(tray_app, need):
    """Второй конец того же моста: очередь Qt обязана донести действие."""
    need("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    qt_app = QApplication.instance() or QApplication([])
    bridge = tray.bridge_class()()
    bridge.invoked.connect(lambda job: job())
    tray_app.app.attach(tray._Icon(bridge, qt_app))

    thread = threading.Thread(target=tray_app.app.record_gif)
    thread.start()
    thread.join(5)
    deadline = time.monotonic() + 5
    while not tray_app.recorded and time.monotonic() < deadline:
        qt_app.processEvents()

    assert tray_app.recorded == [True]


def test_a_hotkey_press_hands_the_recording_to_the_main_thread(tray_app):
    """Оверлей — окно Qt, и заводить его из потока pynput нельзя."""
    jobs = []
    tray_app.app.attach(FakeIcon(jobs))

    thread = threading.Thread(target=tray_app.app.record)
    thread.start()
    thread.join(5)
    deadline = time.monotonic() + 5
    while not jobs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert jobs, "заявка из чужого потока не дошла до главного"

    assert tray_app.recorded == []  # ещё ничего не началось: ждём главный поток
    jobs.pop()()  # а это уже он
    assert tray_app.recorded == [False]


# --- настройки ------------------------------------------------------------


def test_settings_open_in_the_same_process(tray_app, monkeypatch):
    """Окно живёт в цикле событий трея: у Qt он один на всё приложение."""
    opened = []
    monkeypatch.setattr(
        "snapreel.settings_ui.open_settings",
        lambda config, path=None, hotkey_note=None: opened.append(path) or True,
    )

    tray_app.app.open_settings()

    assert opened == [tray_app.app.config_path]
    assert tray_app.recorded == []  # окно настроек не путается с записью


def test_a_second_settings_window_does_not_open(tray_app, monkeypatch):
    """Пункт меню выключен, пока окно открыто, и второе окно не заводится."""
    seen = []

    def once(config, path=None, hotkey_note=None):
        seen.append(tray_app.item("settings").enabled)  # каким пункт виден изнутри
        tray_app.app.open_settings()  # повторное нажатие, пока окно открыто
        return True

    monkeypatch.setattr("snapreel.settings_ui.open_settings", once)

    tray_app.app.open_settings()

    assert seen == [False]  # пункт выключен, пока окно на экране
    assert tray_app.item("settings").enabled  # и снова доступен после закрытия


def test_a_broken_settings_window_does_not_take_down_the_tray(tray_app, monkeypatch):
    def explode(config, path=None, hotkey_note=None):
        raise RuntimeError("окно не собралось")

    monkeypatch.setattr("snapreel.settings_ui.open_settings", explode)

    tray_app.app.open_settings()

    assert any("не открыть настройки" in note for note in tray_app.notes)
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


def test_the_daily_check_is_skipped_when_the_user_turned_it_off(tray_app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("check_updates = false, а в сеть всё равно пошли")

    monkeypatch.setattr(tray.updates, "supported", lambda: True)
    monkeypatch.setattr(tray.updates, "check", forbidden)
    tray_app.app.config.check_updates = False

    tray_app.app._check(force=False)


def test_a_manual_check_says_that_the_version_is_current(tray_app, monkeypatch):
    """У нажатия в меню обязан быть видимый исход, даже когда всё свежее."""
    monkeypatch.setattr(tray.updates, "supported", lambda: True)
    monkeypatch.setattr(tray.updates, "check", lambda directory, force=False: None)

    tray_app.app.check_updates()
    tray_app.app.join()

    assert tray_app.notes == ["установлена последняя версия"]


def test_a_manual_check_reports_an_unreachable_github(tray_app, monkeypatch):
    def fail(directory, force=False):
        raise UpdateError("не спросить github об обновлениях")

    monkeypatch.setattr(tray.updates, "supported", lambda: True)
    monkeypatch.setattr(tray.updates, "check", fail)

    tray_app.app.check_updates()
    tray_app.app.join()

    assert tray_app.notes == ["не спросить github об обновлениях"]


def test_a_manual_check_works_even_with_the_daily_one_off(tray_app, monkeypatch):
    """Переключатель выключает автоматику, а не саму кнопку."""
    monkeypatch.setattr(tray.updates, "supported", lambda: True)
    monkeypatch.setattr(tray.updates, "check", lambda directory, force=False: RELEASE)
    tray_app.app.config.check_updates = False

    tray_app.app.check_updates()
    tray_app.app.join()

    assert tray_app.item("update").label == "Обновить до 9.9.9"


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


# --- меню и иконка Qt -------------------------------------------------------


@pytest.mark.gui
def test_the_menu_translates_to_a_qt_menu(tray_app, need):
    """Единственная проверка перевода меню в Qt: пункты, галочки, разделители."""
    need("PySide6")
    from PySide6.QtWidgets import QApplication, QMenu

    QApplication.instance() or QApplication([])
    menu = QMenu()

    tray.fill_menu(menu, tray_app.app.menu())

    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert "Настройки…" in labels
    assert "Выйти" in labels
    assert any(action.isCheckable() for action in menu.actions())


class FakeIcon:
    """То, что трей считает иконкой: меню, вызов в главном потоке и выход.

    Со списком `jobs` действие не выполняется, а откладывается — так тест
    видит, что из чужого потока трей не делает ничего сам.
    """

    def __init__(self, jobs=None):
        self.updates = 0
        self.stopped = 0
        self.jobs = jobs

    def update_menu(self) -> None:
        self.updates += 1

    def invoke(self, job) -> None:
        if self.jobs is None:
            job()
        else:
            self.jobs.append(job)

    def stop(self) -> None:
        self.stopped += 1


def test_refresh_only_signals_the_icon(tray_app):
    """Из потоков нельзя рисовать: `refresh` лишь просит главный перечитать меню."""
    icon = FakeIcon()
    tray_app.app.attach(icon)

    tray_app.app.refresh()

    assert icon.updates == 1


def test_a_broken_icon_does_not_take_down_the_tray(tray_app):
    class Broken(FakeIcon):
        def update_menu(self):
            raise RuntimeError("меню не перечиталось")

    tray_app.app.attach(Broken())
    tray_app.app.refresh()  # молча пережить


def test_quitting_stops_the_icon_and_the_hotkeys(tray_app):
    icon = FakeIcon()
    tray_app.app.attach(icon)
    stopped = []
    tray_app.app._listener = type("L", (), {"stop": lambda self: stopped.append(1)})()

    tray_app.app.quit()

    assert icon.stopped == 1
    assert stopped == [1]


def test_the_tray_does_not_leave_before_the_packing_is_done(monkeypatch, tmp_path, need):
    """Выход из меню не вправе оборвать упаковку: там клип уходит в буфер."""
    need("PySide6")
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(tray.TrayApp, "bind_hotkeys", lambda self: None)
    monkeypatch.setattr(tray.TrayApp, "watch_updates", lambda self: None)
    monkeypatch.setattr(
        "snapreel.qt.application", lambda config=None: (QApplication.instance(), False)
    )
    made: list = []
    packed: list = []
    monkeypatch.setattr(tray.TrayApp, "greet", lambda self: made.append(self))

    def exec_(self):
        """Пока крутился цикл, человек записал клип — упаковка ушла в фон."""

        def pack():
            time.sleep(0.3)
            packed.append("в буфере")

        made[0]._threads.append(tray._start(pack, daemon=False))
        return 0

    monkeypatch.setattr(QApplication, "exec", exec_)

    assert tray.run(Config(), tmp_path / "c.toml", ENV) == 0
    assert packed == ["в буфере"]


def test_a_session_without_a_tray_is_explained(monkeypatch, capsys, tmp_path, need):
    """Qt отвечает честно: трея в сессии нет — говорим об этом и живём дальше."""
    need("PySide6")
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    monkeypatch.setattr(tray.TrayApp, "bind_hotkeys", lambda self: None)
    monkeypatch.setattr(tray.TrayApp, "watch_updates", lambda self: None)
    monkeypatch.setattr(tray.TrayApp, "greet", lambda self: None)
    monkeypatch.setattr(
        "snapreel.qt.application", lambda config=None: (QApplication.instance(), False)
    )
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)

    assert tray.run(Config(), tmp_path / "c.toml", ENV) == 0
    assert "нет системного трея" in capsys.readouterr().err


def test_a_missing_qt_comes_out_as_our_own_error(monkeypatch, tmp_path):
    """Без Qt команда обязана объяснить, чего не хватает."""
    from snapreel.errors import OverlayUnavailable

    def refuse(config=None):
        raise OverlayUnavailable("нет PySide6 — окна показать нечем")

    monkeypatch.setattr("snapreel.qt.application", refuse)

    with pytest.raises(TrayUnavailable) as failure:
        tray.run(Config(), tmp_path / "c.toml", ENV)

    assert "PySide6" in str(failure.value)


def test_the_cli_explains_a_missing_tray(monkeypatch, capsys, tmp_path):
    """Без Qt команда обязана назвать замену, а не упасть трейсбеком."""
    from snapreel import cli
    from snapreel.errors import TrayUnavailable

    def refuse(config, path=None):
        raise TrayUnavailable("нет PySide6 — иконку в трее показать нечем")

    monkeypatch.setattr(tray, "run", refuse)

    code = cli.main(["--config", str(tmp_path / "config.toml"), "tray"])

    assert code == 2
    printed = capsys.readouterr().err
    assert "нет PySide6" in printed
    assert "daemon" in printed


def test_a_refused_binding_reaches_the_person(tray_app, monkeypatch):
    """Молча не встать нельзя: человек решит, что хоткеи просто не работают."""
    from snapreel.hotkeys import HotkeyError

    def refuse(config, handler, env=None):
        raise HotkeyError("комбинацию уже кто-то держит")

    monkeypatch.setattr("snapreel.hotkeys.listen", refuse)

    # фикстура подменяет `bind_hotkeys` заглушкой — здесь нужен настоящий
    tray.TrayApp.bind_hotkeys(tray_app.app)

    assert "комбинации не работают" in tray_app.notes[0]
    assert tray_app.app.hotkey_problem == "комбинацию уже кто-то держит"


def test_the_settings_window_hears_about_that_refusal(tray_app, monkeypatch):
    """Окно открывает трей, и он знает про привязку точнее любой проверки."""
    seen = {}
    monkeypatch.setattr(
        "snapreel.settings_ui.open_settings",
        lambda config, path=None, hotkey_note=None: seen.setdefault("нота", hotkey_note),
    )
    tray_app.app._hotkey_problem = "комбинацию уже кто-то держит"

    tray_app.app.open_settings()

    assert seen["нота"] == "комбинацию уже кто-то держит"


def test_a_broken_hotkey_in_the_config_does_not_take_down_the_tray(monkeypatch, capsys, tmp_path):
    """Испорченное значение в конфиге не должно уносить с собой иконку.

    Тест не требует ни pynput, ни дисплея: их отсутствие — такой же отказ
    хоткеев, и трей обязан пережить любой из них одинаково.
    """
    config = Config()
    config.hotkey_mp4 = "мусор+"
    app = tray.TrayApp(config, tmp_path / "config.toml", ENV, record_fn=lambda as_gif: None)

    app.bind_hotkeys()  # молча пережить, а не бросить

    assert "горячие клавиши не слушаем" in capsys.readouterr().err


def test_an_unparsable_hotkey_comes_out_as_our_own_error(tmp_path, need):
    from snapreel.hotkeys import HotkeyError, listen

    need("pynput")  # разбирает комбинацию он, а без дисплея не поднимется
    config = Config()
    config.hotkey_mp4 = "мусор+"

    with pytest.raises(HotkeyError) as failure:
        listen(config, lambda as_gif: None, ENV)

    assert "не разобрать" in str(failure.value)


def test_recording_takes_the_config_the_tray_has_now(tmp_path, monkeypatch):
    """Правки в окне настроек действуют на следующую запись, а не с перезапуска."""
    seen = []
    app = tray.TrayApp(Config(), tmp_path / "config.toml", ENV, notifier=lambda *_: None)
    monkeypatch.setattr("snapreel.recorder.start", lambda config, **kw: seen.append(config))
    monkeypatch.setattr("snapreel.recorder.finish", lambda session: None)

    app.config.fps = 60
    app.record()

    assert seen[0].fps == 60


def test_the_first_launch_says_where_to_look(tray_app):
    """Окно само не открывается, но и молчать нельзя: конфига ещё нет."""
    tray_app.app.greet()

    assert tray_app.notes == ["работает в трее — настройки в меню иконки"]


def test_a_configured_launch_greets_nobody(tray_app):
    tray_app.app.config_path.write_text("fps = 30\n", encoding="utf-8")

    tray_app.app.greet()

    assert tray_app.notes == []


def test_the_tray_icon_comes_from_the_package(need):
    """Иконка нарисована заранее и лежит в пакете, а не рисуется на лету."""
    need("PySide6")
    from PySide6.QtWidgets import QApplication

    from snapreel import resources

    QApplication.instance() or QApplication([])
    assert resources.icon() is not None
    assert resources.icon(recording=True) != resources.icon()
    assert not tray.icon().isNull()
    assert not tray.icon(recording=True).isNull()


def test_a_tray_without_qt_explains_itself(monkeypatch, tmp_path):
    """Голый импорт Qt выдал бы трейсбек — команда обязана сказать словами."""
    import builtins

    real = builtins.__import__

    def absent(name, *args, **kwargs):
        if name.startswith("PySide6"):
            raise ImportError("No module named 'PySide6'")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", absent)

    with pytest.raises(TrayUnavailable) as failure:
        tray.run(Config(), tmp_path / "c.toml", ENV)

    assert "PySide6" in str(failure.value)

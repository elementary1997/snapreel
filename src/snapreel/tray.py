"""Иконка в трее: постоянная точка входа в snapreel.

Резидентный процесс, который висит рядом с часами, слушает горячие клавиши
и раз в сутки спрашивает github о новой версии. Настройки открываются из его
меню — по желанию, а не при запуске.

Окна настроек и установки открываются прямо здесь: у Qt цикл событий один на
всё приложение, и вкладывать в него диалог — обычное дело. А вот запись
по-прежнему идёт отдельным процессом (`snapreel record`): у неё свой оверлей,
свой ffmpeg и своё право упасть, не утащив с собой иконку.

Qt ставится экстрой `.[ui]` и подтягивается лениво: без него обязаны работать
и `doctor`, и запись по хоткею (ADR-0009).
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import autostart, notify, resources, updates
from . import config as config_module
from .config import Config
from .errors import OverlayUnavailable, TrayUnavailable
from .platform_info import Environment, detect

# Первая проверка обновлений — не сразу: вход в систему и без нас занят.
# Дальше цикл просыпается каждый час, а сутки между запросами держит
# `updates.check` по своей отметке в updates.json.
FIRST_CHECK = 60.0
CHECK_TICK = 3600.0


@dataclass(frozen=True)
class Item:
    """Пункт меню как данные.

    Меню строится отдельно от Qt, чтобы его можно было проверить тестом в
    headless-окружении, где никакого трея нет.
    """

    key: str
    label: str = ""
    action: Callable[[], None] | None = None
    checked: bool | None = None  # None — обычный пункт, bool — галочка
    enabled: bool = True
    default: bool = False  # действие по щелчку левой кнопкой
    separator: bool = False


class TrayApp:
    """Состояние резидента: что сейчас идёт, что нашлось в релизах, что в меню.

    Внешние действия — запуск процесса и уведомление — принимаются
    параметрами: так поведение проверяется без экрана и без записи.
    """

    def __init__(
        self,
        config: Config,
        config_path: Path | None = None,
        env: Environment | None = None,
        launcher: Callable[[Sequence[str]], subprocess.Popen] | None = None,
        notifier: Callable[[str, str], None] | None = None,
    ):
        self.config = config
        self.config_path = config_path
        self.env = env or detect()
        self._launch = launcher or _spawn
        self._notify = notifier or (lambda title, message: notify.send(title, message, self.env))
        self._recording: subprocess.Popen | None = None
        self._window_open = False  # окно настроек открыто прямо здесь
        self._listener = None
        self._release: updates.Release | None = None
        self._icon = None
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []

    # --- состояние -------------------------------------------------------

    @property
    def recording(self) -> bool:
        return _alive(self._recording)

    @property
    def settings_open(self) -> bool:
        """Открыто ли окно настроек — второе такое же заводить незачем."""
        return self._window_open

    @property
    def release(self) -> updates.Release | None:
        """Найденный релиз новее текущего, если проверка его нашла."""
        return self._release

    def title(self) -> str:
        from . import __version__

        return "snapreel — идёт запись" if self.recording else f"snapreel {__version__}"

    # --- меню ------------------------------------------------------------

    def menu(self) -> tuple[Item, ...]:
        """Меню на текущий момент; пересобирается после каждого события."""
        busy = self.recording
        items = [
            Item(
                "record_mp4",
                "Идёт запись…" if busy else _with_hotkey("Записать MP4", self.config.hotkey_mp4),
                self.record,
                enabled=not busy,
                default=True,
            ),
            Item(
                "record_gif",
                _with_hotkey("Записать GIF", self.config.hotkey_gif),
                self.record_gif,
                enabled=not busy,
            ),
            Item("sep_record", separator=True),
            Item("settings", "Настройки…", self.open_settings, enabled=not self.settings_open),
            Item(
                "autostart",
                "Запускать при входе в систему",
                self.toggle_autostart,
                checked=autostart.autostart_enabled(self.env),
            ),
        ]
        if self._release is not None:
            items.append(Item("update", f"Обновить до {self._release.name}", self.install_update))
        elif updates.supported():
            items.append(Item("update", "Проверить обновления", self.check_updates))
        items += [Item("sep_quit", separator=True), Item("quit", "Выйти", self.quit)]
        return tuple(items)

    def attach(self, icon) -> None:
        """Связывает состояние с живой иконкой в трее."""
        self._icon = icon

    def refresh(self) -> None:
        """Просит трей перечитать меню, иконку и подсказку.

        Зовётся и из фоновых потоков, поэтому сама ничего не рисует: иконка
        лишь получает сигнал, а перерисовывается в главном потоке.
        """
        icon = self._icon
        if icon is None:
            return
        try:
            icon.update_menu()
        except Exception:  # трей живёт дальше даже с прежним меню
            pass

    # --- запись ----------------------------------------------------------

    def record(self, as_gif: bool = False) -> None:
        """Запускает запись отдельным процессом, если предыдущая закончилась."""
        if self.recording:
            self._notify("snapreel", "запись уже идёт")
            return
        try:
            self._recording = self._launch(autostart.launch_argv(as_gif, config=self.config_path))
        except OSError as exc:
            self._notify("snapreel", f"не запустить запись: {exc}")
            return
        self.refresh()
        self._watch(self._recording, self.refresh)

    def record_gif(self) -> None:
        self.record(as_gif=True)

    # --- настройки -------------------------------------------------------

    def open_settings(self) -> None:
        """Открывает окно настроек и по его закрытию перечитывает конфиг.

        Окно живёт в этом же процессе: цикл событий Qt один на приложение, и
        диалог просто вкладывается в него. Запись — другое дело, она уходит
        отдельным процессом.
        """
        if self._window_open:
            return
        self._window_open = True
        self.refresh()
        try:
            from .settings_ui import open_settings

            open_settings(self.config, self.config_path)
        except Exception as exc:  # окно не должно уносить с собой иконку
            self._notify("snapreel", f"не открыть настройки: {exc}")
        finally:
            self._window_open = False
        self.reload()

    def reload(self) -> None:
        """Подхватывает изменённый конфиг: комбинации могли стать другими."""
        try:
            self.config = config_module.load(self.config_path)
        except (OSError, ValueError, TypeError) as exc:
            self._notify("snapreel", f"конфиг не прочитан: {exc}")
            return
        self.bind_hotkeys()
        self.refresh()

    def toggle_autostart(self) -> None:
        enabled = autostart.autostart_enabled(self.env)
        install = autostart.install_autostart
        outcome = autostart.remove_autostart(self.env) if enabled else install(self.env)
        if not outcome.ok:
            self._notify("snapreel", outcome.message)
        self.refresh()

    # --- горячие клавиши -------------------------------------------------

    def bind_hotkeys(self) -> None:
        """Перевешивает комбинации на текущий конфиг.

        Молчит, когда не вышло: в Wayland глобальных хоткеев нет вовсе, а
        иконка в трее там работает и остаётся единственной точкой входа.
        """
        from .hotkeys import HotkeyError, listen

        self.unbind_hotkeys()
        try:
            self._listener = listen(self.config, self.record, self.env)
        except HotkeyError as exc:
            print(f"snapreel: горячие клавиши не слушаем — {exc}", file=sys.stderr)

    def unbind_hotkeys(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:  # слушатель мог умереть сам
                pass

    # --- обновления ------------------------------------------------------

    def check_updates(self, force: bool = True) -> None:
        """Спрашивает github в отдельном потоке, чтобы меню не подвисало."""
        self._threads.append(_start(lambda: self._check(force)))

    def _check(self, force: bool) -> None:
        """`force` — это нажатие в меню: у него обязан быть видимый исход.

        Суточный цикл, наоборот, молчит обо всём, кроме найденной версии, и
        подчиняется переключателю `check_updates`; ручная проверка выключателю
        автоматики не подчиняется — так же ведёт себя кнопка в настройках.
        """
        if not force and not self.config.check_updates:
            return
        if not updates.supported():
            if force:
                self._notify("snapreel", "обновлять умеем только готовый бинарник")
            return
        directory = (self.config_path or config_module.config_path()).parent
        try:
            release = updates.check(directory, force=force)
        except updates.UpdateError as exc:
            # github недоступен — это не повод шуметь окном или гасить трей
            print(f"snapreel: {exc}", file=sys.stderr)
            if force:
                self._notify("snapreel", str(exc))
            return
        if release is None:
            if force:
                self._notify("snapreel", "установлена последняя версия")
            return
        self._release = release
        self._notify("snapreel", f"вышла версия {release.name} — обновить можно из меню")
        self.refresh()

    def install_update(self) -> None:
        release, self._release = self._release, None
        if release is None:
            return
        self._threads.append(_start(lambda: self._install(release)))

    def _install(self, release: updates.Release) -> None:
        try:
            updates.update(release)
        except updates.UpdateError as exc:
            self._release = release  # не поставилось — пункт меню возвращается
            self._notify("snapreel", f"обновление не удалось: {exc}")
        else:
            self._notify("snapreel", f"обновлено до {release.name} — начнёт работать при запуске")
        self.refresh()

    def watch_updates(self) -> None:
        """Фоновый цикл суточной проверки; просыпается чаще, чем спрашивает."""

        def loop() -> None:
            if self._stopping.wait(FIRST_CHECK):
                return
            while True:
                self._check(force=False)
                if self._stopping.wait(CHECK_TICK):
                    return

        self._threads.append(_start(loop))

    # --- жизненный цикл --------------------------------------------------

    def greet(self) -> None:
        """Здоровается, если конфига ещё нет.

        Окно настроек при первом запуске больше не открывается само: человек
        просил иконку, а не окно поверх работы. Но и молчать нельзя — иначе
        первый запуск выглядит как «ничего не произошло».
        """
        path = self.config_path or config_module.config_path()
        try:
            if path.is_file():
                return
        except OSError:
            return
        self._notify("snapreel", "работает в трее — настройки в меню иконки")

    def quit(self) -> None:
        self._stopping.set()
        self.unbind_hotkeys()
        icon = self._icon
        if icon is not None:
            icon.stop()

    def join(self, timeout: float = 5.0) -> None:
        """Ждёт фоновые потоки — нужно тестам и аккуратному выходу."""
        for thread in list(self._threads):
            thread.join(timeout)

    def _watch(self, process: subprocess.Popen, then: Callable[[], None]) -> None:
        def wait() -> None:
            try:
                process.wait()
            except Exception:  # процесс мог сгинуть как угодно
                pass
            then()

        self._threads.append(_start(wait))


# --- Qt -------------------------------------------------------------------


def icon(recording: bool = False):
    """Иконка трея: рамка выделения с точкой записи, красная во время записи.

    Берётся готовый файл из пакета (`scripts/make-icon.py` рисует его один
    раз, каждый размер отдельно): в трее иконка видна размером с букву, и
    уменьшенная на лету она превращается в пятно. Файла может не быть в
    урезанной сборке — тогда иконка пустая, но трей всё равно поднимется.
    """
    from PySide6.QtGui import QIcon

    path = resources.icon(recording)
    return QIcon(str(path)) if path is not None else QIcon()


def fill_menu(menu, items) -> None:
    """Переносит наши пункты в QMenu.

    Меню пересобирается целиком на каждое изменение: пунктов меньше десятка,
    а следить за состоянием каждого — лишний источник рассинхронизации.
    """
    menu.clear()
    for item in items:
        if item.separator:
            menu.addSeparator()
            continue
        action = menu.addAction(item.label)
        action.setEnabled(item.enabled)
        if item.checked is not None:
            action.setCheckable(True)
            action.setChecked(item.checked)
        action.triggered.connect(_handler(item.action))


def _handler(action: Callable[[], None] | None):
    """Оборачивает действие: сбой пункта меню не должен гасить иконку."""

    def call(*_args) -> None:
        if action is None:
            return
        try:
            action()
        except Exception as exc:  # трей переживает любой пункт
            print(f"snapreel: {exc}", file=sys.stderr)

    return call


def run(config: Config, path: Path | None = None, env: Environment | None = None) -> int:
    """Показывает иконку и не возвращается, пока её не попросят исчезнуть."""
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    from .qt import application

    try:
        qt_app, _ = application(config)
    except OverlayUnavailable as exc:
        raise TrayUnavailable(str(exc)) from exc

    app = TrayApp(config, path, env)

    class _Bridge(QObject):
        """Мост из фоновых потоков в главный.

        Qt, как и любой тулкит, не разрешает трогать виджеты из чужого
        потока. Проверка обновлений и ожидание записи живут в потоках, а
        меню и иконку меняет только главный — через этот сигнал.
        """

        changed = Signal()

    bridge = _Bridge()
    menu = QMenu()
    icon_widget = QSystemTrayIcon(icon())

    def rebuild() -> None:
        fill_menu(menu, app.menu())
        icon_widget.setIcon(icon(app.recording))
        icon_widget.setToolTip(app.title())

    bridge.changed.connect(rebuild, Qt.ConnectionType.QueuedConnection)
    app.attach(_Icon(bridge, qt_app))

    rebuild()
    icon_widget.setContextMenu(menu)
    icon_widget.activated.connect(
        lambda reason: (
            app.open_settings() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
    )
    icon_widget.show()

    if not QSystemTrayIcon.isSystemTrayAvailable():
        # иконки в этой сессии не будет, но процесс имеет смысл: хоткеи
        # работают, а трей может появиться позже — Qt встанет в него сам
        print(
            "snapreel: иконка не появилась — в этой сессии нет системного трея. "
            "В GNOME его добавляет расширение AppIndicator; без него остаются "
            "системный хоткей и `snapreel record`.",
            file=sys.stderr,
        )

    app.bind_hotkeys()
    app.watch_updates()
    app.greet()
    try:
        qt_app.exec()
    finally:
        app.quit()
    return 0


class _Icon:
    """То, что `TrayApp` считает иконкой: обновить меню и погасить приложение."""

    def __init__(self, bridge, qt_app):
        self._bridge = bridge
        self._app = qt_app
        self.visible = True

    def update_menu(self) -> None:
        self._bridge.changed.emit()

    def stop(self) -> None:
        self._app.quit()


# --- мелочи ---------------------------------------------------------------


def _spawn(argv: Sequence[str]) -> subprocess.Popen:
    """Запускает snapreel отдельным процессом и сразу отпускает его."""
    return subprocess.Popen(list(argv), stdin=subprocess.DEVNULL)


def _alive(process: subprocess.Popen | None) -> bool:
    return process is not None and process.poll() is None


def _start(job: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=job, daemon=True)
    thread.start()
    return thread


def _with_hotkey(label: str, spec: str) -> str:
    """Подпись пункта с комбинацией; негодная в конфиге просто не показывается.

    Проверяется тем же `validate`, что и при назначении: комбинация без
    модификатора до системы не доедет, и обещать её в меню незачем.
    """
    try:
        autostart.validate(spec)
        return f"{label}   {autostart.describe(spec)}"
    except autostart.HotkeySetupError:
        return label

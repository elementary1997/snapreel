"""Иконка в трее: постоянная точка входа в snapreel.

Резидентный процесс, который висит рядом с часами, слушает горячие клавиши
и раз в сутки спрашивает github о новой версии. Настройки открываются из его
меню — по желанию, а не при запуске.

Сам трей **не создаёт ни одного окна**: и запись, и настройки он запускает
отдельными процессами (`snapreel record`, `snapreel settings`). Причина не в
чистоте, а в главном потоке: цикл событий pystray на macOS обязан идти в нём,
и Tk требует того же. Двум главным потокам не разойтись, а два процесса
расходятся сами. Заодно упавшая запись не уносит с собой иконку.

`pystray` ставится экстрой `.[tray]` и подтягивается лениво: без него обязаны
работать и `doctor`, и запись по хоткею (ADR-0008).
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import autostart, notify, theme, updates
from . import config as config_module
from .config import Config
from .errors import TrayUnavailable
from .platform_info import Environment, detect

# Первая проверка обновлений — не сразу: вход в систему и без нас занят.
# Дальше цикл просыпается каждый час, а сутки между запросами держит
# `updates.check` по своей отметке в updates.json.
FIRST_CHECK = 60.0
CHECK_TICK = 3600.0
# Сколько ждать, прежде чем сказать, что иконки не видно. Столько же длится
# терпение к медленной сессии: в автозапуске трей-менеджер поднимается не
# первым, и pystray встанет в него сам, как только тот появится.
DOCK_TIMEOUT = 15.0


@dataclass(frozen=True)
class Item:
    """Пункт меню как данные.

    Меню строится отдельно от pystray, чтобы его можно было проверить тестом
    в headless-окружении, где никакого трея нет.
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
        self._settings: subprocess.Popen | None = None
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
        return _alive(self._settings)

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
        """Связывает состояние с живой иконкой pystray."""
        self._icon = icon

    def refresh(self) -> None:
        """Просит pystray перечитать меню, иконку и подсказку."""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.menu = build_menu(self)
            icon.icon = image(self.recording)
            icon.title = self.title()
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
            self._recording = self._launch(autostart.launch_argv(as_gif))
        except OSError as exc:
            self._notify("snapreel", f"не запустить запись: {exc}")
            return
        self.refresh()
        self._watch(self._recording, self.refresh)

    def record_gif(self) -> None:
        self.record(as_gif=True)

    # --- настройки -------------------------------------------------------

    def open_settings(self) -> None:
        """Открывает окно настроек, а по его закрытию перечитывает конфиг."""
        if self.settings_open:
            return
        try:
            self._settings = self._launch(autostart.argv_for("settings"))
        except OSError as exc:
            self._notify("snapreel", f"не открыть настройки: {exc}")
            return
        self.refresh()
        self._watch(self._settings, self.reload)

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
        if not self.config.check_updates or not updates.supported():
            return
        directory = (self.config_path or config_module.config_path()).parent
        try:
            release = updates.check(directory, force=force)
        except updates.UpdateError as exc:
            # github недоступен — это не повод шуметь окном или гасить трей
            print(f"snapreel: {exc}", file=sys.stderr)
            return
        if release is None:
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

    def watch_dock(self, timeout: float = DOCK_TIMEOUT) -> None:
        """Говорит вслух, если иконка так и не появилась.

        В X11 без менеджера трея pystray не бросает исключение: он пишет
        «Failed to dock icon» себе в лог и продолжает крутить цикл событий.
        Снаружи это выглядит как повисший без следа процесс, поэтому причину
        объясняем сами. Процесс при этом живёт: трей может появиться позже.
        """

        def watch() -> None:
            if self._stopping.wait(timeout):
                return
            if visible(self._icon):
                return
            print(
                "snapreel: иконка не появилась — в этой сессии нет системного трея. "
                "В GNOME его добавляет расширение AppIndicator; в Wayland без него "
                "остаются системный хоткей и `snapreel record`. "
                "Как только трей появится, иконка встанет сама.",
                file=sys.stderr,
            )

        self._threads.append(_start(watch))

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


# --- pystray --------------------------------------------------------------


def image(recording: bool = False, size: int = 64):
    """Иконка: кружок «запись», красный во время записи.

    Рисуется кодом, а не лежит файлом: в трее она видна размером с букву,
    деталей не разобрать, а картинку пришлось бы класть в бинарник.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover — вместе с pystray
        raise TrayUnavailable("нужен Pillow: pip install 'snapreel[tray]'") from exc

    picture = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(picture)
    edge = size // 8
    pen.ellipse(
        (edge, edge, size - edge, size - edge),
        fill=theme.DANGER if recording else theme.ACCENT,
    )
    inner = size // 3
    pen.ellipse((inner, inner, size - inner, size - inner), fill="#ffffff")
    return picture


def systray_manager_present() -> bool | None:
    """Есть ли в X11-сессии менеджер трея. None — спросить не у кого.

    Владелец селекции `_NET_SYSTEM_TRAY_S<экран>` и есть тот, кто принимает
    иконки. Спрашиваем оконную систему напрямую: pystray об этом не скажет.
    """
    try:
        from Xlib import X, display
    except ImportError:
        return None
    try:
        connection = display.Display()
    except Exception:  # дисплея нет или он не отвечает
        return None
    try:
        selection = connection.intern_atom(f"_NET_SYSTEM_TRAY_S{connection.get_default_screen()}")
        return connection.get_selection_owner(selection) != X.NONE
    except Exception:
        return None
    finally:
        try:
            connection.close()
        except Exception:
            pass


def visible(icon) -> bool:
    """Видно ли иконку на самом деле.

    `icon.visible` у pystray означает только «мы попросили её показать»: в X11
    без менеджера трея он остаётся True, а иконки нет. Поэтому у X11-бэкенда
    переспрашиваем саму оконную систему, а остальным верим на слово — у них
    свои механизмы, и ложная тревога хуже молчания.
    """
    if icon is None or not getattr(icon, "visible", False):
        return False
    if not type(icon).__module__.endswith("_xorg"):
        return True
    return systray_manager_present() is not False


def build_menu(app: TrayApp):
    """Переводит наши пункты в меню pystray."""
    import pystray

    entries = []
    for item in app.menu():
        if item.separator:
            entries.append(pystray.Menu.SEPARATOR)
            continue
        entries.append(
            pystray.MenuItem(
                item.label,
                _handler(item.action),
                # pystray спрашивает состояние функцией, а не значением:
                # значение он бы запомнил на момент сборки меню
                checked=None if item.checked is None else (lambda _item, value=item.checked: value),
                enabled=item.enabled,
                default=item.default,
            )
        )
    return pystray.Menu(*entries)


def _handler(action: Callable[[], None] | None):
    """Оборачивает действие: сбой пункта меню не должен гасить иконку."""

    def call(_icon=None, _item=None) -> None:
        if action is None:
            return
        try:
            action()
        except Exception as exc:  # трей переживает любой пункт
            print(f"snapreel: {exc}", file=sys.stderr)

    return call


def run(config: Config, path: Path | None = None, env: Environment | None = None) -> int:
    """Показывает иконку и не возвращается, пока её не попросят исчезнуть."""
    try:
        import pystray
    except ImportError as exc:
        raise TrayUnavailable(
            "нет pystray — иконку в трее показать нечем. Поставьте: pip install 'snapreel[tray]'"
        ) from exc

    app = TrayApp(config, path, env)
    try:
        icon = pystray.Icon("snapreel", icon=image(), title=app.title())
    except Exception as exc:  # у каждого бэкенда свои беды
        raise TrayUnavailable(f"не создать иконку в трее: {exc}") from exc

    app.attach(icon)
    icon.menu = build_menu(app)
    app.bind_hotkeys()
    app.watch_updates()
    app.watch_dock()
    try:
        icon.run()
    except Exception as exc:
        raise TrayUnavailable(f"трей не запустился: {exc}") from exc
    finally:
        app.quit()
    return 0


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

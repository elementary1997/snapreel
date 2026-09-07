"""Иконка в трее: постоянная точка входа в snapreel.

Резидентный процесс, который висит рядом с часами, слушает горячие клавиши
и раз в сутки спрашивает github о новой версии. Настройки открываются из его
меню — по желанию, а не при запуске.

Всё, что показывает окна, происходит прямо здесь — и настройки, и установка,
и сама запись: цикл событий Qt один на приложение, а отдельный процесс стоил
человеку секунд ожидания между нажатием и оверлеем (ADR-0010).

Qt ставится экстрой `.[ui]` и подтягивается лениво: без него обязаны работать
и `doctor`, и запись по хоткею (ADR-0009).
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import autostart, notify, resources, updates
from . import config as config_module
from .config import Config
from .errors import OverlayUnavailable, SelectionCancelled, TrayUnavailable
from .platform_info import Environment, detect

# Первая проверка обновлений — не сразу: вход в систему и без нас занят.
# Дальше цикл просыпается каждый час, а сутки между запросами держит
# `updates.check` по своей отметке в updates.json.
FIRST_CHECK = 60.0
CHECK_TICK = 3600.0
# сколько трей ждёт упаковку, прежде чем вернуть управление. Дольше ждать
# незачем: поток упаковки не демонский, и процесс всё равно не уйдёт, пока
# клип не окажется в буфере обмена, — сколько бы ни собирался GIF
PACKING_WAIT = 120.0


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
        record_fn: Callable[[bool], None] | None = None,
        notifier: Callable[[str, str], None] | None = None,
    ):
        self.config = config
        self.config_path = config_path
        self.env = env or detect()
        self._record_clip = record_fn or self._record_here
        self._notify = notifier or (lambda title, message: notify.send(title, message, self.env))
        self._busy = False  # идёт запись — она живёт в этом же процессе
        self._running = None  # запущенный ffmpeg, пока висит рамка
        self._window_open = False  # окно настроек открыто прямо здесь
        self._hotkey_problem: str | None = None  # почему комбинации не встали
        self._listener = None
        self._release: updates.Release | None = None
        self._icon = None
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []

    # --- состояние -------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self._busy

    @property
    def hotkey_problem(self) -> str | None:
        """Чем кончилась последняя привязка комбинаций. `None` — встали."""
        return self._hotkey_problem

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
            Item(
                "settings",
                "Настройки…",
                self.open_settings,
                # окно модально на всё приложение: открытое поверх записи, оно
                # отняло бы у рамки и «Стоп», и Esc
                enabled=not self.settings_open and not busy,
            ),
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
        """Просит начать запись; зовётся и из меню, и из потока хоткеев.

        Сама работа уходит в главный поток: оверлей выделения и рамка —
        обычные окна Qt, а его виджеты чужой поток трогать не вправе.
        """
        self._on_main(lambda: self._record_now(as_gif))

    def record_gif(self) -> None:
        self.record(as_gif=True)

    def _record_now(self, as_gif: bool) -> None:
        """Выделение и запись — здесь; упаковка — фоновым потоком."""
        if self.recording:
            self._notify("snapreel", "запись уже идёт")
            return
        if self.settings_open:
            # окно настроек модально на всё приложение: оверлей выделения
            # открылся бы поверх него и не получил бы ни мыши, ни Esc
            self._notify("snapreel", "сначала закройте окно настроек")
            return
        self._busy = True
        self.refresh()
        tail = None
        try:
            tail = self._record_clip(as_gif)
        except SelectionCancelled:
            pass  # человек передумал — говорить ему об этом незачем
        except Exception as exc:
            self._blame(exc)
        if tail is None:
            self._busy = False
            self.refresh()
            return
        # дальше ни одного окна: ffmpeg дописывает файл, собирает GIF и
        # кладёт его в буфер — это секунды, а на GIF и минуты. В главном
        # потоке иконка всё это время была бы немой.
        #
        # Поток намеренно не демонский: выход из меню не должен обрывать
        # упаковку на полпути, а сколько она продлится, заранее не знает
        # никто — сборке GIF отведено столько, сколько сказано в конфиге.
        # Процесс дождётся её сам, даже если иконка уже погасла
        self._threads.append(_start(lambda: self._pack(tail), daemon=False))

    def _pack(self, tail: Callable[[], None]) -> None:
        try:
            tail()
        except Exception as exc:
            self._blame(exc)
        finally:
            self._busy = False
            self.refresh()
            if self._stopping.is_set():
                # пока мы упаковывали, человек выбрал «Выйти»: клип только
                # что лёг в буфер, которым владеет этот процесс, — и уйдёт
                # вместе с ним, если не передать содержимое дальше
                self.hand_over_clipboard()

    def hand_over_clipboard(self) -> None:
        """Отдаёт буфер обмена тому, кто переживёт наш выход.

        Владение селекцией в X11 живёт, пока жив процесс. Зовётся последним —
        после упаковки: до неё в буфере лежит прошлый клип, а нужен этот.
        """
        from . import clipboard

        try:
            clipboard.hand_off(self.env)
        except Exception as exc:  # выход не отменяется из-за буфера
            print(f"snapreel: буфер обмена не передан — {exc}", file=sys.stderr)

    def _blame(self, exc: Exception) -> None:
        """Сбой записи не гасит иконку, но и молчать о нём нельзя.

        Сообщение уходит уведомлением: у оконной сборки Windows потоков
        вывода нет, и привычное «напечатали в stderr» человек не увидит.
        """
        self._notify("snapreel", f"запись не вышла: {exc}")

    def _record_here(self, as_gif: bool) -> Callable[[], None]:
        """Запись по умолчанию — прямо здесь, в цикле событий трея.

        Возвращается «хвост» сценария — всё, чему главный поток уже не
        нужен: его трей доигрывает в фоне (ADR-0010).
        """
        from . import recorder

        try:
            session = recorder.start(
                self.config,
                as_gif=as_gif,
                env=self.env,
                on_started=self._remember,
                # трей — резидент: на Linux буфером обмена он владеет сам,
                # и файл уходит туда во всех форматах разом
                resident=True,
            )
        finally:
            # рамка закрылась: останавливать больше нечего
            self._running = None
        return lambda: recorder.finish(session)

    def _remember(self, recording) -> None:
        """Запоминает идущую запись — за неё дёргает «Выйти»."""
        self._running = recording

    def _on_main(self, job: Callable[[], None]) -> None:
        """Переносит работу в главный поток, если есть кому её передать."""
        invoke = getattr(self._icon, "invoke", None)
        if invoke is None:
            job()
            return
        invoke(job)

    # --- настройки -------------------------------------------------------

    def open_settings(self) -> None:
        """Открывает окно настроек и по его закрытию перечитывает конфиг.

        Окно живёт в этом же процессе: цикл событий Qt один на приложение, и
        диалог просто вкладывается в него — как и оверлей записи.

        На это время комбинации снимаются. Причин две, и обе настоящие: их
        тут же **нажимают**, назначая новые, и глобальный слушатель принял бы
        это нажатие за просьбу записать; а начатая запись открыла бы свой
        оверлей поверх модального окна — без мыши и без Esc, то есть навсегда.
        """
        if self._window_open:
            return
        if self.recording:
            # то же правило с другой стороны: пункт меню на время записи
            # выключен, но позвать сюда могут и мимо меню
            self._notify("snapreel", "идёт запись — настройки откроются после неё")
            return
        self._window_open = True
        self.unbind_hotkeys()
        self.refresh()
        try:
            from .settings_ui import open_settings

            open_settings(self.config, self.config_path, hotkey_note=self._hotkey_problem)
        except Exception as exc:  # окно не должно уносить с собой иконку
            self._notify("snapreel", f"не открыть настройки: {exc}")
        finally:
            self._window_open = False
        self.reload()  # заодно вешает комбинации обратно — уже новые

    def reload(self) -> None:
        """Подхватывает изменённый конфиг: комбинации могли стать другими.

        Комбинации вешаются в любом случае — даже когда конфиг не прочитан:
        на время окна настроек они сняты, и уйти без них значило бы оставить
        человека с испорченного конфига вовсе без горячих клавиш.
        """
        try:
            self.config = config_module.load(self.config_path)
        except (OSError, ValueError, TypeError) as exc:
            self._notify("snapreel", f"конфиг не прочитан: {exc}")
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

        Отказ — не повод гаснуть, но и не повод молчать: в Wayland глобальных
        клавиш нет вовсе, а на X11 комбинацию мог занять рабочий стол.
        Причина запоминается (её показывает окно настроек) и уходит
        уведомлением: `stderr` у оконной сборки Windows никто не читает.
        """
        from .hotkeys import HotkeyError, listen

        self.unbind_hotkeys()
        try:
            self._listener = listen(self.config, self.record, self.env)
        except HotkeyError as exc:
            self._hotkey_problem = str(exc)
            print(f"snapreel: горячие клавиши не слушаем — {exc}", file=sys.stderr)
            self._notify("snapreel", f"комбинации не работают: {exc}")
            return
        self._hotkey_problem = None

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
        """Гасит иконку, но не бросает начатое.

        Начатая запись доводится до файла: ffmpeg просят закончить, а
        упаковку трей доигрывает и дожидается (`run`). Иначе «Выйти»
        посреди записи означало бы потерянный клип, а на wlroots — ещё и
        оставшийся писать экран процесс, которого некому остановить: своего
        ограничения по времени у `wf-recorder` нет.
        """
        self._stopping.set()
        self.unbind_hotkeys()
        recording, self._running = self._running, None
        if recording is not None:
            self._notify("snapreel", "заканчиваю запись — клип уйдёт в буфер обмена")
            try:
                recording.stop(timeout=20)
            except Exception as exc:  # выход не отменяется из-за ffmpeg
                print(f"snapreel: запись не остановилась — {exc}", file=sys.stderr)
        icon = self._icon
        if icon is not None:
            icon.stop()

    def join(self, timeout: float = 5.0) -> None:
        """Ждёт фоновые потоки: в одном из них клип уходит в буфер обмена."""
        for thread in list(self._threads):
            thread.join(timeout)


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


def bridge_class():
    """Мост из фоновых потоков в главный; как и все окна, собирается лениво.

    Qt, как и любой тулкит, не разрешает трогать виджеты из чужого потока.
    Проверка обновлений живёт в своём потоке, а хоткеи — в потоке pynput;
    меню, иконку и окна трогает только главный — через эти сигналы.
    `invoked` переносит в него целое действие: запись начинается с
    полноэкранного оверлея, и звать её из чужого потока нельзя.
    """
    from PySide6.QtCore import QObject, Signal

    class Bridge(QObject):
        changed = Signal()
        invoked = Signal(object)

    return Bridge


def run(config: Config, path: Path | None = None, env: Environment | None = None) -> int:
    """Показывает иконку и не возвращается, пока её не попросят исчезнуть."""
    from .qt import application

    # приложение заводится первым: только оно умеет объяснить, чего не хватает,
    # а голый импорт Qt выдал бы человеку трейсбек вместо сообщения
    try:
        qt_app, _ = application(config)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon
    except OverlayUnavailable as exc:
        raise TrayUnavailable(str(exc)) from exc
    except ImportError as exc:
        raise TrayUnavailable(f"нет PySide6 — иконку в трее показать нечем: {exc}") from exc

    app = TrayApp(config, path, env)

    bridge = bridge_class()()
    menu = QMenu()
    icon_widget = QSystemTrayIcon(icon())

    def rebuild() -> None:
        fill_menu(menu, app.menu())
        icon_widget.setIcon(icon(app.recording))
        icon_widget.setToolTip(app.title())

    bridge.changed.connect(rebuild, Qt.ConnectionType.QueuedConnection)
    bridge.invoked.connect(_run_job, Qt.ConnectionType.QueuedConnection)
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
        # упаковка идёт фоновым потоком, и выход не вправе её оборвать: там
        # клип превращается в файл и уходит в буфер обмена. Ждём её здесь,
        # чтобы уведомление успело выйти при живом трее; а если она дольше —
        # процесс дождётся сам, поток не демонский, и буфер передаст она же
        app.join(timeout=PACKING_WAIT)
        app.hand_over_clipboard()
    return 0


class _Icon:
    """То, что `TrayApp` считает иконкой: обновить меню, позвать в главный
    поток и погасить приложение."""

    def __init__(self, bridge, qt_app):
        self._bridge = bridge
        self._app = qt_app
        self.visible = True

    def update_menu(self) -> None:
        self._bridge.changed.emit()

    def invoke(self, job: Callable[[], None]) -> None:
        self._bridge.invoked.emit(job)

    def stop(self) -> None:
        self._app.quit()


def _run_job(job: Callable[[], None]) -> None:
    """Выполняет отложенное действие в главном потоке, чем бы оно ни кончилось."""
    try:
        job()
    except Exception as exc:  # иконка переживает любое действие
        print(f"snapreel: {exc}", file=sys.stderr)


# --- мелочи ---------------------------------------------------------------


def _start(job: Callable[[], None], daemon: bool = True) -> threading.Thread:
    """Фоновая работа трея. `daemon=False` — работа, которую нельзя бросить."""
    thread = threading.Thread(target=job, daemon=daemon)
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

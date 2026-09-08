"""Окно настроек на Qt (PySide6).

Единственное место, где настройки меняются без терминала и без правки TOML
руками. Комбинацию тут не печатают, а нажимают, — ради этого окно и затевалось.

Строка настройки устроена как в системных настройках: слева название и
пояснение под ним, справа переключатель или поле. Разделы — слева, сохранение
— внизу, ничего лишнего на экране нет.

Модуль импортируется лениво: Qt есть не в каждой установке, а `doctor`,
запись по `--region` и `prune` обязаны работать без него.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import autostart, resources, settings, theme, updates
from . import config as config_module
from .config import Config

PAD = 16
GAP = 10
CONTROL_WIDTH = 230
HOTKEY_WIDTH = 300  # комбинация и кнопка «Изменить» в одну строку
SIDEBAR_WIDTH = 170
UPDATE_GROUP = "Обновление"
HOTKEY_GROUP = "Горячие клавиши"

# Модификаторы приходят отдельными событиями и сами по себе комбинацией не
# являются: ждём, пока нажмут что-то ещё.
_MODIFIERS = {
    Qt.Key.Key_Control: "ctrl",
    Qt.Key.Key_Shift: "shift",
    Qt.Key.Key_Alt: "alt",
    Qt.Key.Key_Meta: "super",
}


class Switch(QWidget):
    """Переключатель-таблетка.

    Рисуется сам: родной QCheckBox в Fusion — это квадратик с галочкой, а в
    настройках, где половина полей «да/нет», таблетка читается быстрее.
    """

    WIDTH = 44
    HEIGHT = 24

    def __init__(self, value: bool, palette: theme.Palette):
        super().__init__()
        self._palette = palette
        self._value = bool(value)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def value(self) -> bool:
        return self._value

    def setValue(self, value: bool) -> None:
        self._value = bool(value)
        self.update()

    def mousePressEvent(self, event) -> None:
        self.setValue(not self._value)

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QColor, QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        track = self._palette.accent if self._value else self._palette.border
        painter.setBrush(QColor(track))
        painter.drawRoundedRect(self.rect(), self.HEIGHT / 2, self.HEIGHT / 2)

        knob = QColor(self._palette.on_accent if self._value else self._palette.muted)
        painter.setBrush(knob)
        size = self.HEIGHT - 8
        left = self.WIDTH - size - 4 if self._value else 4
        painter.drawEllipse(left, 4, size, size)


class HotkeyEdit(QWidget):
    """Поле, которое запоминает нажатую комбинацию.

    Комбинацию тут не печатают: человек жмёт её целиком, а поле показывает,
    что уже нажато. Клавиатура перехватывается только на время захвата —
    иначе окно перестало бы слушаться обычных нажатий.
    """

    def __init__(self, value: str, palette: theme.Palette):
        super().__init__()
        self.value = value
        self._palette = palette
        self._held: list[str] = []
        self._capturing = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(GAP // 2)
        self._label = QLineEdit(autostart.describe_safe(value))
        self._label.setReadOnly(True)
        self._label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._button = QPushButton("Изменить")
        self._button.setFixedWidth(92)
        self._button.clicked.connect(self._toggle)
        row.addWidget(self._label, 1)
        row.addWidget(self._button)

    def get(self) -> str:
        return self.value

    def _toggle(self) -> None:
        self._stop() if self._capturing else self._start()

    def _start(self) -> None:
        self._capturing = True
        self._held = []
        self._label.setText("нажмите комбинацию…")
        self._button.setText("Отмена")
        self.grabKeyboard()

    def _stop(self) -> None:
        self._capturing = False
        self._held = []
        self._button.setText("Изменить")
        self.releaseKeyboard()
        self._label.setText(autostart.describe_safe(self.value))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = Qt.Key(event.key())
        if key in _MODIFIERS:
            name = _MODIFIERS[key]
            if name not in self._held:
                self._held.append(name)
            self._label.setText("+".join(self._held) + "+…")
            return
        if key is Qt.Key.Key_Escape:
            self._stop()
            return
        try:
            self.value = settings.combo(self._held, keysym(event))
        except autostart.HotkeySetupError as exc:
            # частый случай — клавиша без модификатора; объясняем и ждём дальше
            self._label.setText(str(exc).split(".")[0])
            return
        self._stop()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyReleaseEvent(event)
            return
        name = _MODIFIERS.get(Qt.Key(event.key()))
        if name and name in self._held:
            self._held.remove(name)


def keysym(event: QKeyEvent) -> str:
    """Имя клавиши в том виде, в каком его понимает разбор комбинаций."""
    key = Qt.Key(event.key())
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
        return f"f{int(key) - int(Qt.Key.Key_F1) + 1}"
    text = event.text().strip()
    if len(text) == 1 and text.isprintable():
        return text.lower()
    return Qt.Key(key).name.removeprefix("Key_").lower()


class DirEdit(QWidget):
    """Строка с путём и кнопкой выбора каталога."""

    def __init__(self, value: str):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(GAP // 2)
        self._edit = QLineEdit(value)
        button = QPushButton("Обзор")
        button.setFixedWidth(80)
        button.clicked.connect(self._pick)
        row.addWidget(self._edit, 1)
        row.addWidget(button)

    def get(self) -> str:
        return self._edit.text()

    def _pick(self) -> None:
        start = self._edit.text() or str(Config().resolved_output_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Куда складывать клипы", start)
        if chosen:
            self._edit.setText(chosen)


class AudioBox(QComboBox):
    """Выбор звукового устройства из тех, что есть на машине.

    Список спрашивается у ffmpeg в отдельном потоке: перечисление устройств
    dshow занимает секунду-другую, и окно не должно её ждать. Поле остаётся
    редактируемым — устройство может называться не так, как его показал
    ffmpeg, и вписать своё человек вправе.
    """

    def __init__(self, value: str, config: Config):
        super().__init__()
        self.setEditable(True)
        self.addItem("", "")  # пусто — звук не пишется
        if value:
            self.addItem(value, value)
        self.setCurrentText(value)

        self._worker = _AudioWorker(config)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._worker.found.connect(self._fill)
        self._thread.started.connect(self._worker.list_devices)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def _fill(self, devices: list) -> None:
        chosen = self.currentText()
        known = {self.itemData(index) for index in range(self.count())}
        for device in devices:
            if device.value not in known:
                self.addItem(device.label, device.value)
        self.setCurrentText(chosen)
        self.stop()

    def stop(self) -> None:
        """Дожидается потока.

        Пережить окно поток не должен: Qt убивает приложение, когда виджет
        исчезает из-под работающего QThread, и падение приходит не туда, где
        причина.
        """
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)

    def text(self) -> str:
        """Значение для конфига: у выбранного пункта — его, иначе набранное."""
        index = self.findText(self.currentText())
        if index >= 0 and self.itemData(index) is not None:
            return str(self.itemData(index))
        return self.currentText().strip()


class _AudioWorker(QObject):
    """Спрашивает устройства в стороне от окна: ffmpeg отвечает не мгновенно."""

    found = Signal(list)

    def __init__(self, config: Config):
        super().__init__()
        self._config = config

    def list_devices(self) -> None:
        from . import audio

        try:
            self.found.emit(audio.devices(self._config))
        except Exception:  # список — удобство, его неудача не ломает окно
            self.found.emit([])


class _UpdateWorker(QObject):
    """Сеть живёт в отдельном потоке: окно не должно замирать на запросе."""

    done = Signal(object, str)  # найденный релиз (или None) и текст ошибки

    def __init__(self, directory: Path, release=None):
        super().__init__()
        self._directory = directory
        self._release = release

    def check(self) -> None:
        try:
            self.done.emit(updates.check(self._directory, force=True), "")
        except updates.UpdateError as exc:
            self.done.emit(None, str(exc))

    def install(self) -> None:
        try:
            updates.update(self._release)
        except updates.UpdateError as exc:
            self.done.emit(None, str(exc))
            return
        self.done.emit(self._release, "")


class UpdatePanel(QWidget):
    """Версия, проверка и установка обновления одной кнопкой.

    `on_restart` — кому передать просьбу подняться заново: поставленная
    версия лежит на диске, а работает всё ещё прежняя, и перезапуск делает
    тот, кто окно открыл (трей). Без него кнопке превращаться не во что.
    """

    def __init__(
        self,
        config_path: Path | None,
        palette: theme.Palette,
        on_restart=None,
    ):
        super().__init__()
        from . import __version__

        self._config_path = config_path
        self._palette = palette
        self._on_restart = on_restart
        self._release = None
        self._thread: QThread | None = None
        self._worker: _UpdateWorker | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(0, GAP, 0, 0)
        column.setSpacing(GAP // 2)

        version = QHBoxLayout()
        version.setSpacing(GAP)
        version.addWidget(QLabel("Версия"))
        value = QLabel(__version__)
        value.setProperty("role", "value")
        version.addWidget(value)
        version.addStretch(1)
        column.addLayout(version)

        self.button = QPushButton("Проверить обновления")
        self.button.clicked.connect(self.check)
        column.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignLeft)

        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        column.addWidget(self.status)

    # --- действия --------------------------------------------------------

    def check(self) -> None:
        self._work("check", "Спрашиваю github…")

    def install(self) -> None:
        self._work("install", f"Скачиваю {self._release.name}…")

    def _work(self, what: str, message: str) -> None:
        if self._thread is not None:
            return
        self.button.setEnabled(False)
        self._say(message)

        directory = (self._config_path or config_module.config_path()).parent
        self._worker = _UpdateWorker(directory, self._release)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._worker.done.connect(lambda release, error: self._finish(what, release, error))
        self._thread.started.connect(
            self._worker.check if what == "check" else self._worker.install
        )
        self._thread.start()

    def _finish(self, what: str, release, error: str) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None
        self.button.setEnabled(True)

        if error:
            self._say(error, bad=True)
            return
        if what == "install":
            if self._on_restart is None:
                self._say("Обновлено. Заработает при следующем запуске.", ok=True)
                self._rebind("Проверить обновления", self.check)
                return
            # версия скачана и лежит на месте, а в памяти всё ещё прежняя:
            # предложить один щелчок честнее, чем отправить человека
            # закрывать и открывать программу самому
            self._say("Обновлено. Осталось перезапустить.", ok=True)
            self._rebind("Перезапустить snapreel", self._on_restart)
            return
        if release is None:
            self._say("Установлена последняя версия", ok=True)
            return
        self._release = release
        self._rebind(f"Обновить до {release.name}", self.install)
        self._say(f"Есть версия {release.name}")

    def _rebind(self, text: str, action) -> None:
        self.button.setText(text)
        self.button.clicked.disconnect()
        self.button.clicked.connect(action)

    def _say(self, text: str, ok: bool = False, bad: bool = False) -> None:
        _restyle(self.status, "ok" if ok else "error" if bad else "hint", text)


class SettingsWindow(QDialog):
    """Окно целиком: разделы слева, строки настроек справа, сохранение внизу."""

    def __init__(
        self,
        config: Config,
        path: Path | None = None,
        hotkey_note: str | None = None,
        on_restart=None,
    ):
        super().__init__()
        self.config = config
        self.path = path
        self.saved = False
        # кому отдать просьбу подняться заново после обновления; None —
        # окно открыли само по себе, и перезапускать нечего
        self._on_restart = on_restart
        # чем кончилась привязка у того, кто окно открыл: трей знает это
        # точно, а окно само проверить не может — комбинации уже заняты им
        self.hotkey_note = hotkey_note
        self.palette = theme.resolve(getattr(config, "theme", "auto"))
        self._widgets: dict[str, object] = {}
        self._hints: dict[str, QLabel] = {}
        self._order: list[str] = []

        self.setWindowTitle("snapreel")
        icon = resources.icon()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(760, 540)
        self.setMinimumSize(680, 470)
        self._build()

    # --- сборка ----------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(PAD, PAD, PAD, PAD)
        outer.setSpacing(GAP)

        body = QHBoxLayout()
        body.setSpacing(GAP)
        outer.addLayout(body, 1)

        self.nav = QListWidget()
        self.nav.setFixedWidth(SIDEBAR_WIDTH)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.currentRowChanged.connect(self._select)
        body.addWidget(self.nav)

        panel = QFrame()
        panel.setObjectName("Panel")
        inside = QVBoxLayout(panel)
        inside.setContentsMargins(1, 1, 1, 1)
        body.addWidget(panel, 1)

        self.stack = QStackedWidget()
        inside.addWidget(self.stack)

        values = settings.values_of(self.config)
        for group in settings.GROUPS:
            self.nav.addItem(QListWidgetItem(group.title))
            self._order.append(group.title)
            self.stack.addWidget(self._page(group, values))
        self.nav.setCurrentRow(0)

        footer = QHBoxLayout()
        footer.setSpacing(GAP // 2)
        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)

        close = QPushButton("Закрыть")
        close.clicked.connect(self.close)
        save = QPushButton("Сохранить")
        save.setProperty("role", "accent")
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(close)
        footer.addWidget(save)
        outer.addLayout(footer)

    def _page(self, group: settings.Group, values: dict) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        page = QWidget()
        page.setObjectName("Page")
        column = QVBoxLayout(page)
        column.setContentsMargins(PAD + 2, PAD, PAD + 2, PAD)
        column.setSpacing(0)

        title = QLabel(group.title)
        title.setProperty("role", "title")
        column.addWidget(title)
        column.addSpacing(GAP // 2)

        for index, field in enumerate(group.fields):
            if index:
                column.addWidget(_divider())
            column.addWidget(self._row(field, values[field.name]))

        if group.title == HOTKEY_GROUP:
            column.addWidget(_divider())
            column.addWidget(_hotkey_status(self.hotkey_note))

        if group.title == UPDATE_GROUP:
            column.addWidget(_divider())
            self.updates = UpdatePanel(
                self.path, self.palette, self._restart if self._on_restart else None
            )
            column.addWidget(self.updates)

        column.addStretch(1)
        area.setWidget(page)
        return area

    def _restart(self) -> None:
        """Сначала закрыть окно, потом просить о перезапуске.

        Окно модально на всё приложение, и подниматься заново поверх него
        нечему: просьбу исполняет тот, кто окно открыл.
        """
        self.close()
        if self._on_restart is not None:
            self._on_restart()

    def _row(self, field: settings.Field, value) -> QWidget:
        """Строка настройки: слева название и пояснение, справа контрол."""
        row = QWidget()
        row.setObjectName("Row")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, GAP, 0, GAP)
        line.setSpacing(GAP)

        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(QLabel(field.label))
        hint = QLabel(field.hint)
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        hint.setVisible(bool(field.hint))
        texts.addWidget(hint)
        self._hints[field.name] = hint
        line.addLayout(texts, 1)

        control = self._control(field, value)
        self._widgets[field.name] = control
        line.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return row

    def _control(self, field: settings.Field, value) -> QWidget:
        if field.kind == "hotkey":
            widget = HotkeyEdit(str(value), self.palette)
            widget.setFixedWidth(HOTKEY_WIDTH)
            return widget
        elif field.kind == "bool":
            return Switch(bool(value), self.palette)
        elif field.kind == "audio":
            widget = AudioBox(str(value), self.config)
        elif field.kind == "choice":
            widget = QComboBox()
            names = dict(field.labels)
            for choice in field.choices:
                # подпись видит человек, а в конфиг уходит значение рядом с ней
                widget.addItem(names.get(choice, choice), choice)
            widget.setCurrentIndex(max(widget.findData(str(value)), 0))
        elif field.kind == "dir":
            widget = DirEdit(str(value))
        else:
            widget = QLineEdit(str(value))
        widget.setFixedWidth(CONTROL_WIDTH)
        return widget

    def _select(self, row: int) -> None:
        self.stack.setCurrentIndex(row)

    def closeEvent(self, event) -> None:
        """Окно уходит только вместе со своими потоками."""
        for widget in self._widgets.values():
            if isinstance(widget, AudioBox):
                widget.stop()
        super().closeEvent(event)

    # --- сохранение ------------------------------------------------------

    def _collect(self) -> dict[str, object]:
        raw: dict[str, object] = {}
        for name, widget in self._widgets.items():
            if isinstance(widget, (HotkeyEdit, DirEdit)):
                raw[name] = widget.get()
            elif isinstance(widget, Switch):
                raw[name] = widget.value()
            elif isinstance(widget, AudioBox):
                raw[name] = widget.text()
            elif isinstance(widget, QComboBox):
                chosen = widget.currentData()
                raw[name] = widget.currentText() if chosen is None else str(chosen)
            else:
                raw[name] = widget.text()
        return raw

    def _save(self) -> None:
        config, errors = settings.parse(self._collect(), self.config)
        self._show_errors(errors)
        if errors:
            self._tell("Не сохранено: поправьте отмеченное красным.", bad=True)
            self.nav.setCurrentRow(self._order.index(_first_group_with(errors)))
            return

        try:
            config_module.save(config, self.path)
        except OSError as exc:
            self._tell(f"Не записать конфиг: {exc}", bad=True)
            return

        self.config = config
        self.saved = True
        # тире, а не точка: продолжение приходит из `autostart` и `hotkeys`
        # строчной буквой, и «Сохранено. комбинации слушает…» читается как
        # оборванная фраза
        self._tell(f"Сохранено — {self._apply_hotkey(config)}", ok=True)

    def _apply_hotkey(self, config: Config) -> str:
        """Хоткей регистрируется системой и не везде автоматически.

        Спокойный отказ («в системе так не принято») — не беда, пока
        комбинации слушает сама иконка: об этом и говорим, одной строкой.
        Длинный совет «назначьте средствами рабочего стола вот такую
        команду» нужен только там, где слушать их некому, — в Wayland; в
        остальных случаях он занимал три строки под кнопками и пугал на
        ровном месте. А вот сорвавшаяся регистрация показывается как есть:
        это уже поломка, и молчать о ней нельзя.
        """
        from . import hotkeys

        try:
            outcome = autostart.install(config.hotkey_mp4)
        except autostart.HotkeySetupError as exc:
            # сорвавшаяся регистрация — это не «здесь так не принято», а
            # поломка: не приняли gsettings, не отработал powershell. Такое
            # прячут только вместе с ярлыком, который человек ждёт
            return str(exc)
        if outcome.ok:
            return outcome.message
        try:
            listened = hotkeys.why_silent() is None
        except Exception:  # подсказка не повод ронять сохранение
            listened = False
        return "комбинации слушает иконка в трее" if listened else outcome.message

    def _tell(self, text: str, ok: bool = False, bad: bool = False) -> None:
        _restyle(self.status, "ok" if ok else "error" if bad else "hint", text)

    def _show_errors(self, errors: dict[str, str]) -> None:
        for field in settings.FIELDS:
            hint = self._hints[field.name]
            broken = field.name in errors
            _restyle(hint, "error" if broken else "hint", errors.get(field.name, field.hint))
            hint.setVisible(bool(hint.text()))


def _restyle(label: QLabel, role: str, text: str) -> None:
    """Меняет роль подписи: Qt перечитывает таблицу стилей только по просьбе."""
    label.setProperty("role", role)
    label.setText(text)
    label.style().unpolish(label)
    label.style().polish(label)


def _hotkey_status(note: str | None = None) -> QLabel:
    """Строка о том, слышны ли комбинации в этой сессии, и почему нет.

    Раньше причина уходила в `stderr`, которого у оконной сборки нет: человек
    видел лишь то, что ни одна комбинация не работает, и объяснить это было
    некому. `note` — чем кончилась привязка у трея; он знает это точнее, чем
    любая проверка отсюда: комбинации уже держит он сам.
    """
    from . import hotkeys

    try:
        refusal = note or hotkeys.why_silent()
    except Exception as exc:  # окно настроек не падает из-за подсказки
        refusal = f"не проверить: {exc}"

    label = QLabel()
    label.setWordWrap(True)
    label.setContentsMargins(0, GAP, 0, 0)
    if refusal is None:
        _restyle(label, "ok", "Комбинации слушает иконка в трее — назначать их в системе не нужно.")
    else:
        _restyle(label, "error", refusal)
    return label


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


def _first_group_with(errors: dict[str, str]) -> str:
    """Раздел, который надо показать: ошибку прячет тот, кто её не открывает."""
    for group in settings.GROUPS:
        if any(field.name in errors for field in group.fields):
            return group.title
    return settings.GROUPS[0].title


def open_settings(
    config: Config,
    path: Path | None = None,
    hotkey_note: str | None = None,
    on_restart=None,
) -> bool:
    """Показывает окно настроек. True — пользователь сохранил изменения.

    Диалог крутит свой цикл событий: так окно ждёт человека и в одиночном
    запуске, и внутри трея, где цикл уже идёт. `on_restart` есть только у
    второго: поставленное обновление поднимает трей, а не окно.
    """
    from .qt import application

    application(config)
    window = SettingsWindow(config, path, hotkey_note, on_restart)
    window.exec()
    return window.saved

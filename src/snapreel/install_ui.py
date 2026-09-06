"""Окно установки: одно на всю жизнь программы.

Скачанный бинарник спрашивает один раз — куда он ляжет и поднимать ли его
при входе в систему. Дальше он живёт в трее и это окно больше не показывает.

Модуль импортируется лениво, как и остальной GUI: без Qt установка делается
командой `snapreel install`.
"""

from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from . import install, resources, theme
from .config import Config
from .settings_ui import GAP, PAD, Switch, _restyle


class InstallWindow(QDialog):
    """Путь, переключатель автозапуска и две кнопки — больше здесь нечему быть."""

    def __init__(self, config: Config):
        super().__init__()
        self.plan = install.plan()
        self.installed = False
        self.palette = theme.resolve(getattr(config, "theme", "auto"))

        self.setWindowTitle("snapreel")
        icon = resources.icon()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))
        self.setFixedWidth(460)
        self._build()

    def _build(self) -> None:
        column = QVBoxLayout(self)
        column.setContentsMargins(PAD + 4, PAD + 4, PAD + 4, PAD + 4)
        column.setSpacing(GAP)

        title = QLabel("Установить snapreel")
        title.setProperty("role", "title")
        column.addWidget(title)

        hint = QLabel("Программа будет лежать здесь и подниматься сама при входе:")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        column.addWidget(hint)

        path = QLineEdit(str(self.plan.target))
        path.setReadOnly(True)
        path.setCursorPosition(0)
        column.addWidget(path)

        row = QHBoxLayout()
        row.setSpacing(GAP)
        self.autostart = Switch(True, self.palette)
        row.addWidget(self.autostart)
        row.addWidget(QLabel("Запускать при входе в систему"), 1)
        column.addLayout(row)

        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        column.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setSpacing(GAP // 2)
        buttons.addStretch(1)
        later = QPushButton("Не сейчас")
        later.clicked.connect(self.close)
        self.button = QPushButton("Установить")
        self.button.setProperty("role", "accent")
        self.button.setDefault(True)
        self.button.clicked.connect(self._install)
        buttons.addWidget(later)
        buttons.addWidget(self.button)
        column.addLayout(buttons)

    # --- действие --------------------------------------------------------

    def _install(self) -> None:
        self.button.setEnabled(False)
        _restyle(self.status, "hint", "Устанавливаю…")
        self.repaint()

        with_autostart = self.autostart.value()
        outcome = install.install(with_autostart=with_autostart)
        if not outcome.ok:
            _restyle(self.status, "error", outcome.message)
            self.button.setEnabled(True)
            return

        self.installed = install.launch(self.plan.target, autostarted=with_autostart)
        if not self.installed:
            # поставили, но поднять не смогли: пусть человек запустит сам, а
            # не гадает, почему иконки нет
            _restyle(self.status, "ok", f"{outcome.message}. Запустите его сами.")
            self.button.setText("Готово")
            self.button.setEnabled(True)
            self.button.clicked.disconnect()
            self.button.clicked.connect(self.close)
            return
        self.close()


def ask_install(config: Config) -> bool:
    """Показывает окно установки. True — установили и подняли новую копию.

    Диалог крутит свой цикл событий: он ждёт человека и внутри трея, где
    цикл уже идёт, и в одиночном запуске, где его ещё нет.
    """
    from .qt import application

    application(config)
    window = InstallWindow(config)
    window.exec()
    return window.installed

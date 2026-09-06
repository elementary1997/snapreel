"""Общее для всех окон Qt: приложение, тема и иконка.

Приложение Qt в процессе одно. Из трея окна открываются внутри уже
работающего цикла событий, а из командной строки — заводят свой; кто именно
завёл, знает вызывающий, поэтому `application` и говорит об этом вторым
значением.

Qt импортируется только здесь и в самих окнах: `doctor`, `prune` и запись по
`--region` обязаны работать там, где Qt не установлен.
"""

from __future__ import annotations

import sys

from . import resources, theme
from .config import Config
from .errors import OverlayUnavailable

# Платформы Qt, при которых окон не будет: их выбирают, когда настоящая не
# поднялась (или когда так попросили тесты).
BLIND = ("minimal", "offscreen", "vnc")


def application(config: Config | None = None) -> tuple[object, bool]:
    """Приложение Qt и признак «его завели мы».

    Второй раз приложение не создаётся: Qt этого не допускает, а окно
    настроек открывается и из трея, где цикл событий уже крутится.
    """
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        raise OverlayUnavailable(
            "нет PySide6 — окна показать нечем. Поставьте: pip install 'snapreel[ui]'"
        ) from exc

    existing = QApplication.instance()
    if existing is not None:
        return existing, False

    app = QApplication(sys.argv[:1])
    app.setApplicationName("snapreel")
    app.setApplicationDisplayName("snapreel")
    # окно настроек закрывается, а трей остаётся жить: без этого Qt погасил бы
    # приложение вместе с последним окном
    app.setQuitOnLastWindowClosed(False)

    icon = resources.icon()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))

    theme.apply(app, theme.resolve(getattr(config, "theme", "auto") if config else "auto"))

    # Qt молча откатывается на «платформу» без окон, если не смог поднять
    # настоящую: на Linux ему с версии 6.5 нужен libxcb-cursor0, и без него
    # окно просто не появляется — команда отрабатывает и ничего не показывает.
    # Молчаливый отказ хуже любого сообщения, поэтому проверяем сами.
    if app.platformName() in BLIND:
        raise OverlayUnavailable(
            f"Qt поднялся без окон (платформа «{app.platformName()}»). "
            "На Linux ему нужен libxcb-cursor0 — поставьте его "
            "(apt install libxcb-cursor0) и запустите снова."
        )
    return app, True

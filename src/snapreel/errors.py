"""Исключения, которые нужны CLI до того, как подтянется GUI-код.

Держим их отдельно: `snapreel doctor` обязан работать и без Qt — иначе он не
сможет сказать, что Qt как раз и не хватает.
"""

from __future__ import annotations


class SelectionCancelled(RuntimeError):
    """Пользователь передумал выделять область."""


class OverlayUnavailable(RuntimeError):
    """Оверлей показать нечем — нет Qt или нет доступа к дисплею."""


class TrayUnavailable(RuntimeError):
    """Иконку в трее показать нечем — нет Qt или нет самого трея."""

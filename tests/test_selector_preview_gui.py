"""Проверка перевода выделения со снимка в координаты разрешённых экранов."""

import pytest

from snapreel.region import Region

pytestmark = pytest.mark.gui


def test_preview_selection_scales_the_drag_without_adding_an_extra_pixel(tmp_path):
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from snapreel.selector import select_preview

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    path = tmp_path / "screen.png"
    image = QImage(1024, 512, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(path))
    timer = QTimer()
    timer.setInterval(20)

    def drag():
        dialog = app.activeModalWidget()
        if dialog is None:
            return
        timer.stop()
        dialog.resize(512, 256)
        area = dialog.image_rect()
        start = QPoint(area.x() + 64, area.y() + 32)
        end = QPoint(area.x() + 192, area.y() + 96)
        QTest.mousePress(dialog, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog, end)
        QTest.mouseRelease(dialog, Qt.MouseButton.LeftButton, pos=end)

    timer.timeout.connect(drag)
    timer.start()
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: app.activeModalWidget() and app.activeModalWidget().reject())
    watchdog.start(3000)
    try:
        region = select_preview(path, Region(-1000, -200, 4096, 2048))
        assert region == Region(-488, 56, 1024, 512)
    finally:
        timer.stop()
        watchdog.stop()

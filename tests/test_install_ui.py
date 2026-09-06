"""Окно установки: каталог выбирают проводником, а не печатают."""

from __future__ import annotations

import pytest

from snapreel.config import Config

try:
    from PySide6.QtWidgets import QApplication, QFileDialog
except Exception as exc:  # системной библиотеки может не быть — тогда пропускаем
    pytest.skip(f"PySide6 недоступен: {exc}", allow_module_level=True)

from pathlib import Path

from snapreel import install


@pytest.fixture
def window(monkeypatch, tmp_path):
    from snapreel.install_ui import InstallWindow

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        install,
        "plan",
        lambda env=None, folder=None: install.Plan(
            tmp_path / "Downloads" / "snapreel", tmp_path / "куда-то" / "snapreel", False
        ),
    )
    widget = InstallWindow(Config())
    yield widget
    widget.deleteLater()


def test_the_folder_starts_at_the_default_place(window, tmp_path):
    assert window.path.text() == str(tmp_path / "куда-то")
    assert window.folder() is None  # ничего не выбирали — ставим по умолчанию


def test_a_picked_folder_replaces_the_default(window, monkeypatch, tmp_path):
    chosen = tmp_path / "Программы"
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(chosen))
    )

    window._pick()

    assert window.path.text() == str(chosen)
    assert window.folder() == chosen


def test_a_cancelled_choice_changes_nothing(window, monkeypatch):
    before = window.path.text()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))

    window._pick()

    assert window.path.text() == before
    assert window.folder() is None


def test_a_typed_home_path_is_expanded(window):
    window.path.setText("~/Программы")

    assert window.folder() == Path("~/Программы").expanduser()

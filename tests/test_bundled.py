"""Вшитый в собранный бинарник ffmpeg: когда он берётся, а когда нет."""

from __future__ import annotations

import sys

import pytest

from snapreel import bundled
from snapreel.config import Config, to_toml


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Делает вид, что код запущен из бинарника с распакованным бандлом."""

    def install(name="ffmpeg"):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        binary = tmp_path / name
        binary.write_text("", encoding="utf-8")
        binary.chmod(0o755)
        return binary

    return install


def test_the_binary_carries_its_own_ffmpeg(frozen):
    binary = frozen()

    assert Config().ffmpeg_path == str(binary)


def test_a_chosen_ffmpeg_wins_over_the_bundled_one(frozen):
    """Пользователь указал свой ffmpeg — значит он и запускается."""
    frozen()

    assert Config(ffmpeg="/opt/ffmpeg/bin/ffmpeg").ffmpeg_path == "/opt/ffmpeg/bin/ffmpeg"


def test_without_a_bundle_ffmpeg_comes_from_path():
    assert Config().ffmpeg_path == "ffmpeg"


def test_a_bundle_outside_a_frozen_binary_is_ignored(monkeypatch, tmp_path):
    """`_MEIPASS` без `frozen` — чужая переменная, а не наш бандл."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    (tmp_path / "ffmpeg").write_text("", encoding="utf-8")

    assert bundled.binary("ffmpeg") is None
    assert Config().ffmpeg_path == "ffmpeg"


def test_a_missing_bundle_does_not_break_the_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "нет-такого"), raising=False)

    assert bundled.binary("ffmpeg") is None


def test_the_bundled_path_never_reaches_the_config_file(frozen):
    """Каталог бандла у каждого запуска свой, поэтому в файле ему не место."""
    binary = frozen()
    config = Config()

    assert config.ffmpeg_path == str(binary)
    assert 'ffmpeg = "ffmpeg"' in to_toml(config)
    assert str(binary) not in to_toml(config)

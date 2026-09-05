import time
from datetime import datetime

import pytest

from snapreel import config as config_module
from snapreel import storage
from snapreel.config import Config


def test_defaults_are_valid():
    Config().validate()


def test_min_greater_than_max_is_rejected():
    with pytest.raises(ValueError):
        Config(min_seconds=90, max_seconds=60).validate()


def test_file_values_override_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("SNAPREEL_FPS", raising=False)
    path = tmp_path / "config.toml"
    path.write_text("fps = 24\nmax_seconds = 15.0\nnotify = false\n", encoding="utf-8")
    cfg = config_module.load(path)
    assert (cfg.fps, cfg.max_seconds, cfg.notify) == (24, 15.0, False)


def test_env_beats_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("fps = 24\n", encoding="utf-8")
    monkeypatch.setenv("SNAPREEL_FPS", "50")
    monkeypatch.setenv("SNAPREEL_CAPTURE_CURSOR", "no")
    cfg = config_module.load(path)
    assert cfg.fps == 50
    assert cfg.capture_cursor is False


def test_missing_config_file_is_fine(tmp_path):
    assert config_module.load(tmp_path / "nope.toml").fps == Config().fps


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('fps = 20\nwat = "?"\n', encoding="utf-8")
    assert config_module.load(path).fps == 20


def test_default_toml_round_trips(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(config_module.dump_default_toml(), encoding="utf-8")
    assert config_module.load(path) == Config()


def test_new_path_avoids_collisions(tmp_path):
    cfg = Config(output_dir=str(tmp_path), filename_template="clip")
    moment = datetime(2026, 9, 5, 12, 0, 0)
    first = storage.new_path(cfg, ".mp4", moment)
    first.touch()
    second = storage.new_path(cfg, ".mp4", moment)
    assert first.name == "clip.mp4"
    assert second.name == "clip-2.mp4"


def test_prune_removes_only_old_clips(tmp_path):
    # имена обязаны совпадать с шаблоном: чужие видео prune не трогает,
    # см. tests/test_regressions.py::test_prune_spares_foreign_videos
    cfg = Config(output_dir=str(tmp_path), keep_days=7)
    old = tmp_path / "snapreel-20240101-100000.mp4"
    fresh = tmp_path / "snapreel-20260905-100000.mp4"
    other = tmp_path / "notes.txt"
    for path in (old, fresh, other):
        path.touch()
    stale = time.time() - 10 * 86400
    import os

    os.utime(old, (stale, stale))
    os.utime(other, (stale, stale))

    removed = storage.prune(cfg)

    assert removed == [old]
    assert fresh.exists() and other.exists()


def test_prune_disabled_by_zero(tmp_path):
    cfg = Config(output_dir=str(tmp_path), keep_days=0)
    clip = tmp_path / "snapreel-20240101-100000.mp4"
    clip.touch()
    stale = time.time() - 999 * 86400
    import os

    os.utime(clip, (stale, stale))
    assert storage.prune(cfg) == []
    assert clip.exists()

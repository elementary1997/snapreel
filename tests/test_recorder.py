"""Сценарий записи целиком, но без экрана и без ffmpeg."""

from __future__ import annotations

import pytest

from snapreel import recorder
from snapreel.backends import CaptureError
from snapreel.config import Config
from snapreel.platform_info import Environment, Platform
from snapreel.region import Region

ENV = Environment(Platform.LINUX_X11, is_wsl=False)
DESKTOP = Region(0, 0, 1920, 1080)


class FakeRecording:
    def __init__(self, output, payload=b"video-bytes"):
        self.output = output
        self.payload = payload
        self.stopped = 0
        self.stderr_tail = ""

    @property
    def finished(self) -> bool:
        return True

    @property
    def elapsed(self) -> float:
        return 1.0

    def stop(self, timeout: float = 10.0) -> int:
        self.stopped += 1
        if self.payload:
            self.output.write_bytes(self.payload)
        return 0


class FakeBackend:
    name = "fake"

    def __init__(self, config, payload=b"video-bytes"):
        self.config = config
        self.payload = payload
        self.region = None

    def preflight(self):
        return []

    def start(self, region, output, duration):
        self.region = region
        self.duration = duration
        output.parent.mkdir(parents=True, exist_ok=True)
        return FakeRecording(output, self.payload)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Подменяет всё, что требует живого экрана."""
    backends = {}

    def install(payload=b"video-bytes"):
        backend = FakeBackend(Config(), payload)
        backends["backend"] = backend
        monkeypatch.setattr(recorder, "for_environment", lambda cfg, env: backend)
        return backend

    monkeypatch.setattr(recorder, "virtual_desktop", lambda env: DESKTOP)
    monkeypatch.setattr(recorder, "enable_dpi_awareness", lambda: None)
    monkeypatch.setattr(recorder, "probe", lambda path, cfg: None)
    monkeypatch.setattr(recorder.notify, "send", lambda *a, **k: None)
    monkeypatch.setattr(recorder, "_indicator_class", lambda: None)
    copied = []
    monkeypatch.setattr(recorder.clipboard, "copy_files", lambda paths, env: copied.extend(paths))
    monkeypatch.setattr(recorder.clipboard, "copy_text", lambda text, env: copied.append(text))
    install.copied = copied
    install.backends = backends
    return install


def config_for(tmp_path, **kwargs) -> Config:
    return Config(output_dir=str(tmp_path / "clips"), notify=False, **kwargs)


def test_records_and_copies_file(wired, tmp_path):
    wired()
    cfg = config_for(tmp_path)

    result = recorder.record(cfg, region=Region(10, 20, 640, 480), indicator=False, env=ENV)

    assert result.video.is_file()
    assert result.clipboard_ok
    assert wired.copied == [result.video]


def test_region_is_clamped_and_evened(wired, tmp_path):
    backend = wired()

    recorder.record(
        config_for(tmp_path), region=Region(1900, 0, 401, 301), indicator=False, env=ENV
    )

    # 1900 + 401 выходит за 1920, остаток обрезан и приведён к чётному
    assert backend.region == Region(1900, 0, 20, 300)


def test_clipboard_failure_keeps_the_clip(wired, tmp_path, monkeypatch):
    wired()

    def explode(paths, env):
        raise RuntimeError("буфер занят")

    monkeypatch.setattr(recorder.clipboard, "copy_files", explode)

    result = recorder.record(
        config_for(tmp_path), region=Region(0, 0, 100, 100), indicator=False, env=ENV
    )

    assert result.video.is_file()
    assert result.clipboard_ok is False
    assert "буфер занят" in result.clipboard_error


def test_empty_output_is_an_error(wired, tmp_path):
    wired(payload=b"")

    with pytest.raises(CaptureError):
        recorder.record(
            config_for(tmp_path), region=Region(0, 0, 100, 100), indicator=False, env=ENV
        )


def test_gif_mode_copies_gif_not_mp4(wired, tmp_path, monkeypatch):
    wired()

    def fake_gif(source, target, config):
        target.write_bytes(b"GIF89a")
        return target

    monkeypatch.setattr(recorder, "to_gif", fake_gif)

    result = recorder.record(
        config_for(tmp_path), region=Region(0, 0, 100, 100), as_gif=True, indicator=False, env=ENV
    )

    assert result.payload == result.gif
    assert result.gif.suffix == ".gif"
    assert wired.copied == [result.gif]


def test_path_as_text_mode(wired, tmp_path):
    wired()
    cfg = config_for(tmp_path, copy_path_as_text=True)

    result = recorder.record(cfg, region=Region(0, 0, 100, 100), indicator=False, env=ENV)

    assert wired.copied == [str(result.video)]


def test_max_seconds_reaches_the_backend(wired, tmp_path):
    backend = wired()

    recorder.record(
        config_for(tmp_path, max_seconds=12.0),
        region=Region(0, 0, 100, 100),
        indicator=False,
        env=ENV,
    )

    assert backend.duration == 12.0

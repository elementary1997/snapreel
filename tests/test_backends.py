from pathlib import Path

import pytest

from snapreel.backends.linux import WfRecorderBackend, X11GrabBackend
from snapreel.backends.macos import AvFoundationBackend, _scaled_crop
from snapreel.backends.windows import GdigrabBackend
from snapreel.config import Config
from snapreel.region import Region

REGION = Region(100, 50, 640, 480)
OUT = Path("/tmp/clip.mp4")


def pair(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_gdigrab_passes_offsets_and_size():
    command = GdigrabBackend(Config()).build_command(REGION, OUT, 60)
    assert pair(command, "-f") == "gdigrab"
    assert pair(command, "-offset_x") == "100"
    assert pair(command, "-offset_y") == "50"
    assert pair(command, "-video_size") == "640x480"
    assert pair(command, "-i") == "desktop"


def test_gdigrab_handles_monitor_left_of_primary():
    command = GdigrabBackend(Config()).build_command(Region(-1920, 0, 800, 600), OUT, 10)
    assert pair(command, "-offset_x") == "-1920"


def test_x11grab_encodes_offset_into_display(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":1")
    command = X11GrabBackend(Config()).build_command(REGION, OUT, 60)
    assert pair(command, "-i") == ":1+100,50"
    assert pair(command, "-video_size") == "640x480"


def test_wf_recorder_uses_its_own_geometry_syntax():
    command = WfRecorderBackend(Config()).build_command(REGION, OUT, 60)
    assert pair(command, "--geometry") == "100,50 640x480"
    # у wf-recorder нет лимита времени — его держит таймер индикатора
    assert "-t" not in command


def test_wf_recorder_stops_by_signal():
    assert WfRecorderBackend(Config()).graceful_stop == "sigint"


def test_avfoundation_crops_scaled_region(monkeypatch):
    backend = AvFoundationBackend(Config())
    monkeypatch.setattr(backend, "screen_index", lambda: 3)
    monkeypatch.setattr(backend, "scale", lambda: 2.0)
    command = backend.build_command(REGION, OUT, 30)
    assert pair(command, "-i") == "3:none"
    assert pair(command, "-vf") == "crop=1280:960:200:100"


def test_scaled_crop_keeps_sides_even():
    _x, _y, width, height = _scaled_crop(Region(0, 0, 101, 51), 1.0)
    assert (width, height) == (100, 50)


@pytest.mark.parametrize("backend_cls", [GdigrabBackend, X11GrabBackend, AvFoundationBackend])
def test_duration_limit_is_passed_to_ffmpeg(backend_cls, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    backend = backend_cls(Config())
    if isinstance(backend, AvFoundationBackend):
        monkeypatch.setattr(backend, "screen_index", lambda: 1)
        monkeypatch.setattr(backend, "scale", lambda: 1.0)
    command = backend.build_command(REGION, OUT, 12.5)
    assert pair(command, "-t") == "12.500"


def test_audio_is_off_unless_asked():
    assert "-an" in GdigrabBackend(Config()).build_command(REGION, OUT, 10)


def test_audio_device_adds_dshow_input():
    config = Config(capture_audio=True, audio_device="Стерео микшер")
    command = GdigrabBackend(config).build_command(REGION, OUT, 10)
    assert "audio=Стерео микшер" in command
    assert "-an" not in command

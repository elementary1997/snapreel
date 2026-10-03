"""Портал и PipeWire подменены: тесты не получают доступ к экрану."""

import os
from pathlib import Path
from typing import ClassVar

import pytest

from snapreel import screencast
from snapreel.backends.base import CaptureError
from snapreel.backends.portal import build_pipeline, desktop
from snapreel.config import Config
from snapreel.region import Region
from snapreel.screencast import Stream


def test_capture_scales_before_cropping_and_preserves_negative_monitor_positions():
    streams = [Stream(12, Region(-1920, 0, 1920, 1080), 7)]
    pipeline = build_pipeline(
        Config(), streams, Region(-1800, 100, 640, 480), Path("/tmp/video.mp4")
    )
    assert "width=1920,height=1080" in pipeline
    assert "videocrop left=120 top=100 right=1160 bottom=500" in pipeline
    assert pipeline.index("videoscale") < pipeline.index("videocrop")
    assert "format=I420,framerate=30/1" in pipeline
    assert "videorate drop-only=true" in pipeline
    assert "mp4mux name=mux faststart=true" in pipeline


def test_a_region_across_two_monitors_composes_only_the_intersections():
    streams = [Stream(1, Region(-640, 0, 640, 480), 4), Stream(2, Region(0, 0, 640, 480), 5)]
    pipeline = build_pipeline(Config(), streams, Region(-100, 50, 300, 200), Path("/tmp/video.mp4"))
    assert "sink_0::xpos=0" in pipeline
    assert "sink_1::xpos=100" in pipeline
    assert "left=540 top=50 right=0 bottom=230" in pipeline
    assert "left=0 top=50 right=440 bottom=230" in pipeline
    assert desktop(streams) == Region(-640, 0, 1280, 480)


def test_a_region_outside_the_permitted_monitors_is_rejected():
    with pytest.raises(CaptureError, match="вне разрешённых"):
        build_pipeline(
            Config(),
            [Stream(1, Region(0, 0, 640, 480), 4)],
            Region(700, 0, 100, 100),
            Path("/tmp/video.mp4"),
        )


def test_audio_and_file_paths_cannot_inject_pipeline_elements(tmp_path):
    output = tmp_path / 'a " ! fakesink.mp4'
    pipeline = build_pipeline(
        Config(capture_audio=True, audio_device="device ! fakesink"),
        [Stream(1, Region(0, 0, 640, 480), 4)],
        Region(0, 0, 640, 480),
        output,
    )
    assert 'device="device ! fakesink"' in pipeline
    escaped = str(output).replace("\\", "\\\\").replace('"', '\\"')
    assert f'location="{escaped}"' in pipeline
    assert "avenc_aac bitrate=128000" in pipeline


@pytest.fixture
def portal(monkeypatch, tmp_path):
    calls = []

    class Glib:
        def g_main_context_new(self):
            return object()

        def g_main_context_push_thread_default(self, context):
            pass

        def g_main_context_pop_thread_default(self, context):
            pass

        def g_main_context_unref(self, context):
            pass

        def g_main_context_iteration(self, context, blocking):
            pass

    class Gio:
        glib = Glib()
        properties: ClassVar = {"size": [5120, 1440]}
        failure = False

        def register_app(self):
            pass

        def request(self, context, stopped, method, args, interface):
            calls.append((method, args("@a{sv} {'handle_token': <'test'>}")))
            if method == "CreateSession":
                return {"session_handle": "/session/test"}
            if method == "Start":
                if self.failure:
                    raise screencast.PortalError("Запись отменена")
                return {"streams": [[17, self.properties]], "restore_token": "test-token"}
            return {}

        def subscribe(self, *args):
            pass

        def open_remote(self, session):
            fd = os.open(os.devnull, os.O_RDONLY)
            calls.append(("fd", fd))
            return fd

        def call(self, path, interface, method):
            calls.append((path, method))

        def close(self):
            calls.append("disconnected")

    monkeypatch.setattr(screencast, "Gio", Gio)
    monkeypatch.setattr(screencast, "cache_path", lambda: tmp_path / "screencast.json")
    monkeypatch.setattr(screencast, "wait_ready", lambda event: event.wait(2))
    return Gio, calls, tmp_path


def test_kde_streams_without_a_position_can_be_recorded_and_closed(portal):
    _, calls, tmp_path = portal
    session = screencast.ScreenSession(True)
    session.start()
    assert session.streams[0].bounds == Region(0, 0, 5120, 1440)
    session.renew()
    fd = session.streams[0].fd
    if os.name == "posix":
        assert (tmp_path / "screencast.json").stat().st_mode & 0o777 == 0o600
    session.close()
    assert not session.thread.is_alive()
    with pytest.raises(OSError):
        os.fstat(fd)
    assert ("/session/test", "Close") in calls


def test_missing_dimensions_are_measured_from_the_permitted_stream(portal, monkeypatch):
    gio, calls, _ = portal
    gio.properties = {}
    monkeypatch.setattr(screencast, "stream_size", lambda fd, node: (1280, 720))
    session = screencast.ScreenSession(False)
    session.start()
    try:
        assert session.streams[0].bounds == Region(0, 0, 1280, 720)
    finally:
        session.close()
    assert sum(isinstance(call, tuple) and call[0] == "fd" for call in calls) == 2


def test_cancelling_permission_closes_the_portal_without_creating_a_recording(portal):
    gio, calls, _ = portal
    gio.failure = True
    session = screencast.ScreenSession(False)
    with pytest.raises(CaptureError, match="отменена"):
        session.start()
    assert ("/session/test", "Close") in calls
    assert not session.thread.is_alive()


def test_snapshot_waits_for_the_first_stream_frame_and_never_enables_audio():
    pipeline = build_pipeline(
        Config(capture_audio=True),
        [Stream(12, Region(0, 0, 1920, 1080), 7)],
        Region(0, 0, 1920, 1080),
        Path("/tmp/preview.png"),
        snapshot=True,
    )
    assert "start-time-selection=first" in pipeline
    assert "pngenc snapshot=true" in pipeline
    assert "pulsesrc" not in pipeline
    assert "x264enc" not in pipeline

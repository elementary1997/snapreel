"""Панель не перекрывает запись и остаётся рядом с выбранным монитором."""

import pytest

from snapreel.overlay_wayland import SurfaceClient, panel_position
from snapreel.region import Region


def test_panel_prefers_the_monitor_containing_the_selection():
    assert panel_position(
        Region(2800, 200, 640, 480), [Region(0, 0, 2560, 1440), Region(2560, 0, 2560, 1440)]
    ) == Region(2800, 148, 260, 44)


@pytest.mark.parametrize("region", [Region(100, 10, 640, 480), Region(-1800, -50, 300, 200)])
def test_panel_moves_below_when_there_is_no_room_above(region):
    screen = Region(0, 0, 1920, 1080) if region.x >= 0 else Region(-1920, -100, 1920, 1080)
    panel = panel_position(region, [screen])
    assert panel.y == region.bottom + 8
    assert panel.x >= screen.x and panel.right <= screen.right


def test_fullscreen_recording_falls_back_without_covering_video():
    assert panel_position(Region(0, 0, 1920, 1080), [Region(0, 0, 1920, 1080)]) is None


def test_panel_input_keeps_native_coordinates_on_negative_monitors():
    import queue
    from types import SimpleNamespace

    client = object.__new__(SurfaceClient)
    client.pointer_surface = 17
    client.pointer_position = (180.5, 21.75)
    client.surfaces = {17: (Region(-1800, 100, 260, 44), None)}
    client.owner = SimpleNamespace(events=queue.SimpleQueue())
    client.emit("release", 272)
    assert client.owner.events.get() == ("release", -1620, 121, 272)
    client.pointer_surface = None
    client.emit("release", 272)
    assert client.owner.events.empty()

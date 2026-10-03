"""Геометрия рамки не должна пересекаться с содержимым клипа."""

import pytest

from snapreel.indicator_wayland import (
    border_edges,
    desktop_region,
    visible_border_edges,
)
from snapreel.region import Region


@pytest.mark.parametrize("region", [Region(100, 200, 640, 480), Region(-900, -200, 800, 600)])
def test_every_border_pixel_stays_outside_the_recorded_rectangle(region):
    edges = border_edges(region)
    assert len(edges) == 4
    for edge in edges:
        overlap_width = min(edge.right, region.right) - max(edge.x, region.x)
        overlap_height = min(edge.bottom, region.bottom) - max(edge.y, region.y)
        assert overlap_width <= 0 or overlap_height <= 0
    assert edges[0].right == region.right + 2
    assert edges[2].bottom == region.bottom


def test_full_desktop_capture_maps_back_to_negative_monitor_coordinates():
    assert desktop_region(
        Region(100, 50, 640, 480), Region(0, 0, 3840, 1080), Region(-1920, 0, 3840, 1080)
    ) == Region(-1820, 50, 640, 480)


def test_an_ambiguous_monitor_subset_never_draws_a_border_on_another_screen():
    assert (
        desktop_region(
            Region(100, 50, 640, 480), Region(0, 0, 1920, 1080), Region(0, 0, 3840, 1080)
        )
        is None
    )


def test_a_region_touching_the_screen_keeps_the_other_three_visible_edges():
    pieces = visible_border_edges(Region(100, 0, 640, 480), [Region(0, 0, 1920, 1080)])
    assert len(pieces) == 3
    assert all(piece.y >= 0 for piece in pieces)
    assert Region(98, 480, 644, 2) in pieces


def test_a_border_crossing_monitor_edges_is_split_without_drawing_in_the_gap():
    pieces = visible_border_edges(
        Region(-100, 50, 300, 200),
        [Region(-1000, 0, 1000, 800), Region(100, 0, 1000, 800)],
    )
    assert all(piece.right <= 0 or piece.x >= 100 for piece in pieces)


def test_layer_surface_never_takes_focus_and_rejects_an_unexpected_size():
    from snapreel.indicator_wayland import LayerClient

    class Client(LayerClient):
        def __init__(self):
            self.calls = []
            self.listeners = {}
            self.serial = 100
            self.surface_interface = "surface"
            self.region_interface = "region"
            self.layer = "layer"
            self.error = None
            self.configured = 0

        def _marshal(self, proxy, opcode, interface, version, *arguments):
            self.serial += 1
            self.calls.append((proxy, opcode, interface, [a.value for a in arguments]))
            return self.serial

        def _listen(self, proxy, specifications):
            self.listeners[proxy] = specifications

    client = Client()
    client._surface(1, 2, 3, 4, Region(100, 50, 640, 2), Region(0, 0, 1920, 1080))
    layer = next(
        proxy
        for proxy, callbacks in client.listeners.items()
        if len(callbacks) == 2 and len(callbacks[0][0]) == 3
    )
    assert any(proxy == 3 and values[-2] == 3 for proxy, _, _, values in client.calls)
    assert (layer, 4, None, [0]) in client.calls
    assert (layer, 2, None, [-1]) in client.calls
    assert (101, 5, None, [102]) in client.calls
    client.listeners[layer][0][1](None, None, 1, 800, 2)
    assert client.error
    assert client.configured == 0
    assert not any(proxy == 101 and opcode == 1 for proxy, opcode, _, _ in client.calls)

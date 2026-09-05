import pytest

from snapreel.region import Region, RegionError, clamp, from_corners, normalize, parse


def test_from_corners_works_in_any_direction():
    assert from_corners(300, 200, 100, 50) == Region(100, 50, 200, 150)


def test_normalize_rounds_sides_down_to_even():
    assert normalize(Region(10, 20, 101, 51)) == Region(10, 20, 100, 50)


def test_normalize_keeps_negative_origin():
    """Монитор слева от основного даёт отрицательные координаты — это норма."""
    assert normalize(Region(-1920, -100, 640, 480)).x == -1920


def test_normalize_rejects_tiny_region():
    with pytest.raises(RegionError):
        normalize(Region(0, 0, 8, 8))


def test_clamp_trims_to_desktop():
    desktop = Region(0, 0, 1920, 1080)
    assert clamp(Region(1800, 1000, 400, 400), desktop) == Region(1800, 1000, 120, 80)


def test_clamp_rejects_offscreen_region():
    with pytest.raises(RegionError):
        clamp(Region(3000, 0, 100, 100), Region(0, 0, 1920, 1080))


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("640x480+10+20", Region(10, 20, 640, 480)),
        ("1920x1080+-1920+0", Region(-1920, 0, 1920, 1080)),
    ],
)
def test_parse_geometry(spec, expected):
    assert parse(spec) == expected


@pytest.mark.parametrize("spec", ["640x480", "640+10+20", "abc", "640x480+10"])
def test_parse_rejects_garbage(spec):
    with pytest.raises(RegionError):
        parse(spec)

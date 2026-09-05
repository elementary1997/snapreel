import sys
from pathlib import Path

import pytest

from snapreel.clipboard.posix import file_uri
from snapreel.config import Config
from snapreel.encode import MediaInfo


def test_file_uri_escapes_spaces(tmp_path):
    path = tmp_path / "мой клип.mp4"
    path.touch()
    uri = file_uri(path)
    assert uri.startswith("file:///")
    assert " " not in uri


def test_human_size_scales():
    assert MediaInfo(0, 0, 0, 900).human_size == "900 Б"
    assert MediaInfo(0, 0, 0, 2 * 1024 * 1024).human_size == "2.0 МБ"


@pytest.mark.skipif(sys.platform != "win32", reason="CF_HDROP есть только в Windows")
def test_hdrop_payload_is_utf16_and_double_terminated():
    from snapreel.clipboard.windows import build_hdrop

    payload = build_hdrop([Path("C:/tmp/clip.mp4")])
    assert payload.endswith("\0\0".encode("utf-16-le"))
    assert "clip.mp4".encode("utf-16-le") in payload


def test_gif_filter_chain_builds_palette(monkeypatch, tmp_path):
    """GIF без своей палитры выходит грязным, поэтому проверяем обе стадии."""
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        (tmp_path / "out.gif").write_bytes(b"GIF89a")

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    monkeypatch.setattr("snapreel.encode.subprocess.run", fake_run)
    from snapreel.encode import to_gif

    to_gif(tmp_path / "in.mp4", tmp_path / "out.gif", Config(gif_fps=12, gif_max_width=600))

    filters = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "fps=12" in filters
    # узкий клип не должен растягиваться до gif_max_width
    assert "min(600,iw)" in filters
    assert "palettegen" in filters and "paletteuse" in filters

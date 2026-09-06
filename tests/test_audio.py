"""Разбор списка звуковых устройств: у трёх платформ три разных формата.

Сам ffmpeg тут не запускается — проверяется только разбор его вывода, иначе
тест зависел бы от того, какая звуковая карта стоит в машине.
"""

from __future__ import annotations

from snapreel import audio

DSHOW = """
[dshow @ 0000] "Integrated Camera" (video)
[dshow @ 0000]   Alternative name "@device_pnp_\\\\?\\usb#vid_04f2"
[dshow @ 0000] DirectShow audio devices
[dshow @ 0000]  "Микрофон (Realtek(R) Audio)"
[dshow @ 0000]   Alternative name "@device_cm_{33D9A762}"
[dshow @ 0000]  "Стерео микшер (Realtek(R) Audio)"
"""

AVFOUNDATION = """
[AVFoundation indev @ 0x7f] AVFoundation video devices:
[AVFoundation indev @ 0x7f] [0] FaceTime HD Camera
[AVFoundation indev @ 0x7f] [1] Capture screen 0
[AVFoundation indev @ 0x7f] AVFoundation audio devices:
[AVFoundation indev @ 0x7f] [0] Built-in Microphone
[AVFoundation indev @ 0x7f] [1] BlackHole 2ch
"""

PACTL = (
    "0\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n"
    "1\talsa_input.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
)


def test_windows_takes_names_from_the_audio_section():
    """Видеокамера в тот же список попасть не должна."""
    found = audio.parse_dshow(DSHOW)

    assert [device.value for device in found] == [
        "Микрофон (Realtek(R) Audio)",
        "Стерео микшер (Realtek(R) Audio)",
    ]


def test_windows_ignores_alternative_names():
    """Альтернативное имя — это тот же микрофон, дважды он в списке не нужен."""
    assert all("device_cm" not in device.value for device in audio.parse_dshow(DSHOW))


def test_macos_keeps_the_index_and_shows_the_name():
    """avfoundation принимает номер входа, а человеку нужен его название."""
    found = audio.parse_avfoundation(AVFOUNDATION)

    assert [device.value for device in found] == ["0", "1"]
    assert found[0].label == "[0] Built-in Microphone"
    assert all("Camera" not in device.label for device in found)


def test_linux_names_the_monitor_source_plainly():
    found = audio.parse_pactl(PACTL)

    assert found[0].value.endswith(".monitor")
    assert "звук системы" in found[0].label
    assert found[1].value == "alsa_input.pci-0000_00_1f.3.analog-stereo"


def test_nothing_found_is_an_empty_list_not_a_crash():
    assert audio.parse_dshow("") == []
    assert audio.parse_avfoundation("пусто") == []
    assert audio.parse_pactl("") == []


def test_a_refusing_ffmpeg_leaves_the_list_empty(monkeypatch):
    """Список — удобство: если спросить не вышло, поле остаётся текстовым."""

    def refuse(*args, **kwargs):
        raise OSError("нет такого файла")

    monkeypatch.setattr(audio.subprocess, "run", refuse)

    assert audio.devices() == []

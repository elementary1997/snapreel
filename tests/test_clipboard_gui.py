"""Буфер обмена глазами чужого приложения: что оно на самом деле увидит.

Маркер `gui`: список типов и содержимое отдаёт X-сервер, и спросить его
можно только у живого. Это единственный способ убедиться, что в буфере лежит
файл, а не путь строкой, — Qt, например, к своему многоформатному буферу
добавляет `text/plain` с адресом файла, и заметно это только отсюда.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.gui


@pytest.fixture
def owner_module():
    try:
        from snapreel.clipboard import x11_owner
    except Exception as exc:  # без python-xlib проверять нечего
        pytest.skip(f"владелец буфера недоступен: {exc}")
    if not x11_owner.available():
        pytest.skip("нет X-сервера")
    return x11_owner


def ask(target: str):
    """Спрашивает у буфера один тип — так же, как это делает чужая программа."""
    from Xlib import X, display

    d = display.Display()
    window = d.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
    prop = d.intern_atom("SNAPREEL_TEST")
    window.convert_selection(d.intern_atom("CLIPBOARD"), d.intern_atom(target), prop, X.CurrentTime)
    d.flush()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if d.pending_events():
            event = d.next_event()
            if event.type == X.SelectionNotify:
                if event.property == 0:
                    return None  # такого типа нам не дали
                value = window.get_full_property(prop, X.AnyPropertyType)
                return value.value if value else None
        time.sleep(0.02)
    raise AssertionError(f"буфер не ответил про {target}")


def test_the_clipboard_offers_the_file_and_nothing_textual(owner_module, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    owner_module.copy_files([clip])
    try:
        from Xlib import display

        d = display.Display()
        names = [d.get_atom_name(atom) for atom in ask("TARGETS")]

        assert owner_module.URI_LIST in names
        assert owner_module.GNOME_FILES in names
        # именно из-за строки с путём чат вставлял текст вместо вложения
        assert "text/plain" not in names
        assert "UTF8_STRING" not in names
    finally:
        owner_module._current.stop()


def test_the_offered_file_is_the_recorded_one(owner_module, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    owner_module.copy_files([clip])
    try:
        uri = bytes(ask(owner_module.URI_LIST)).decode()
        gnome = bytes(ask(owner_module.GNOME_FILES)).decode()

        assert uri.strip() == Path(clip).resolve().as_uri()
        assert gnome.startswith("copy\n")
        assert gnome.endswith(Path(clip).resolve().as_uri())
    finally:
        owner_module._current.stop()


def test_an_unknown_type_is_refused_not_invented(owner_module, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    owner_module.copy_files([clip])
    try:
        assert ask("text/plain") is None
    finally:
        owner_module._current.stop()


def test_a_second_clip_replaces_the_first(owner_module, tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"video")
    second.write_bytes(b"video")

    owner_module.copy_files([first])
    owner_module.copy_files([second])
    try:
        assert bytes(ask(owner_module.URI_LIST)).decode().strip() == second.resolve().as_uri()
    finally:
        owner_module._current.stop()

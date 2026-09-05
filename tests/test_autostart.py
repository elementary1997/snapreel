import pytest

from snapreel import autostart
from snapreel.autostart import HotkeySetupError


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("<ctrl>+<shift>+<alt>+r", (["ctrl", "shift", "alt"], "r")),
        ("Ctrl+Shift+5", (["ctrl", "shift"], "5")),
        ("  cmd + alt + F9 ", (["super", "alt"], "f9")),
        ("WIN+PrintScreen", (["super"], "printscreen")),
    ],
)
def test_parse_accepts_both_human_and_pynput_syntax(spec, expected):
    assert autostart.parse_hotkey(spec) == expected


def test_parse_deduplicates_modifiers():
    assert autostart.parse_hotkey("ctrl+control+r")[0] == ["ctrl"]


def test_parse_needs_a_main_key():
    with pytest.raises(HotkeySetupError):
        autostart.parse_hotkey("ctrl+shift")


def test_validate_rejects_bare_key():
    """Одиночная клавиша без модификатора сломала бы обычную печать."""
    with pytest.raises(HotkeySetupError, match="модификатор"):
        autostart.validate("r")


def test_validate_rejects_unknown_named_key():
    with pytest.raises(HotkeySetupError, match="не распознана"):
        autostart.validate("ctrl+wat")


@pytest.mark.parametrize("spec", ["Ctrl+Shift+R", "alt+f4", "<ctrl>+<alt>+space", "Win+Up"])
def test_validate_accepts_reasonable_combos(spec):
    autostart.validate(spec)


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("Ctrl+Shift+R", "<ctrl>+<shift>+r"),
        ("alt+f4", "<alt>+<f4>"),
        ("<ctrl>+<alt>+space", "<ctrl>+<alt>+<space>"),
    ],
)
def test_to_pynput_normalizes(spec, expected):
    assert autostart.to_pynput(spec) == expected


def test_to_gnome_uses_angle_bracket_names():
    assert autostart.to_gnome("Ctrl+Shift+Alt+R") == "<Control><Shift><Alt>r"


def test_to_windows_uses_plus_separated_caps():
    assert autostart.to_windows("<ctrl>+<shift>+5") == "CTRL+SHIFT+5"


def test_describe_is_human_readable():
    assert autostart.describe("<ctrl>+<shift>+<alt>+r") == "Ctrl+Shift+Alt+R"
    assert autostart.describe("<alt>+<f9>") == "Alt+f9"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@as []", []),
        ("['/org/a/', '/org/b/']", ["/org/a/", "/org/b/"]),
        ("", []),
        ("мусор", []),
    ],
)
def test_gsettings_list_parsing(raw, expected):
    assert autostart.parse_gsettings_list(raw) == expected


def test_gsettings_list_round_trip():
    paths = ["/org/a/", autostart.GNOME_PATH]
    rendered = autostart.format_gsettings_list(paths)
    assert autostart.parse_gsettings_list(rendered) == paths


def test_windows_shortcut_script_carries_hotkey_and_command(tmp_path):
    script = autostart.windows_shortcut_script(tmp_path / "Snapreel.lnk", "Ctrl+Alt+5")
    assert "$link.Hotkey = 'CTRL+ALT+5'" in script
    assert "-m" in script and "snapreel" in script
    assert "$link.Save()" in script


def test_launch_argv_targets_the_running_interpreter():
    argv = autostart.launch_argv()
    assert argv[1:] == ["-m", "snapreel", "record"]
    assert autostart.launch_argv(as_gif=True)[-1] == "--gif"


def test_launch_argv_in_a_frozen_build_calls_the_binary(monkeypatch):
    """У бинарника PyInstaller нет модуля snapreel — подкоманда идёт ему самому."""
    monkeypatch.setattr(autostart, "is_frozen", lambda: True)
    monkeypatch.setattr(autostart.sys, "executable", "/opt/snapreel")

    assert autostart.launch_argv() == ["/opt/snapreel", "record"]
    assert autostart.launch_argv(as_gif=True) == ["/opt/snapreel", "record", "--gif"]


def test_quote_wraps_paths_with_spaces():
    assert autostart.quote(["/a b/python", "-m", "snapreel"]) == '"/a b/python" -m snapreel'


def test_launch_agent_plist_is_wellformed():
    from xml.etree import ElementTree

    plist = autostart.launch_agent_plist()
    ElementTree.fromstring(plist)  # бросит, если XML битый
    assert autostart.LAUNCH_AGENT in plist
    assert "<string>daemon</string>" in plist

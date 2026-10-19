import pytest

from slowmovie_inkdrop.remote import (
    COMMAND_KINDS,
    REMOTE_SETTINGS,
    check_setting_value,
    validate_command,
)


def test_the_remote_setting_list_is_closed_and_excludes_dangerous_ones():
    for forbidden in ("relay_url", "relay_token", "epd", "directory", "file", "clear", "loglevel", "start"):
        assert forbidden not in REMOTE_SETTINGS
    for needed in ("delay", "increment", "subtitles", "subtitle_track", "contrast", "active_hours"):
        assert needed in REMOTE_SETTINGS


@pytest.mark.parametrize("name, value, expected", [
    ("delay", 40, 40), ("fullscreen", True, True), ("contrast", 1, 1.0), ("contrast", 1.5, 1.5),
    ("active_hours", " 08:00-22:00 ", "08:00-22:00"), ("relay_poll_seconds", 20, 20),
])
def test_valid_setting_values(name, value, expected):
    assert check_setting_value(name, value) == expected


@pytest.mark.parametrize("name, value", [
    ("delay", "40"), ("delay", True), ("delay", 1.5), ("delay", 10**9), ("fullscreen", 1), ("fullscreen", "true"),
    ("contrast", "1"), ("contrast", float("nan")), ("contrast", True), ("active_hours", 5),
    ("active_hours", "x" * 51), ("relay_poll_seconds", 4), ("relay_poll_seconds", 301),
    ("relay_url", "https://evil"), ("relay_token", "x"), ("epd", "x"), ("nimporte", 1),
])
def test_invalid_setting_values_are_refused(name, value):
    with pytest.raises(ValueError):
        check_setting_value(name, value)


def test_simple_commands():
    for kind in ("pause", "resume", "skip"):
        assert validate_command({"kind": kind, "ignored": 1}) == {"kind": kind}


def test_film_commands_are_normalised():
    assert validate_command({"kind": "play", "video": "a.mkv"}) == {"kind": "play", "video": "a.mkv"}
    assert validate_command({"kind": "seek", "video": "a.mkv", "seconds": 90}) == {
        "kind": "seek", "video": "a.mkv", "seconds": 90.0}
    assert validate_command({"kind": "set_start", "video": "Le film (2013).mkv", "seconds": 0}) == {
        "kind": "set_start", "video": "Le film (2013).mkv", "seconds": 0.0}
    assert validate_command({"kind": "reset", "video": "a.mkv"}) == {"kind": "reset", "video": "a.mkv"}


@pytest.mark.parametrize("payload", [
    None, [], "play", {}, {"kind": "dance"}, {"kind": "play"}, {"kind": "play", "video": ""},
    {"kind": "play", "video": "../x.mkv"}, {"kind": "play", "video": "/etc/passwd"},
    {"kind": "play", "video": "a\\b.mkv"}, {"kind": "play", "video": ".cache.mkv"},
    {"kind": "play", "video": "a\x00b.mkv"}, {"kind": "play", "video": "x" * 300}, {"kind": "play", "video": 5},
    {"kind": "seek", "video": "a.mkv"}, {"kind": "seek", "video": "a.mkv", "seconds": -1},
    {"kind": "seek", "video": "a.mkv", "seconds": "10"}, {"kind": "seek", "video": "a.mkv", "seconds": True},
    {"kind": "seek", "video": "a.mkv", "seconds": 10**9},
    {"kind": "set_start", "video": "a.mkv", "seconds": float("nan")},
    {"kind": "settings"}, {"kind": "settings", "changes": {}}, {"kind": "settings", "changes": []},
    {"kind": "settings", "changes": {"relay_url": "https://evil"}},
    {"kind": "settings", "changes": {"delay": "40"}},
    {"kind": "settings", "changes": {f"delay{i}": 1 for i in range(30)}},
])
def test_invalid_commands_are_refused_with_a_message(payload):
    with pytest.raises(ValueError) as exc:
        validate_command(payload)
    assert str(exc.value)


def test_settings_command_is_normalised():
    cmd = validate_command({"kind": "settings", "changes": {"delay": 10, "contrast": 1}})
    assert cmd == {"kind": "settings", "changes": {"delay": 10, "contrast": 1.0}}


def test_command_kinds_cover_the_spec():
    assert set(COMMAND_KINDS) == {"pause", "resume", "skip", "play", "seek", "set_start", "reset", "settings"}

import pytest

from slowmovie_inkdrop.remote import (
    COMMAND_KINDS,
    REMOTE_SETTINGS,
    SECRET_SETTINGS,
    SETTING_TYPES,
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
    assert validate_command({"kind": "set_end", "video": "a.mkv", "seconds": 5400}) == {
        "kind": "set_end", "video": "a.mkv", "seconds": 5400.0}


@pytest.mark.parametrize("payload", [
    None, [], "play", {}, {"kind": "dance"}, {"kind": "play"}, {"kind": "play", "video": ""},
    {"kind": "play", "video": "../x.mkv"}, {"kind": "play", "video": "/etc/passwd"},
    {"kind": "play", "video": "a\\b.mkv"}, {"kind": "play", "video": ".cache.mkv"},
    {"kind": "play", "video": "a\x00b.mkv"}, {"kind": "play", "video": "x" * 300}, {"kind": "play", "video": 5},
    {"kind": "seek", "video": "a.mkv"}, {"kind": "seek", "video": "a.mkv", "seconds": -1},
    {"kind": "set_end", "video": "a.mkv"}, {"kind": "set_end", "video": "a.mkv", "seconds": -3},
    {"kind": "set_end", "video": "../a.mkv", "seconds": 10},
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
    assert set(COMMAND_KINDS) == {"pause", "resume", "skip", "play", "seek", "set_start", "set_end", "reset",
                                  "settings"}


ALBUM = "https://photos.google.com/share/AF1QipAbC?key=Q0pLeVV3"


def test_mode_settings_are_remote_and_the_album_link_is_secret():
    for name in ("mode", "pipiquant_interval", "photos_interval", "pipiquant_title", "photos_album_url"):
        assert name in REMOTE_SETTINGS and name in SETTING_TYPES
    assert SECRET_SETTINGS == ("photos_album_url",)


@pytest.mark.parametrize("name, value, expected", [
    ("mode", "slowmovie", "slowmovie"), ("mode", "pipiquant", "pipiquant"), ("mode", "photos", "photos"),
    ("pipiquant_interval", 120, 120), ("photos_interval", 86400, 86400), ("pipiquant_title", False, False),
    ("photos_album_url", ALBUM, ALBUM), ("photos_album_url", "", ""), ("photos_album_url", f" {ALBUM} ", ALBUM),
])
def test_valid_mode_setting_values(name, value, expected):
    assert check_setting_value(name, value) == expected


@pytest.mark.parametrize("name, value", [
    ("mode", "autre"), ("mode", ""), ("mode", 1), ("mode", None), ("mode", "PHOTOS"),
    ("pipiquant_interval", 119), ("pipiquant_interval", 86401), ("photos_interval", 0),
    ("photos_interval", True), ("photos_interval", 600.5), ("photos_interval", "600"),
    ("pipiquant_title", 1), ("pipiquant_title", "true"),
    ("photos_album_url", "https://example.com/x"), ("photos_album_url", "http://photos.google.com/x"),
    ("photos_album_url", ALBUM + "a" * 500), ("photos_album_url", 5), ("photos_album_url", None),
])
def test_invalid_mode_setting_values_are_refused(name, value):
    with pytest.raises(ValueError):
        check_setting_value(name, value)


def test_other_text_settings_stay_limited_to_50_characters():
    with pytest.raises(ValueError):
        check_setting_value("active_hours", "x" * 51)


def test_the_refusal_message_never_repeats_the_album_link():
    bad = "https://evil.example/secret?key=zzz"
    with pytest.raises(ValueError) as caught:
        check_setting_value("photos_album_url", bad)
    assert "secret?key" not in str(caught.value)


def test_a_mode_change_is_a_valid_settings_command():
    assert validate_command({"kind": "settings", "changes": {"mode": "photos"}}) == {
        "kind": "settings", "changes": {"mode": "photos"}}
    with pytest.raises(ValueError):
        validate_command({"kind": "settings", "changes": {"mode": "photos", "photos_album_url": "https://x.io/a"}})

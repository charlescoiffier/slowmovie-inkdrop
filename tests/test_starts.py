import pytest

from slowmovie_inkdrop.starts import (
    StartPoints,
    format_timecode,
    frame_to_seconds,
    parse_timecode,
    seconds_to_frame,
)


@pytest.mark.parametrize("text, expected", [
    ("90", 90.0), ("1:30", 90.0), ("01:02:03", 3723.0), ("0:0:0", 0.0), ("  00:00:10.5 ", 10.5),
    ("2:00:00", 7200.0),
])
def test_parse_timecode(text, expected):
    assert parse_timecode(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "abc", "1:2:3:4", "-5", "1:60", "1:99:00", "nan", "inf", ":", "1::2"])
def test_parse_timecode_rejects_garbage_with_a_message(text):
    with pytest.raises(ValueError) as exc:
        parse_timecode(text)
    assert str(exc.value)


def test_format_timecode():
    assert format_timecode(0) == "00:00:00"
    assert format_timecode(3723.9) == "01:02:03"
    assert format_timecode(8233) == "02:17:13"


def test_seconds_and_frames_convert_and_clamp():
    assert seconds_to_frame(10, 24.0, 1000) == 240
    assert seconds_to_frame(5000, 24.0, 1000) == 1000
    assert seconds_to_frame(-3, 24.0, 1000) == 0
    assert frame_to_seconds(240, 24.0) == 10.0
    assert frame_to_seconds(5, 0) == 0.0


def test_start_points_roundtrip_and_remove_on_zero(tmp_path):
    sp = StartPoints(tmp_path / "films.json")
    assert sp.get("a.mkv") == 0.0 and sp.all() == {}
    sp.set("a.mkv", 12.5)
    assert StartPoints(tmp_path / "films.json").get("a.mkv") == 12.5       # persisté
    sp.set("a.mkv", 0)
    assert sp.all() == {}


def test_start_points_ignore_a_corrupt_file_and_negative_values(tmp_path):
    path = tmp_path / "films.json"
    path.write_text("{pas du json")
    sp = StartPoints(path)
    assert sp.all() == {}
    sp.set("a.mkv", -4)
    assert sp.get("a.mkv") == 0.0


def test_end_points_live_next_to_start_points(tmp_path):
    sp = StartPoints(tmp_path / "films.json")
    sp.set("a.mkv", 10)
    sp.set_end("a.mkv", 90)
    again = StartPoints(tmp_path / "films.json")
    assert again.get("a.mkv") == 10 and again.get_end("a.mkv") == 90
    assert again.all() == {"a.mkv": 10.0} and again.ends() == {"a.mkv": 90.0}
    again.set("a.mkv", 0)                                       # effacer le départ garde la fin
    assert again.get_end("a.mkv") == 90
    again.set_end("a.mkv", 0)
    assert again.ends() == {} and again.all() == {}
    assert StartPoints(tmp_path / "films.json").path.read_text() == "{}"     # plus d'entrée vide


def test_an_end_point_without_a_start_point_and_files_from_before_end_points(tmp_path):
    import json
    path = tmp_path / "films.json"
    path.write_text(json.dumps({"a.mkv": {"start_seconds": 5}}))             # ancien format
    sp = StartPoints(path)
    assert sp.get("a.mkv") == 5 and sp.get_end("a.mkv") == 0.0
    sp.set_end("b.mkv", 50)
    assert sp.get("b.mkv") == 0.0 and sp.get_end("b.mkv") == 50 and sp.get("a.mkv") == 5

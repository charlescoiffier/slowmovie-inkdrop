from pathlib import Path

import pytest

from slowmovie_inkdrop.videos import (
    find_subtitles,
    has_progress,
    is_supported,
    next_video,
    parse_probe,
    read_progress,
    write_progress,
)

MP4 = {"streams": [{"avg_frame_rate": "24000/1001", "nb_frames": "2400", "width": 1920, "height": 1080}],
       "format": {"duration": "100.1"}}
MKV = {"streams": [{"avg_frame_rate": "25/1", "width": 1280, "height": 720}],
       "format": {"duration": "60"}}


def test_parse_probe_mp4_uses_nb_frames():
    info = parse_probe(MP4)
    assert info.frame_count == 2400 and round(info.fps, 3) == 23.976
    assert round(info.frame_time_ms, 1) == 41.7 and round(info.aspect_ratio, 2) == 1.78


def test_parse_probe_mkv_computes_frames():
    assert parse_probe(MKV).frame_count == 1500


@pytest.mark.parametrize("bad", [
    {"streams": [], "format": {"duration": "1"}},
    {"streams": [{"avg_frame_rate": "0/0", "width": 1, "height": 1}], "format": {"duration": "1"}},
    {"streams": [{"avg_frame_rate": "25/1", "width": 1, "height": 1}], "format": {}},
])
def test_parse_probe_rejects_unusable(bad):
    with pytest.raises(ValueError):
        parse_probe(bad)


def test_is_supported():
    assert is_supported("film.MP4") and is_supported("a.mkv")
    assert not is_supported(".caché.mp4") and not is_supported("notes.txt")


def test_progress_roundtrip_and_garbage(tmp_path):
    video = Path("/x/film.mp4")
    assert read_progress(tmp_path, video) == 0
    write_progress(tmp_path, video, 128)
    assert read_progress(tmp_path, video) == 128
    (tmp_path / "film.mp4.progress").write_text("abc")
    assert read_progress(tmp_path, video) == 0


def test_next_video_wraps():
    vids = [Path("/v/a.mp4"), Path("/v/b.mp4")]
    assert next_video(vids, None) == vids[0]
    assert next_video(vids, vids[0]) == vids[1]
    assert next_video(vids, vids[1]) == vids[0]
    assert next_video([], None) is None


def test_find_subtitles(tmp_path):
    (tmp_path / "f.mp4").touch()
    (tmp_path / "f.srt").touch()
    assert find_subtitles(tmp_path / "f.mp4") == tmp_path / "f.srt"
    assert find_subtitles(tmp_path / "g.mp4") is None


def test_parse_probe_collects_subtitle_streams_and_ignores_cover_art():
    probe = {"streams": [
        {"codec_type": "video", "avg_frame_rate": "0/0", "width": 600, "height": 900,
         "disposition": {"attached_pic": 1}},
        {"codec_type": "video", "avg_frame_rate": "24000/1001", "nb_frames": "2400",
         "width": 1920, "height": 1040, "disposition": {"attached_pic": 0}},
        {"codec_type": "audio", "codec_name": "ac3"},
        {"codec_type": "subtitle", "codec_name": "subrip"},
        {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"}],
        "format": {"duration": "100.1"}}
    info = parse_probe(probe)
    assert (info.width, info.height, info.frame_count) == (1920, 1040, 2400)
    assert info.subtitle_codecs == ("subrip", "hdmv_pgs_subtitle")
    assert info.usable_subtitle_track(0)
    assert not info.usable_subtitle_track(1)          # sous-titres image : non rendus
    assert not info.usable_subtitle_track(2) and not info.usable_subtitle_track(-1)


def test_a_film_without_subtitle_streams_has_no_usable_track():
    info = parse_probe(MP4)
    assert info.subtitle_codecs == () and not info.usable_subtitle_track(0)


def test_a_file_with_only_cover_art_is_rejected():
    probe = {"streams": [{"codec_type": "video", "avg_frame_rate": "0/0", "width": 1, "height": 1,
                          "disposition": {"attached_pic": 1}}], "format": {"duration": "1"}}
    with pytest.raises(ValueError):
        parse_probe(probe)


def test_has_progress(tmp_path):
    video = Path("/x/film.mp4")
    assert not has_progress(tmp_path, video)
    write_progress(tmp_path, video, 0)
    assert has_progress(tmp_path, video)


def test_find_subtitles_survives_glob_characters_in_the_film_name(tmp_path):
    film = tmp_path / "Film [2013] * (v2).mkv"
    film.touch()
    (tmp_path / "Film [2013] * (v2).fr.srt").touch()
    (tmp_path / "Autre film.srt").touch()
    assert find_subtitles(film) == tmp_path / "Film [2013] * (v2).fr.srt"
    assert find_subtitles(tmp_path / "Inconnu.mkv") is None

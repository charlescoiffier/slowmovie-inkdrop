import shutil
import subprocess

import pytest

from slowmovie_inkdrop.config import Settings
from slowmovie_inkdrop.framesource import FfmpegFrameSource
from slowmovie_inkdrop.videos import VideoInfo, probe_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "test.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24",
                    "-t", "2", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_extracts_frame_with_panel_size(clip):
    info = probe_video(clip)
    img = FfmpegFrameSource().frame(clip, info, 10, Settings(), (800, 480))
    assert img.size == (800, 480)


def test_fullscreen_and_timecode_options_run(clip):
    info = probe_video(clip)
    img = FfmpegFrameSource().frame(clip, info, 5, Settings(fullscreen=True, timecode=True), (800, 480))
    assert img.size == (800, 480)


def _has_filter(name: str) -> bool:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return f" {name} " in out


needs_subtitles_filter = pytest.mark.skipif(not _has_filter("subtitles"), reason="filtre subtitles absent")
SRT = "1\n00:00:00,000 --> 00:00:04,000\nBONJOUR LE MONDE\n"


def _black_film(path, *, srt=None, sub_codec="srt"):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=24:d=4"]
    if srt is not None:
        srt_dir = path.parent / "montage"                 # pas à côté du film : il serait pris pour un fichier externe
        srt_dir.mkdir(exist_ok=True)
        srt_file = srt_dir / "s.srt"
        srt_file.write_text(srt, encoding="utf-8")
        cmd += ["-i", str(srt_file), "-map", "0:v", "-map", "1:s", "-c:s", sub_codec]
    subprocess.run(cmd + ["-c:v", "mpeg4", str(path)], check=True)


def _brightest(img):
    return img.convert("L").getextrema()[1]


@needs_subtitles_filter
def test_embedded_text_subtitles_are_drawn_even_with_a_hostile_file_name(tmp_path):
    film = tmp_path / "Film (2013), v2 [x]; a'b.mkv"
    _black_film(film, srt=SRT)
    info = probe_video(film)
    assert info.subtitle_codecs == ("subrip",)
    src = FfmpegFrameSource(scratch=tmp_path)
    src.frame(film, info, 24, Settings(subtitles=True), (800, 480))       # lance l'extraction de la piste
    assert src.wait_for_extraction(timeout=60)
    with_subs = src.frame(film, info, 24, Settings(subtitles=True), (800, 480))
    without = src.frame(film, info, 24, Settings(subtitles=False), (800, 480))
    assert _brightest(with_subs) > 200 and _brightest(without) < 30


@needs_subtitles_filter
def test_an_external_subtitle_file_wins_over_the_embedded_track(tmp_path):
    film = tmp_path / "film.mkv"
    _black_film(film, srt=SRT)
    film.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:04,000\nEXTERNE\n", encoding="utf-8")
    info = probe_video(film)
    img = FfmpegFrameSource(scratch=tmp_path).frame(film, info, 24, Settings(subtitles=True), (800, 480))
    assert _brightest(img) > 200


@needs_subtitles_filter
def test_a_film_without_any_subtitles_just_shows_the_picture(tmp_path):
    film = tmp_path / "plain.mkv"
    _black_film(film)
    info = probe_video(film)
    img = FfmpegFrameSource(scratch=tmp_path).frame(film, info, 24, Settings(subtitles=True), (800, 480))
    assert img.size == (800, 480) and _brightest(img) < 30


@needs_subtitles_filter
def test_image_subtitles_and_out_of_range_tracks_are_ignored_without_error(tmp_path):
    film = tmp_path / "film.mkv"
    _black_film(film, srt=SRT)
    real = probe_video(film)
    pgs = VideoInfo(real.fps, real.duration, real.frame_count, real.width, real.height,
                    ("hdmv_pgs_subtitle",))
    src = FfmpegFrameSource(scratch=tmp_path)
    assert _brightest(src.frame(film, pgs, 24, Settings(subtitles=True), (800, 480))) < 30
    assert _brightest(src.frame(film, real, 24, Settings(subtitles=True, subtitle_track=7), (800, 480))) < 30


@needs_subtitles_filter
def test_embedded_subtitles_are_extracted_once_in_the_background_then_cached(tmp_path):
    film = tmp_path / "Film (2013), v2 [x]; a'b.mkv"
    _black_film(film, srt=SRT)
    info = probe_video(film)
    cache = tmp_path / "cache"
    src = FfmpegFrameSource(scratch=tmp_path, cache_dir=cache)
    settings = Settings(subtitles=True)
    first = src.frame(film, info, 24, settings, (800, 480))
    assert _brightest(first) < 30                       # extraction lancée en arrière-plan : pas encore de sous-titres
    assert src.wait_for_extraction(timeout=60) and src.started == 1
    assert len(list(cache.glob("*.srt"))) == 1
    assert _brightest(src.frame(film, info, 24, settings, (800, 480))) > 200
    again = FfmpegFrameSource(scratch=tmp_path, cache_dir=cache)          # autre processus, même cache
    assert _brightest(again.frame(film, info, 24, settings, (800, 480))) > 200
    assert again.started == 0 and len(list(cache.iterdir())) == 1


@needs_subtitles_filter
def test_ass_tracks_keep_their_format_in_the_cache(tmp_path):
    film = tmp_path / "film.mkv"
    _black_film(film, srt=SRT, sub_codec="ass")
    info = probe_video(film)
    assert info.subtitle_codecs == ("ass",)
    src = FfmpegFrameSource(scratch=tmp_path, cache_dir=tmp_path / "cache")
    src.frame(film, info, 24, Settings(subtitles=True), (800, 480))
    assert src.wait_for_extraction(timeout=60)
    assert len(list((tmp_path / "cache").glob("*.ass"))) == 1
    assert _brightest(src.frame(film, info, 24, Settings(subtitles=True), (800, 480))) > 200


@needs_subtitles_filter
def test_a_changed_film_is_extracted_again(tmp_path):
    import os
    film = tmp_path / "film.mkv"
    _black_film(film, srt=SRT)
    info = probe_video(film)
    src = FfmpegFrameSource(scratch=tmp_path, cache_dir=tmp_path / "cache")
    src.frame(film, info, 24, Settings(subtitles=True), (800, 480))
    src.wait_for_extraction(timeout=60)
    os.utime(film, ns=(1, 5_000_000_000))                       # le fichier a été remplacé
    src.frame(film, info, 24, Settings(subtitles=True), (800, 480))
    src.wait_for_extraction(timeout=60)
    assert src.started == 2 and len(list((tmp_path / "cache").glob("*.srt"))) == 2


@needs_subtitles_filter
def test_a_failed_extraction_never_crashes_nor_loops(tmp_path):
    film = tmp_path / "plain.mkv"
    _black_film(film)                                           # aucune piste de sous-titres
    real = probe_video(film)
    lying = VideoInfo(real.fps, real.duration, real.frame_count, real.width, real.height, ("subrip",))
    src = FfmpegFrameSource(scratch=tmp_path, cache_dir=tmp_path / "cache")
    for _ in range(3):
        img = src.frame(film, lying, 24, Settings(subtitles=True), (800, 480))
        assert img.size == (800, 480) and _brightest(img) < 30
        src.wait_for_extraction(timeout=60)
    assert src.started == 1                                     # une seule tentative, pas de boucle


def test_an_unusable_track_never_starts_an_extraction(tmp_path):
    film = tmp_path / "film.mkv"
    film.write_bytes(b"x")
    info = VideoInfo(24.0, 10.0, 240, 640, 360, ("hdmv_pgs_subtitle",))
    src = FfmpegFrameSource(scratch=tmp_path, cache_dir=tmp_path / "cache")
    assert src._embedded_file(film, info, 0) is None and src.started == 0

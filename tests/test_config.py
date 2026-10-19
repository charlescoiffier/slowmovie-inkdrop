from datetime import datetime

import pytest

from slowmovie_inkdrop.config import (
    ConfigError,
    Paths,
    Settings,
    in_active_hours,
    parse_active_hours,
)


def test_defaults_match_upstream():
    s = Settings()
    assert (s.delay, s.increment, s.contrast) == (120, 4, 1.0)
    assert s.epd == "waveshare_epd.epd7in5_V2"
    assert s.min_image_seconds == 60 and s.max_image_seconds == 3600
    assert s.queue_max == 10 and s.queue_expiry_seconds == 21600
    assert s.active_hours == ""
    assert s.caption_max_chars == 128


def test_load_reads_upstream_style_conf(tmp_path):
    conf = tmp_path / "slowmovie.conf"
    conf.write_text("# commentaire\nrandom-frames = true\ndelay=60\nincrement = 6\ncontrast=1.2\nclé-inconnue = 1\n")
    s = Settings.load(conf)
    assert s.random_frames is True and s.delay == 60 and s.increment == 6
    assert s.contrast == 1.2


def test_load_missing_file_gives_defaults(tmp_path):
    assert Settings.load(tmp_path / "absent.conf") == Settings()


def test_load_ignores_invalid_values(tmp_path):
    conf = tmp_path / "slowmovie.conf"
    conf.write_text("delay = abc\nincrement = 0\nactive-hours = n'importe quoi\nloop = peut-être\n")
    s = Settings.load(conf)
    assert s == Settings()


def test_save_roundtrip_and_permissions(tmp_path):
    conf = tmp_path / "slowmovie.conf"
    s = Settings(delay=30, loop=True, active_hours="08:00-22:00", relay_token="secret")
    s.save(conf)
    assert Settings.load(conf) == s
    assert oct(conf.stat().st_mode & 0o777) == "0o600"


def test_save_refuses_invalid(tmp_path):
    with pytest.raises(ConfigError):
        Settings(delay=0).save(tmp_path / "x.conf")


def test_active_hours_same_day():
    assert in_active_hours("08:00-22:00", datetime(2026, 1, 1, 12, 0))
    assert not in_active_hours("08:00-22:00", datetime(2026, 1, 1, 23, 0))
    assert not in_active_hours("08:00-22:00", datetime(2026, 1, 1, 22, 0))


def test_active_hours_wraps_past_midnight():
    assert in_active_hours("22:00-06:00", datetime(2026, 1, 1, 23, 30))
    assert in_active_hours("22:00-06:00", datetime(2026, 1, 1, 2, 0))
    assert not in_active_hours("22:00-06:00", datetime(2026, 1, 1, 12, 0))


def test_active_hours_empty_means_always():
    assert in_active_hours("", datetime(2026, 1, 1, 3, 0))


@pytest.mark.parametrize("spec", ["8h-22h", "08:00", "08:00-08:00", "25:00-26:00"])
def test_active_hours_invalid(spec):
    with pytest.raises(ConfigError):
        parse_active_hours(spec)


def test_paths_ensure(tmp_path):
    paths = Paths(tmp_path)
    settings = Settings()
    paths.ensure(settings)
    assert paths.videos_dir(settings) == tmp_path / "Videos"
    for d in (paths.progress, paths.queue_dir, paths.socket.parent, paths.videos_dir(settings)):
        assert d.is_dir()


def test_videos_dir_absolute(tmp_path):
    assert Paths(tmp_path).videos_dir(Settings(directory="/mnt/films")).as_posix() == "/mnt/films"


@pytest.mark.parametrize("value", [0, -1, 501])
def test_caption_max_chars_out_of_range_is_refused(tmp_path, value):
    with pytest.raises(ConfigError):
        Settings(caption_max_chars=value).save(tmp_path / "x.conf")


def test_paths_for_start_points_and_command_log(tmp_path):
    paths = Paths(tmp_path)
    assert paths.starts == tmp_path / "films.json" and paths.commands == tmp_path / "commands.json"


@pytest.mark.parametrize("value", [-1, 51])
def test_subtitle_track_out_of_range_is_refused(tmp_path, value):
    with pytest.raises(ConfigError):
        Settings(subtitle_track=value).save(tmp_path / "x.conf")


def test_subtitle_track_defaults_to_the_first_one():
    assert Settings().subtitle_track == 0

import json

from slowmovie_inkdrop.config import Paths, Settings
from slowmovie_inkdrop.tui import model


def test_read_state_missing(tmp_path):
    assert model.read_state(Paths(tmp_path)) is None


def test_film_rows_merge_library_and_progress(tmp_path):
    paths = Paths(tmp_path)
    paths.progress.mkdir()
    (paths.progress / "a.mp4.progress").write_text("250")
    paths.library.write_text(json.dumps({
        "a.mp4": {"size": 1, "status": "ready", "frames": 1000, "changed_at": 0},
        "b.mp4": {"size": 1, "status": "error", "error": "illisible", "changed_at": 0},
        "c.mp4": {"size": 1, "status": "pending", "changed_at": 0}}))
    rows = {r["name"]: r for r in model.film_rows(paths)}
    assert rows["a.mp4"]["percent"] == 25.0 and rows["a.mp4"]["status"] == "ready"
    assert rows["b.mp4"]["error"] == "illisible"
    assert rows["c.mp4"]["status"] == "pending" and rows["c.mp4"]["percent"] is None


def test_setting_fields_cover_every_user_setting():
    names = {name for name, _, _ in model.SETTING_FIELDS}
    for expected in ("delay", "increment", "random_file", "random_frames", "loop", "contrast",
                     "fullscreen", "subtitles", "timecode", "min_image_seconds",
                     "max_image_seconds", "queue_max", "active_hours", "relay_url",
                     "caption_max_chars"):
        assert expected in names
    assert "relay_token" not in names           # jamais affiché à l'écran
    assert names <= set(Settings.__dataclass_fields__)


def test_tail_log(tmp_path):
    paths = Paths(tmp_path)
    paths.log.write_text("\n".join(f"ligne {i}" for i in range(300)))
    out = model.tail_log(paths, lines=3)
    assert out.splitlines() == ["ligne 297", "ligne 298", "ligne 299"]
    assert model.tail_log(Paths(tmp_path / "vide"), 3) == ""


def test_film_rows_report_positions_and_start_points_in_seconds(tmp_path):
    from slowmovie_inkdrop.starts import StartPoints
    paths = Paths(tmp_path)
    paths.progress.mkdir()
    (paths.progress / "a.mp4.progress").write_text("480")
    paths.library.write_text(json.dumps({
        "a.mp4": {"size": 1, "status": "ready", "frames": 2400, "fps": 24.0, "duration": 100.0, "changed_at": 0},
        "old.mp4": {"size": 1, "status": "ready", "frames": 100, "changed_at": 0}}))
    StartPoints(paths.starts).set("a.mp4", 30)
    rows = {r["name"]: r for r in model.film_rows(paths)}
    assert rows["a.mp4"]["position_seconds"] == 20.0 and rows["a.mp4"]["start_seconds"] == 30.0
    assert rows["a.mp4"]["duration_seconds"] == 100.0
    assert rows["old.mp4"]["position_seconds"] is None            # pas de fps connu : on n'invente rien


def test_subtitle_track_is_a_setting():
    assert "subtitle_track" in {n for n, _, _ in model.SETTING_FIELDS}

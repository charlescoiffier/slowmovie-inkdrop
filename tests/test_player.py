import json
from datetime import datetime

from conftest import png_bytes

from slowmovie_inkdrop.videos import read_progress, write_progress

A, B = "a" * 32, "b" * 32


def test_film_advances_and_saves_progress(make_player):
    ctx = make_player()
    sleep = ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 0)]
    assert ctx.display.events == ["prepare", "display", "sleep"]
    assert read_progress(ctx.paths.progress, ctx.library.videos[0]) == 4
    assert 0 < sleep <= 5
    ctx.player.tick()
    assert len(ctx.frames.calls) == 1            # pas avant le délai
    ctx.clock.advance(120)
    ctx.player.tick()
    assert ctx.frames.calls[-1] == ("a.mp4", 4)


def test_resumes_from_progress_file(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 500)
    ctx = make_player()
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 500)]


def test_image_preempts_film_then_film_resumes_where_it_stopped(make_player):
    ctx = make_player()
    ctx.player.tick()                             # image 0 affichée, prochaine = 4
    ctx.queue.add(A, png_bytes(), 300, ctx.clock())
    ctx.clock.advance(1)
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE"
    assert ctx.done == [(A, "shown")]
    refreshes = ctx.display.events.count("display")
    ctx.clock.advance(299)
    ctx.player.tick()
    assert ctx.display.events.count("display") == refreshes   # un seul rafraîchissement
    ctx.clock.advance(2)
    ctx.player.tick()
    assert ctx.player.mode == "FILM"
    assert ctx.frames.calls[-1] == ("a.mp4", 4)


def test_queue_is_played_in_order_before_film_resumes(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(color="red"), 60, ctx.clock())
    ctx.queue.add(B, png_bytes(color="green"), 120, ctx.clock() + 1)
    ctx.player.tick()
    assert ctx.frames.calls == []
    ctx.clock.advance(61)
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE" and ctx.frames.calls == []
    assert [d[0] for d in ctx.done] == [A, B]
    ctx.clock.advance(121)
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 0)]


def test_duration_is_clamped(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 5, ctx.clock())
    ctx.player.tick()
    assert ctx.player.image_until - ctx.clock() == 60
    ctx2 = make_player(base=ctx.paths.base / "other")
    ctx2.queue.add(B, png_bytes(), 99999, ctx2.clock())
    ctx2.player.tick()
    assert ctx2.player.image_until - ctx2.clock() == 3600


def test_restart_during_image_resumes_until_deadline(make_player, tmp_path):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 300, ctx.clock())
    ctx.player.tick()
    again = make_player(clock=ctx.clock)
    assert again.player.mode == "IMAGE"
    again.player.tick()
    assert again.display.events == []             # pas de nouveau rafraîchissement
    ctx.clock.advance(301)
    again.player.tick()
    assert again.player.mode == "FILM"


def test_absurd_deadline_after_clock_jump_is_ignored(make_player, tmp_path):
    ctx = make_player()
    ctx.paths.state.write_text(json.dumps({"mode": "IMAGE", "image_until": ctx.clock() + 10**9}))
    again = make_player(clock=ctx.clock)
    assert again.player.mode == "FILM"


def test_clock_jump_while_showing_image_does_not_freeze_screen(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 60, ctx.clock())
    ctx.player.tick()
    ctx.player.image_until = ctx.clock() + 10**9       # l'heure a sauté en arrière
    ctx.player.tick()
    assert ctx.player.image_until - ctx.clock() <= 3600


def test_paused_player_does_nothing(make_player):
    ctx = make_player()
    assert ctx.player.submit({"cmd": "pause"}) is not None
    ctx.player.tick()
    assert ctx.display.events == []
    ctx.player.submit({"cmd": "resume"})
    ctx.player.tick()
    assert ctx.display.events.count("display") == 1


def test_no_videos_does_not_crash(make_player):
    ctx = make_player(videos=())
    sleep = ctx.player.tick()
    assert 0 < sleep <= 5
    state = json.loads(ctx.paths.state.read_text())
    assert "Aucun film" in state["error"] and state["video"] is None
    ctx.library.videos = [ctx.paths.videos_dir(ctx.player.settings) / "a.mp4"]
    ctx.clock.advance(31)
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 0)]


def test_images_still_shown_when_no_video(make_player):
    ctx = make_player(videos=())
    ctx.queue.add(A, png_bytes(), 60, ctx.clock())
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE"


def test_frame_extraction_failure_moves_on(make_player):
    ctx = make_player()
    ctx.frames.fail_on = {0}
    ctx.player.tick()
    assert ctx.display.events == []
    assert "ffmpeg" in ctx.player.error
    ctx.clock.advance(11)
    ctx.player.tick()
    assert ctx.frames.calls[-1] == ("a.mp4", 4) and ctx.player.error == ""


def test_unreadable_queued_image_is_discarded_and_reported(make_player):
    ctx = make_player()
    ctx.queue.add(A, b"pas une image", 60, ctx.clock())
    ctx.player.tick()
    assert ctx.done == [(A, "failed")] and len(ctx.queue) == 0
    assert ctx.player.mode == "FILM"


def test_outside_active_hours_nothing_is_drawn(make_player):
    ctx = make_player(active_hours="08:00-22:00")
    ctx.clock.t = datetime(2026, 1, 1, 23, 30).timestamp()
    ctx.player.tick()
    assert ctx.display.events == []
    ctx.clock.t = datetime(2026, 1, 2, 9, 0).timestamp()
    ctx.player.tick()
    assert ctx.display.events.count("display") == 1


def test_end_of_video_moves_to_next_and_writes_now_playing(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 998)
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 998)]
    assert ctx.paths.now_playing.read_text().endswith("b.mp4")
    assert read_progress(ctx.paths.progress, tmp_path / "Videos" / "b.mp4") == 0


def test_skip_ends_current_image(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 600, ctx.clock())
    ctx.player.tick()
    ctx.player.submit({"cmd": "skip"})
    ctx.player.tick()
    assert ctx.player.mode == "FILM"


def test_play_command_switches_video(make_player):
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.tick()
    fut = ctx.player.submit({"cmd": "play", "video": "b.mp4"})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.frames.calls[-1][0] == "b.mp4"


def test_play_unknown_video_is_refused(make_player):
    ctx = make_player()
    fut = ctx.player.submit({"cmd": "play", "video": "inconnu.mp4"})
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False


def test_reload_applies_new_settings(make_player):
    ctx = make_player()
    conf = ctx.paths.conf
    conf.write_text("delay = 10\nqueue-max = 3\n")
    fut = ctx.player.submit({"cmd": "reload"})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.player.settings.delay == 10 and ctx.queue.max_items == 3


def test_queue_remove_and_clear_report_failed(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 60, ctx.clock())
    ctx.queue.add(B, png_bytes(), 60, ctx.clock() + 1)
    ctx.player.submit({"cmd": "queue_remove", "id": A})
    ctx.player.submit({"cmd": "queue_clear"})
    ctx.player.tick()
    assert ctx.done == [(A, "failed"), (B, "failed")]
    assert len(ctx.queue) == 0 and ctx.player.mode == "FILM"


def test_unknown_command_is_refused(make_player):
    ctx = make_player()
    fut = ctx.player.submit({"cmd": "dance"})
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False


def test_state_snapshot_fields(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 60, ctx.clock())
    ctx.player.tick()
    ctx.queue.add(B, png_bytes(), 90, ctx.clock())
    ctx.player.tick()
    state = json.loads(ctx.paths.state.read_text())
    assert state["mode"] == "IMAGE" and state["queue"] == [{"id": B, "duration": 90}]
    for key in ("paused", "video", "frame", "frame_count", "percent", "remaining_seconds",
                "next_frame_at", "image_until", "active", "error", "relay_status", "updated_at"):
        assert key in state


def test_video_appearing_after_start_is_picked_up_within_seconds(make_player):
    ctx = make_player(videos=())
    ctx.player.tick()
    ctx.library.videos = [ctx.paths.videos_dir(ctx.player.settings) / "a.mp4"]
    ctx.clock.advance(6)
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 0)]

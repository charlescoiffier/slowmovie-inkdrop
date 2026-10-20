import json
from datetime import datetime

import pytest
from conftest import png_bytes

from slowmovie_inkdrop.config import Settings
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
    assert state["mode"] == "IMAGE" and state["queue"] == [{"id": B, "duration": 90, "caption": ""}]
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


def test_image_that_explodes_while_decoding_is_dropped_not_retried_forever(make_player, monkeypatch):
    from PIL import Image

    from slowmovie_inkdrop import player as player_module

    def bomb(*args, **kwargs):
        raise Image.DecompressionBombError("trop de pixels")

    monkeypatch.setattr(player_module, "to_panel", bomb)
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 60, ctx.clock())
    ctx.player.tick()
    assert ctx.done == [(A, "failed")] and len(ctx.queue) == 0
    ctx.clock.advance(11)
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 0)]           # le film revient


def test_caption_is_drawn_on_the_displayed_image_only_when_present(make_player):
    from PIL import ImageChops

    plain = make_player()
    plain.queue.add(A, png_bytes(size=(800, 480), color="gray"), 60, plain.clock())
    plain.player.tick()
    with_text = make_player(base=plain.paths.base / "other")
    with_text.queue.add(B, png_bytes(size=(800, 480), color="gray"), 60, with_text.clock(),
                        caption="Joyeux anniversaire")
    with_text.player.tick()
    diff = ImageChops.difference(plain.display.shown[-1], with_text.display.shown[-1]).getbbox()
    assert diff is not None and diff[1] > 480 * 0.7        # texte en bas, rien ailleurs


def test_caption_bottom_margin_follows_the_setting(make_player):
    from PIL import ImageChops

    def text_box(name, caption, **settings):
        ctx = make_player(base=name, **settings)
        ctx.queue.add(A, png_bytes(size=(800, 480), color="gray"), 60, ctx.clock(), caption=caption)
        ctx.player.tick()
        return ctx.display.shown[-1]

    def box(tag, **settings):
        base = text_box(tag / "sans", "", **settings)
        return ImageChops.difference(base, text_box(tag / "avec", "Bonjour", **settings)).getbbox()

    root = make_player().paths.base
    assert box(root / "m120", caption_margin=120)[3] <= 480 - 120
    assert box(root / "m40")[3] <= 480 - 40               # défaut : 40 px
    assert box(root / "m0", caption_margin=0)[3] > 480 - 40


def test_overlong_caption_from_the_queue_is_cut_to_the_setting(make_player):
    ctx = make_player(caption_max_chars=5)
    ctx.queue.add(A, png_bytes(size=(800, 480), color="gray"), 60, ctx.clock(), caption="x" * 100)
    ctx.player.tick()
    short = make_player(base=ctx.paths.base / "o2", caption_max_chars=5)
    short.queue.add(B, png_bytes(size=(800, 480), color="gray"), 60, short.clock(), caption="x" * 5)
    short.player.tick()
    from PIL import ImageChops
    assert ImageChops.difference(ctx.display.shown[-1], short.display.shown[-1]).getbbox() is None


def test_first_play_starts_at_the_film_start_point(make_player):
    ctx = make_player()
    ctx.player.starts.set("a.mp4", 10)            # 10 s à 24 i/s = image 240
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 240)]


def test_saved_progress_wins_over_the_start_point(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 500)
    ctx = make_player()
    ctx.player.starts.set("a.mp4", 10)
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 500)]


def test_set_start_does_not_move_a_film_already_started(make_player):
    ctx = make_player()
    ctx.player.tick()                                           # image 0 affichée, progression = 4
    fut = ctx.player.submit({"cmd": "set_start", "video": "a.mp4", "seconds": 30})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.player.starts.get("a.mp4") == 30
    assert ctx.player.frame == 4 and read_progress(ctx.paths.progress, ctx.library.videos[0]) == 4


def test_set_start_beyond_the_film_is_refused(make_player):
    ctx = make_player()
    fut = ctx.player.submit({"cmd": "set_start", "video": "a.mp4", "seconds": 5000})   # durée 100 s
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False and ctx.player.starts.get("a.mp4") == 0.0


def test_seek_jumps_now_and_is_saved(make_player):
    ctx = make_player()
    ctx.player.tick()
    fut = ctx.player.submit({"cmd": "seek", "video": "a.mp4", "seconds": 20})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.frames.calls[-1] == ("a.mp4", 480)
    assert read_progress(ctx.paths.progress, ctx.library.videos[0]) == 484


def test_seek_past_the_end_is_clamped(make_player):
    ctx = make_player()
    ctx.player.tick()
    ctx.player.submit({"cmd": "seek", "video": "a.mp4", "seconds": 5000})
    ctx.player.tick()
    assert ctx.frames.calls[-1] == ("a.mp4", 1000)


def test_seek_on_another_film_only_moves_its_saved_position(make_player):
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.tick()
    ctx.player.submit({"cmd": "seek", "video": "b.mp4", "seconds": 10})
    ctx.player.tick()
    assert read_progress(ctx.paths.progress, ctx.library.videos[1]) == 240
    assert ctx.player.video.name == "a.mp4" and ctx.frames.calls[-1][0] == "a.mp4"


def test_seek_unknown_film_is_refused(make_player):
    ctx = make_player()
    fut = ctx.player.submit({"cmd": "seek", "video": "inconnu.mp4", "seconds": 10})
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False


def test_reset_goes_back_to_the_start_point(make_player):
    ctx = make_player()
    ctx.player.starts.set("a.mp4", 10)
    ctx.player.tick()
    ctx.player.submit({"cmd": "seek", "video": "a.mp4", "seconds": 20})
    ctx.player.tick()
    assert ctx.frames.calls[-1] == ("a.mp4", 480)
    fut = ctx.player.submit({"cmd": "reset", "video": "a.mp4"})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.frames.calls[-1] == ("a.mp4", 240)


def test_loop_restart_uses_the_start_point(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 998)
    ctx = make_player(loop=True)
    ctx.player.starts.set("a.mp4", 10)
    ctx.player.tick()                                            # affiche 998, dépasse la fin, repart au départ
    assert read_progress(ctx.paths.progress, ctx.library.videos[0]) == 240


def test_next_film_starts_at_its_own_start_point(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 998)
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.starts.set("b.mp4", 5)
    ctx.player.tick()
    assert ctx.paths.now_playing.read_text().endswith("b.mp4")
    assert read_progress(ctx.paths.progress, tmp_path / "Videos" / "b.mp4") == 120


def test_remote_settings_change_is_validated_saved_and_applied(make_player):
    ctx = make_player()
    ctx.paths.conf.write_text("relay-token = secret\ndelay = 40\n")
    fut = ctx.player.submit({"cmd": "settings", "changes": {
        "delay": 10, "fullscreen": True, "contrast": 1.5, "active_hours": "08:00-22:00", "queue_max": 3}})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    saved = Settings.load(ctx.paths.conf)
    assert (saved.delay, saved.fullscreen, saved.contrast, saved.active_hours) == (10, True, 1.5, "08:00-22:00")
    assert saved.relay_token == "secret"                          # les réglages non touchés sont conservés
    assert ctx.player.settings.delay == 10 and ctx.queue.max_items == 3


@pytest.mark.parametrize("changes", [
    {"relay_url": "https://evil"}, {"relay_token": "x"}, {"epd": "x"}, {"directory": "/"},
    {"delay": "10"}, {"delay": True}, {"delay": 0}, {"delay": 1.5}, {"fullscreen": 1}, {"contrast": 9},
    {"active_hours": "n'importe quoi"}, {"relay_poll_seconds": 1000}, {}, {"subtitle_track": -1},
    {"min_image_seconds": 500, "max_image_seconds": 100},
])
def test_invalid_remote_settings_are_refused_and_nothing_is_written(make_player, changes):
    ctx = make_player()
    ctx.paths.conf.write_text("delay = 40\n")
    before = ctx.paths.conf.read_text()
    fut = ctx.player.submit({"cmd": "settings", "changes": changes})
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False
    assert ctx.paths.conf.read_text() == before and ctx.player.settings.delay == 120


def test_remote_snapshot_describes_the_player_without_secrets(make_player):
    ctx = make_player(videos=("a.mp4", "b.mp4"), relay_token="jeton-secret", relay_url="https://x.test")
    ctx.player.starts.set("b.mp4", 12)
    ctx.queue.add(A, png_bytes(), 90, ctx.clock(), caption="Bonjour")
    ctx.player.tick()                       # affiche d'abord l'image envoyée
    snap = ctx.player.remote_snapshot(ctx.clock())
    assert snap["mode"] == "IMAGE" and snap["paused"] is False
    assert snap["queue"] == [] and snap["video"] is None
    ctx.clock.advance(120)
    ctx.player.tick()                       # puis le film
    snap = ctx.player.remote_snapshot(ctx.clock())
    assert snap["video"] == "a.mp4" and snap["duration_seconds"] == 100.0 and snap["position_seconds"] > 0
    films = {f["name"]: f for f in snap["films"]}
    assert films["b.mp4"]["start_seconds"] == 12 and films["a.mp4"]["fps"] == 24.0
    assert films["a.mp4"]["status"] == "ready" and films["a.mp4"]["subtitle_codecs"] == []
    text = json.dumps(snap)
    assert "jeton-secret" not in text and "relay_url" not in snap["settings"] and "x.test" not in text
    assert snap["settings"]["delay"] == 120 and "subtitle_track" in snap["settings"]


def test_remote_snapshot_lists_the_waiting_images_with_their_captions(make_player):
    ctx = make_player(videos=())
    ctx.queue.add(A, png_bytes(), 90, ctx.clock(), caption="Texte")
    snap = ctx.player.remote_snapshot(ctx.clock())
    assert snap["queue"] == [{"id": A, "duration": 90, "caption": "Texte"}]


def test_remote_snapshot_stays_small_with_many_films(make_player):
    names = tuple(f"film numéro {i} avec un nom assez long pour faire du volume.mkv" for i in range(200))
    ctx = make_player(videos=names)
    assert len(json.dumps(ctx.player.remote_snapshot(ctx.clock()))) < 64 * 1024


def test_film_moves_to_the_next_one_when_it_reaches_its_end_point(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.starts.set_end("a.mp4", 10)                        # 10 s = image 240
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 238)
    ctx.player.tick()                                              # affiche 238, avance à 242 : au-delà de la fin
    assert ctx.paths.now_playing.read_text().endswith("b.mp4")
    assert read_progress(ctx.paths.progress, tmp_path / "Videos" / "b.mp4") == 0


def test_end_point_with_loop_restarts_at_the_start_point(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    ctx = make_player(loop=True)
    ctx.player.starts.set("a.mp4", 5)                              # image 120
    ctx.player.starts.set_end("a.mp4", 10)                         # image 240
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 238)
    ctx.player.tick()
    assert read_progress(ctx.paths.progress, ctx.library.videos[0]) == 120


def test_a_film_without_an_end_point_still_plays_to_its_real_end(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 900)
    ctx.player.tick()
    assert ctx.paths.now_playing.read_text().endswith("a.mp4")      # 904 <= 1000 : on reste sur a


def test_set_end_is_validated_and_zero_clears_it(make_player):
    ctx = make_player()
    ctx.player.starts.set("a.mp4", 10)

    def run(seconds):
        fut = ctx.player.submit({"cmd": "set_end", "video": "a.mp4", "seconds": seconds})
        ctx.player.tick()
        return fut.result(timeout=1)

    assert run(5)["ok"] is False                                   # avant le départ
    assert run(10)["ok"] is False                                  # égal au départ
    assert run(5000)["ok"] is False                                # au-delà de la durée (100 s)
    assert ctx.player.starts.get_end("a.mp4") == 0.0
    assert run(20) == {"ok": True} and ctx.player.starts.get_end("a.mp4") == 20.0
    assert run(0) == {"ok": True} and ctx.player.starts.get_end("a.mp4") == 0.0


def test_set_start_must_stay_before_the_end_point(make_player):
    ctx = make_player()
    ctx.player.starts.set_end("a.mp4", 20)
    refused = ctx.player.submit({"cmd": "set_start", "video": "a.mp4", "seconds": 30})
    ctx.player.tick()
    assert refused.result(timeout=1)["ok"] is False and ctx.player.starts.get("a.mp4") == 0.0
    ok = ctx.player.submit({"cmd": "set_start", "video": "a.mp4", "seconds": 10})
    ctx.player.tick()
    assert ok.result(timeout=1) == {"ok": True}


def test_seeking_past_the_end_point_moves_on_to_the_next_film(make_player):
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.starts.set_end("a.mp4", 10)
    ctx.player.tick()
    ctx.player.submit({"cmd": "seek", "video": "a.mp4", "seconds": 20})
    ctx.player.tick()
    assert ctx.paths.now_playing.read_text().endswith("b.mp4")


def test_random_frames_stay_between_the_start_and_end_points(make_player):
    ctx = make_player(random_frames=True)
    ctx.player.starts.set("a.mp4", 10)                             # image 240
    ctx.player.starts.set_end("a.mp4", 20)                         # image 480
    for _ in range(40):
        ctx.clock.advance(121)
        ctx.player.tick()
    frames = [f for _, f in ctx.frames.calls]
    assert len(frames) >= 30 and all(240 <= f <= 480 for f in frames)


def test_snapshot_reports_the_end_point(make_player):
    ctx = make_player()
    ctx.player.starts.set_end("a.mp4", 50)
    film = ctx.player.remote_snapshot(ctx.clock())["films"][0]
    assert film["end_seconds"] == 50.0


def test_remote_snapshot_never_contains_the_album_link(make_player):
    link = "https://photos.google.com/share/AF1QipSecret?key=ZZZtop"
    ctx = make_player(photos_album_url=link)
    snap = ctx.player.remote_snapshot(ctx.clock())
    assert "AF1QipSecret" not in json.dumps(snap) and "ZZZtop" not in json.dumps(snap)
    assert "photos_album_url" not in snap["settings"]
    assert snap["settings"]["photos_album_set"] is True
    assert snap["settings"]["photos_album_host"] == "photos.google.com"
    assert snap["settings"]["mode"] == "slowmovie"
    plain = make_player(base=ctx.paths.base / "o").player.remote_snapshot(0)
    assert plain["settings"]["photos_album_set"] is False and plain["settings"]["photos_album_host"] == ""


def test_a_small_caption_size_on_the_image_gives_smaller_text(make_player):
    from PIL import ImageChops

    def text_height(name, size):
        plain = make_player(base=name / "p")
        plain.queue.add(A, png_bytes(size=(800, 480), color="gray"), 60, plain.clock())
        plain.player.tick()
        ctx = make_player(base=name / "t")
        ctx.queue.add(A, png_bytes(size=(800, 480), color="gray"), 60, ctx.clock(), caption="Bonjour",
                      caption_size=size)
        ctx.player.tick()
        box = ImageChops.difference(plain.display.shown[-1], ctx.display.shown[-1]).getbbox()
        return box[3] - box[1]

    root = make_player().paths.base
    assert text_height(root / "a", 14) < text_height(root / "b", 0)


# -- diaporamas (modes pipiquant et photos) ---------------------------------

ALBUM = "https://photos.google.com/share/AF1QipX?key=abc"


def test_a_slide_mode_shows_a_slide_instead_of_a_film_frame(make_player):
    ctx = make_player(mode="pipiquant", pipiquant_interval=600)
    ctx.player.tick()
    assert ctx.slides.calls == ["pipiquant"] and ctx.frames.calls == [] and len(ctx.display.shown) == 1
    assert ctx.player.next_frame_at == ctx.clock() + 600


def test_nothing_is_drawn_before_the_interval_then_a_new_slide_comes(make_player):
    ctx = make_player(mode="pipiquant", pipiquant_interval=600)
    ctx.player.tick()
    ctx.clock.advance(599)
    ctx.player.tick()
    assert len(ctx.slides.calls) == 1
    ctx.clock.advance(2)
    ctx.player.tick()
    assert len(ctx.slides.calls) == 2 and len(ctx.display.shown) == 2


def test_the_photos_mode_uses_its_own_interval(make_player):
    ctx = make_player(mode="photos", photos_interval=1800, pipiquant_interval=300, photos_album_url=ALBUM)
    ctx.player.tick()
    assert ctx.slides.calls == ["photos"] and ctx.player.next_frame_at == ctx.clock() + 1800


def test_the_slide_caption_uses_its_own_size_and_the_bottom_margin(make_player):
    from PIL import ImageChops
    plain = make_player(mode="pipiquant", caption_margin=60)
    plain.player.tick()
    ctx = make_player(base=plain.paths.base / "c", mode="pipiquant", caption_margin=60)
    ctx.slides.caption, ctx.slides.caption_size = "π-piquant • 1 = 10° • 5 décimales", 14
    ctx.player.tick()
    box = ImageChops.difference(plain.display.shown[-1], ctx.display.shown[-1]).getbbox()
    assert box is not None and box[3] <= 480 - 60 and box[3] - box[1] <= 22


def test_a_sent_image_comes_first_and_the_slideshow_resumes_after_it(make_player):
    ctx = make_player(mode="pipiquant", pipiquant_interval=600)
    ctx.player.tick()
    ctx.queue.add(A, png_bytes(size=(800, 480), color="gray"), 60, ctx.clock())
    ctx.clock.advance(5)
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE" and len(ctx.slides.calls) == 1
    ctx.clock.advance(30)
    ctx.player.tick()
    assert len(ctx.slides.calls) == 1                      # l'image envoyée est encore à l'écran
    ctx.clock.advance(40)
    ctx.player.tick()
    assert len(ctx.slides.calls) == 2 and ctx.player.mode == "FILM"
    assert ctx.player.next_frame_at == ctx.clock() + 600    # le compte à rebours repart de zéro


def test_a_failing_slide_keeps_the_screen_and_retries_later(make_player):
    from slowmovie_inkdrop.player import SLIDE_RETRY_SECONDS
    from slowmovie_inkdrop.slides import SlideError
    ctx = make_player(mode="pipiquant", pipiquant_interval=3600)
    ctx.player.tick()
    shown = len(ctx.display.shown)
    ctx.clock.advance(3601)
    ctx.slides.fail_with = SlideError("Album illisible.")
    ctx.player.tick()
    assert len(ctx.display.shown) == shown and ctx.player.error == "Album illisible."
    assert ctx.player.next_frame_at == ctx.clock() + SLIDE_RETRY_SECONDS
    ctx.player.tick()                                       # pas de boucle serrée
    assert len(ctx.slides.calls) == 2
    ctx.slides.fail_with = None
    ctx.clock.advance(SLIDE_RETRY_SECONDS + 1)
    ctx.player.tick()
    assert ctx.player.error == "" and len(ctx.display.shown) == shown + 1


def test_a_display_failure_is_reported_without_crashing(make_player):
    ctx = make_player(mode="pipiquant")

    def broken(image):
        raise OSError("écran débranché")
    ctx.display.display = broken
    ctx.player.tick()
    assert "écran débranché" in ctx.player.error


def test_without_a_slide_source_the_slideshow_is_reported_unavailable(make_player):
    ctx = make_player(mode="photos", slides=None, photos_album_url=ALBUM)
    ctx.player.tick()
    assert ctx.player.error == "Diaporama indisponible."
    assert ctx.player.next_frame_at > ctx.clock()


def test_a_mode_change_by_command_takes_effect_on_the_next_tick(make_player):
    ctx = make_player()
    ctx.player.tick()
    assert ctx.frames.calls and not ctx.slides.calls
    fut = ctx.player.submit({"cmd": "settings", "changes": {"mode": "pipiquant"}})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.slides.calls == ["pipiquant"] and ctx.player.settings.mode == "pipiquant"
    assert Settings.load(ctx.paths.conf).mode == "pipiquant"


def test_going_back_to_slowmovie_resumes_the_film_where_it_was(make_player):
    ctx = make_player(mode="pipiquant")
    write_progress(ctx.paths.progress, ctx.library.videos[0], 400)
    ctx.player.tick()
    ctx.player.submit({"cmd": "settings", "changes": {"mode": "slowmovie"}})
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 400)]


def test_a_mode_change_does_not_interrupt_a_sent_image(make_player):
    ctx = make_player(mode="slowmovie")
    ctx.queue.add(A, png_bytes(size=(800, 480), color="gray"), 60, ctx.clock())
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE"
    shown = len(ctx.display.shown)
    ctx.player.submit({"cmd": "settings", "changes": {"mode": "pipiquant"}})
    ctx.clock.advance(10)
    ctx.player.tick()
    assert len(ctx.display.shown) == shown and not ctx.slides.calls
    ctx.clock.advance(60)
    ctx.player.tick()
    assert ctx.slides.calls == ["pipiquant"]


def test_a_clock_jump_does_not_freeze_the_slideshow(make_player):
    ctx = make_player(mode="pipiquant", pipiquant_interval=600)
    ctx.player.tick()
    ctx.player.next_frame_at = ctx.clock() + 10 * 86400     # horloge du Pi revenue d'un saut
    ctx.clock.advance(1)
    ctx.player.tick()
    assert len(ctx.slides.calls) == 2


def test_pause_stops_the_slideshow_resume_and_skip_draw_a_new_slide(make_player):
    ctx = make_player(mode="pipiquant", pipiquant_interval=600)
    ctx.player.tick()
    ctx.player.submit({"cmd": "pause"})
    ctx.clock.advance(5000)
    ctx.player.tick()
    assert len(ctx.slides.calls) == 1
    ctx.player.submit({"cmd": "resume"})
    ctx.player.tick()
    assert len(ctx.slides.calls) == 2
    ctx.player.submit({"cmd": "skip"})
    ctx.player.tick()
    assert len(ctx.slides.calls) == 3


def test_nothing_is_drawn_outside_the_active_hours(make_player):
    ctx = make_player(mode="pipiquant", active_hours="08:00-09:00")
    noon = datetime(2026, 1, 1, 12, 0).timestamp()
    ctx.clock.t = noon
    ctx.player.tick()
    assert not ctx.slides.calls


def test_the_state_files_name_the_source(make_player):
    ctx = make_player(mode="photos", photos_album_url=ALBUM)
    ctx.player.tick()
    assert ctx.player.snapshot(ctx.clock())["source"] == "photos"
    remote = ctx.player.remote_snapshot(ctx.clock())
    assert remote["source"] == "photos" and remote["next_at"] == ctx.player.next_frame_at
    assert json.loads(ctx.paths.state.read_text())["source"] == "photos"

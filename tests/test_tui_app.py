import json

import pytest

from slowmovie_inkdrop.config import Paths, Settings
from slowmovie_inkdrop.tui import app as tui_app
from slowmovie_inkdrop.tui.app import SlowMovieApp

STATE = {"mode": "FILM", "paused": False, "video": "metropolis.mp4", "percent": 42.0,
         "remaining_seconds": 7200, "queue": [{"id": "a" * 32, "duration": 120}], "error": "",
         "relay_status": "ok (0 nouvelle(s) image(s))", "frame": 10, "frame_count": 100,
         "updated_at": 1.0, "image_until": 0}


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path)
    p.state.write_text(json.dumps(STATE))
    p.library.write_text(json.dumps(
        {"metropolis.mp4": {"size": 1, "status": "ready", "frames": 100, "changed_at": 0}}))
    Settings().save(p.conf)
    return p


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(tui_app, "send_command", lambda path, cmd, timeout=3: calls.append(cmd) or {"ok": True})
    return calls


async def test_player_tab_shows_state(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.pause()
        text = pilot.app.player_text
        assert "metropolis.mp4" in text and "42" in text


async def test_number_keys_switch_tabs(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        assert pilot.app.query_one("TabbedContent").active == "films"
        await pilot.press("3")
        assert pilot.app.query_one("TabbedContent").active == "queue"


async def test_space_toggles_pause(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("space")
        assert sent[-1] == {"cmd": "pause"}


async def test_enter_on_film_plays_it(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("enter")
        assert sent[-1] == {"cmd": "play", "video": "metropolis.mp4"}


async def test_queue_clear(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("3")
        await pilot.press("c")
        assert sent[-1] == {"cmd": "queue_clear"}


async def test_service_down_shows_message_instead_of_crashing(paths, monkeypatch):
    from slowmovie_inkdrop.control import ServiceNotRunning

    def boom(*a, **k):
        raise ServiceNotRunning("Le service slowmovie ne tourne pas.")

    monkeypatch.setattr(tui_app, "send_command", boom)
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("space")
        await pilot.pause()
        assert pilot.app.is_running


async def test_missing_state_does_not_crash(tmp_path, sent):
    async with SlowMovieApp(Paths(tmp_path)).run_test() as pilot:
        await pilot.pause()
        assert "arrêté" in pilot.app.player_text


async def test_enter_on_boolean_setting_toggles_it_and_reloads(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("down", "down", "enter")          # 3e ligne : random_file
        await pilot.pause()
        assert Settings.load(paths.conf).random_file is True
        assert sent[-1] == {"cmd": "reload"}


async def test_editing_a_number_through_the_prompt(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("enter")                           # 1re ligne : delay
        await pilot.pause()
        pilot.app.screen.query_one("Input").value = "45"
        await pilot.press("enter")
        await pilot.pause()
        assert Settings.load(paths.conf).delay == 45


async def test_invalid_setting_is_refused_without_writing(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        pilot.app.screen.query_one("Input").value = "0"
        await pilot.press("enter")
        await pilot.pause()
        assert Settings.load(paths.conf).delay == 120
        assert {"cmd": "reload"} not in sent


async def test_selected_row_survives_refresh(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("4")
        await pilot.press("down", "down", "down")
        table = pilot.app.query_one("#settings-table")
        before = table.cursor_row
        pilot.app.refresh_data()
        await pilot.pause()
        assert table.cursor_row == before == 3


async def test_commands_wait_long_enough_for_an_epaper_refresh(paths, monkeypatch):
    seen = []
    monkeypatch.setattr(tui_app, "send_command", lambda path, cmd, timeout=3: seen.append(timeout) or {"ok": True})
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("space")
    assert seen and seen[0] >= 20

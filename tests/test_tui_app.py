import json

import pytest
from textual.widgets import Tabs

from slowmovie_inkdrop.config import Paths, Settings
from slowmovie_inkdrop.tui import app as tui_app
from slowmovie_inkdrop.tui.app import SlowMovieApp

STATE = {"mode": "FILM", "paused": False, "video": "metropolis.mp4", "percent": 42.0,
         "remaining_seconds": 7200, "queue": [{"id": "a" * 32, "duration": 120, "caption": "Joyeux anniversaire"}], "error": "",
         "relay_status": "ok (0 nouvelle(s) image(s))", "frame": 10, "frame_count": 100,
         "updated_at": 1.0, "image_until": 0}


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path)
    p.state.write_text(json.dumps(STATE))
    p.library.write_text(json.dumps(
        {"metropolis.mp4": {"size": 1, "status": "ready", "frames": 100, "fps": 24.0, "duration": 100.0,
                         "changed_at": 0}}))
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


async def test_queue_tab_shows_the_caption_of_each_image(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()
        row = pilot.app.query_one("#queue-table").get_row_at(0)
        assert "Joyeux anniversaire" in row


async def test_down_from_the_tab_bar_enters_the_content_of_the_tab(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        pilot.app.focus_tabs()
        await pilot.pause()
        assert isinstance(pilot.app.focused, Tabs)
        await pilot.press("down")
        await pilot.pause()
        assert pilot.app.focused.id == "films-table"


async def test_down_from_the_tab_bar_works_on_every_table_tab(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        for key, table in (("3", "queue-table"), ("4", "settings-table"), ("5", "log-view")):
            await pilot.press(key)
            pilot.app.focus_tabs()
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            assert pilot.app.focused.id == table


async def test_up_on_the_first_row_returns_to_the_tab_bar_and_up_elsewhere_moves_the_cursor(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("down", "down")
        await pilot.press("up")
        assert pilot.app.query_one("#settings-table").cursor_row == 1
        await pilot.press("up")
        await pilot.press("up")
        await pilot.pause()
        assert isinstance(pilot.app.focused, Tabs)


async def test_escape_returns_to_the_tab_bar(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(pilot.app.focused, Tabs)


async def test_films_table_shows_position_and_start_columns(paths, sent):
    from slowmovie_inkdrop.starts import StartPoints
    StartPoints(paths.starts).set("metropolis.mp4", 90)
    paths.progress.mkdir(exist_ok=True)
    (paths.progress / "metropolis.mp4.progress").write_text("240")
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        row = pilot.app.query_one("#films-table").get_row_at(0)
        assert "00:00:10" in row and "00:01:30" in row


async def _prompt(pilot, key, text):
    await pilot.press("2")
    await pilot.pause()
    await pilot.press(key)
    await pilot.pause()
    pilot.app.screen.query_one("Input").value = text
    await pilot.press("enter")
    await pilot.pause()


async def test_t_sets_the_start_point(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await _prompt(pilot, "t", "00:01:30")
        assert sent[-1] == {"cmd": "set_start", "video": "metropolis.mp4", "seconds": 90.0}


async def test_g_seeks_to_a_timecode(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await _prompt(pilot, "g", "1:05")
        assert sent[-1] == {"cmd": "seek", "video": "metropolis.mp4", "seconds": 65.0}


async def test_an_invalid_timecode_sends_nothing(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        before = len(sent)
        await _prompt(pilot, "t", "n'importe quoi")
        assert len(sent) == before and pilot.app.is_running


async def test_z_goes_back_to_the_start_point(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("z")
        assert sent[-1] == {"cmd": "reset", "video": "metropolis.mp4"}


async def test_e_sets_the_end_point(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await _prompt(pilot, "e", "00:01:20")
        assert sent[-1] == {"cmd": "set_end", "video": "metropolis.mp4", "seconds": 80.0}


async def test_films_table_shows_the_end_column(paths, sent):
    from slowmovie_inkdrop.starts import StartPoints
    StartPoints(paths.starts).set_end("metropolis.mp4", 80)
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        row = pilot.app.query_one("#films-table").get_row_at(0)
        assert "00:01:20" in row

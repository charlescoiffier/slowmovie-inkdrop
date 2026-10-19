import json
from pathlib import Path

from slowmovie_inkdrop import cli


def test_rsync_command_targets_videos_dir():
    cmd = cli.build_rsync_command(Path("/tmp/mon film.mp4"), "slowmovie.local", "admin",
                                  "/home/admin/SlowMovie")
    assert cmd[0] == "rsync" and "--partial" in cmd
    assert cmd[-1] == "admin@slowmovie.local:/home/admin/SlowMovie/Videos/"
    assert "/tmp/mon film.mp4" in cmd           # un seul argument, pas d'interprétation par un shell


def test_ssh_command_wraps_arguments():
    cmd = cli.build_ssh_command(["play", "mon film.mp4"], "slowmovie.local", "admin")
    assert cmd[:3] == ["ssh", "-t", "admin@slowmovie.local"]
    assert cmd[3] == "slowmovie play 'mon film.mp4'"


def test_is_remote_respects_local_override(monkeypatch):
    monkeypatch.setenv("SLOWMOVIE_LOCAL", "1")
    assert cli.is_remote() is False


def test_add_rejects_missing_and_unsupported(tmp_path, capsys):
    assert cli.main(["add", str(tmp_path / "absent.mp4")]) == 1
    assert "introuvable" in capsys.readouterr().err
    notes = tmp_path / "notes.txt"
    notes.write_text("x")
    assert cli.main(["add", str(notes)]) == 1
    assert "format" in capsys.readouterr().err.lower()


def test_add_runs_rsync(tmp_path, monkeypatch):
    film = tmp_path / "film.mp4"
    film.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert cli.main(["add", str(film)]) == 0
    assert calls[0][0] == "rsync"


def test_format_status_for_each_mode():
    assert "arrêté" in cli.format_status(None)
    film = {"mode": "FILM", "paused": False, "video": "a.mp4", "percent": 12.5,
            "remaining_seconds": 7200, "queue": [], "error": "", "relay_status": "ok"}
    out = cli.format_status(film)
    assert "a.mp4" in out and "12.5" in out and "2 h" in out
    image = dict(film, mode="IMAGE", queue=[{"id": "x", "duration": 60}], image_until=2_000_000.0,
                 updated_at=1_999_940.0)
    assert "image" in cli.format_status(image).lower() and "1 en attente" in cli.format_status(image)
    assert "pause" in cli.format_status(dict(film, paused=True)).lower()


def test_status_command_reads_state_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLOWMOVIE_LOCAL", "1")
    monkeypatch.setenv("SLOWMOVIE_HOME", str(tmp_path))
    (tmp_path / "state.json").write_text(json.dumps(
        {"mode": "FILM", "paused": False, "video": "z.mp4", "percent": 1.0,
         "remaining_seconds": 60, "queue": [], "error": "", "relay_status": ""}))
    assert cli.main(["status"]) == 0
    assert "z.mp4" in capsys.readouterr().out


def test_control_command_reports_service_down(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLOWMOVIE_LOCAL", "1")
    monkeypatch.setenv("SLOWMOVIE_HOME", str(tmp_path))
    monkeypatch.setenv("SLOWMOVIE_SOCKET", "/tmp/slowmovie-test-absent.sock")
    assert cli.main(["pause"]) == 1
    assert "ne tourne pas" in capsys.readouterr().err


def test_overlong_socket_path_gives_a_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLOWMOVIE_LOCAL", "1")
    monkeypatch.setenv("SLOWMOVIE_HOME", str(tmp_path))
    monkeypatch.setenv("SLOWMOVIE_SOCKET", str(tmp_path / ("x" * 120) / "s.sock"))
    assert cli.main(["pause"]) == 1
    assert "impossible" in capsys.readouterr().err.lower()


def test_remote_mode_delegates_to_ssh(monkeypatch):
    monkeypatch.delenv("SLOWMOVIE_LOCAL", raising=False)
    monkeypatch.setattr(cli, "is_remote", lambda: True)
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert cli.main(["status"]) == 0
    assert calls[0][:3] == ["ssh", "-t", "admin@slomovpi.local"]


def test_target_defaults_to_the_pi_hostname_and_can_be_overridden(monkeypatch):
    for name in ("SLOWMOVIE_HOST", "SLOWMOVIE_USER", "SLOWMOVIE_REMOTE_PATH"):
        monkeypatch.delenv(name, raising=False)
    assert cli._client_target() == ("slomovpi.local", "admin", "/home/admin/SlowMovie")
    monkeypatch.setenv("SLOWMOVIE_HOST", "192.168.1.50")
    assert cli._client_target()[0] == "192.168.1.50"

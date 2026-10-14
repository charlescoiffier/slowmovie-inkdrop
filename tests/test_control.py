import shutil
import tempfile
from concurrent.futures import Future
from pathlib import Path

import pytest

from slowmovie_inkdrop.control import ControlServer, ServiceNotRunning, send_command


class FakePlayer:
    def __init__(self):
        self.received = []

    def submit(self, cmd):
        self.received.append(cmd)
        fut = Future()
        fut.set_result({"ok": True, "echo": cmd.get("cmd")})
        return fut


@pytest.fixture
def sock_path():
    d = tempfile.mkdtemp(dir="/tmp")     # chemin court : limite des sockets Unix
    yield Path(d) / "c.sock"
    shutil.rmtree(d, ignore_errors=True)


def test_roundtrip(sock_path):
    player = FakePlayer()
    server = ControlServer(player, sock_path)
    server.start()
    try:
        assert send_command(sock_path, {"cmd": "pause"}) == {"ok": True, "echo": "pause"}
        assert player.received == [{"cmd": "pause"}]
    finally:
        server.stop()
    assert not sock_path.exists()


def test_garbage_request_gets_error_reply(sock_path):
    import socket
    server = ControlServer(FakePlayer(), sock_path)
    server.start()
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(sock_path))
            s.sendall(b"pas du json\n")
            assert b'"ok": false' in s.makefile("rb").readline()
    finally:
        server.stop()


def test_service_not_running(sock_path):
    with pytest.raises(ServiceNotRunning):
        send_command(sock_path, {"cmd": "pause"})


def test_busy_player_gets_a_clear_message_not_an_empty_error(sock_path):
    class BusyPlayer:
        def submit(self, cmd):
            return Future()                  # jamais résolu : le lecteur est en plein rafraîchissement

    server = ControlServer(BusyPlayer(), sock_path, reply_timeout=0.2)
    server.start()
    try:
        reply = send_command(sock_path, {"cmd": "pause"})
    finally:
        server.stop()
    assert reply["ok"] is False and "occupé" in reply["error"]

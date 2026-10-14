"""Socket Unix : la TUI et la CLI envoient des commandes au lecteur."""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
from pathlib import Path


class ServiceNotRunning(RuntimeError):
    pass


class ControlServer:
    def __init__(self, player, path: Path):
        self.player = player
        self.path = Path(path)
        self._server: socketserver.ThreadingUnixStreamServer | None = None

    def start(self) -> None:
        player = self.player

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    request = json.loads(self.rfile.readline(65536) or b"{}")
                    reply = player.submit(request).result(timeout=10)
                except Exception as exc:
                    reply = {"ok": False, "error": f"requête invalide : {exc}"}
                self.wfile.write(json.dumps(reply).encode() + b"\n")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._server = socketserver.ThreadingUnixStreamServer(str(self.path), Handler)
        self._server.daemon_threads = True
        os.chmod(self.path, 0o660)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self.path.exists():
            self.path.unlink()


def send_command(path: Path, command: dict, timeout: float = 10) -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(json.dumps(command).encode() + b"\n")
            return json.loads(s.makefile("rb").readline())
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise ServiceNotRunning("Le service slowmovie ne tourne pas.") from exc

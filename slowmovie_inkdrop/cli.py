"""Ligne de commande : `slowmovie`."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .config import Paths, default_base
from .control import ServiceNotRunning, send_command
from .videos import FILE_TYPES, is_supported


def is_remote() -> bool:
    """Hors du Pi, la commande pilote le Pi par SSH."""
    if os.environ.get("SLOWMOVIE_LOCAL") == "1":
        return False
    return not Path("/proc/device-tree/model").exists()


def _client_target() -> tuple[str, str, str]:
    return (os.environ.get("SLOWMOVIE_HOST", "slomovpi.local"),
            os.environ.get("SLOWMOVIE_USER", "admin"),
            os.environ.get("SLOWMOVIE_REMOTE_PATH", "/home/admin/SlowMovie"))


def build_rsync_command(file: Path, host: str, user: str, remote_dir: str) -> list[str]:
    return ["rsync", "-a", "--partial-dir=.rsync-partial", "--progress", str(file),
            f"{user}@{host}:{remote_dir.rstrip('/')}/Videos/"]


def build_ssh_command(argv: list[str], host: str, user: str) -> list[str]:
    return ["ssh", "-t", f"{user}@{host}", "slowmovie " + " ".join(shlex.quote(a) for a in argv)]


def _hours(seconds: int) -> str:
    if seconds >= 86400:
        return f"{seconds / 86400:.1f} j"
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    return f"{max(seconds // 60, 1)} min"


def format_status(state: dict | None) -> str:
    if not state:
        return "Service arrêté ou jamais démarré (aucun état disponible)."
    lines = []
    if state.get("paused"):
        lines.append("⏸  En pause")
    elif state.get("mode") == "IMAGE":
        left = max(int(state.get("image_until", 0) - state.get("updated_at", 0)), 0)
        lines.append(f"🖼  Image envoyée à l'écran (encore {_hours(left)})")
    elif state.get("source") == "pipiquant":
        lines.append("🎨  Diaporama de π-piquants")
    elif state.get("source") == "photos":
        lines.append("🖼  Diaporama de photos")
    else:
        lines.append("🎞  Lecture du film")
    if state.get("video"):
        lines.append(f"Film : {state['video']} — {state.get('percent')} %"
                     + (f", reste environ {_hours(state['remaining_seconds'])}"
                        if state.get("remaining_seconds") is not None else ""))
    queue = state.get("queue") or []
    lines.append(f"File : {len(queue)} en attente")
    if state.get("relay_status"):
        lines.append(f"Relais : {state['relay_status']}")
    if state.get("error"):
        lines.append(f"⚠ {state['error']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slowmovie",
                                     description="SlowMovie avec envoi d'images à distance")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="lance le service (utilisé par systemd)")
    sub.add_parser("tui", help="ouvre l'interface au clavier (défaut)")
    sub.add_parser("status", help="affiche l'état")
    for name, text in (("pause", "met en pause"), ("resume", "reprend"),
                       ("skip", "passe l'image envoyée ou l'image du film")):
        sub.add_parser(name, help=text)
    play = sub.add_parser("play", help="lance un film")
    play.add_argument("film")
    add = sub.add_parser("add", help="copie un film vers le Pi (rsync)")
    add.add_argument("file", type=Path)
    return parser


def _read_state(paths: Paths) -> dict | None:
    try:
        return json.loads(paths.state.read_text())
    except (OSError, ValueError):
        return None


def _add(file: Path) -> int:
    if not file.is_file():
        print(f"Fichier introuvable : {file}", file=sys.stderr)
        return 1
    if not is_supported(file.name):
        print(f"Format non pris en charge. Extensions acceptées : {', '.join(FILE_TYPES)}",
              file=sys.stderr)
        return 1
    host, user, remote = _client_target()
    return subprocess.call(build_rsync_command(file, host, user, remote))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    command = args.command or "tui"
    if command == "add":
        return _add(args.file)
    if command != "run" and is_remote():
        host, user, _ = _client_target()
        return subprocess.call(build_ssh_command(argv or ["tui"], host, user))
    paths = Paths(default_base())
    if command == "run":
        from .runner import run
        return run(default_base())
    if command == "status":
        print(format_status(_read_state(paths)))
        return 0
    if command == "tui":
        from .tui.app import SlowMovieApp
        SlowMovieApp(paths).run()
        return 0
    payload = {"cmd": command}
    if command == "play":
        payload["video"] = args.film
    try:
        reply = send_command(paths.socket, payload)
    except ServiceNotRunning as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Communication impossible avec le service : {exc}", file=sys.stderr)
        return 1
    if not reply.get("ok"):
        print(reply.get("error", "échec"), file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

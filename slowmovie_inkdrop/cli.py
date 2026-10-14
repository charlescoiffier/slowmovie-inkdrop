"""Ligne de commande : `slowmovie`."""
from __future__ import annotations

import argparse
import sys

from .config import default_base


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slowmovie", description="SlowMovie avec envoi d'images à distance")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="lance le service (utilisé par systemd)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        from .runner import run
        return run(default_base())
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

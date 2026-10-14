"""`slowmovie-relay hash-password` et `slowmovie-relay serve`."""
from __future__ import annotations

import argparse
import getpass
import sys

from .security import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slowmovie-relay")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash-password", help="calcule le haché à mettre dans SLOWMOVIE_RELAY_PASSWORD_HASH")
    serve = sub.add_parser("serve", help="lance le relais")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8077)
    args = parser.parse_args(argv)
    if args.command == "hash-password":
        password = getpass.getpass("Mot de passe : ")
        if password != getpass.getpass("Confirmer : "):
            print("Les mots de passe diffèrent.", file=sys.stderr)
            return 1
        print(hash_password(password))
        return 0
    import uvicorn

    from .app import create_app
    from .config import RelayConfig

    uvicorn.run(create_app(RelayConfig.from_env()), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

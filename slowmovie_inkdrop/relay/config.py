from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RelayConfig:
    data_dir: Path
    password_hash: str
    device_token: str
    min_duration: int = 60
    max_duration: int = 3600
    queue_max: int = 10
    expiry_seconds: int = 21600
    max_upload_bytes: int = 10 * 1024 * 1024
    trust_proxy: bool = False

    @classmethod
    def from_env(cls, env=os.environ) -> RelayConfig:
        try:
            password_hash = env["SLOWMOVIE_RELAY_PASSWORD_HASH"]
            token = env["SLOWMOVIE_RELAY_DEVICE_TOKEN"]
        except KeyError as exc:
            raise SystemExit(f"Variable d'environnement manquante : {exc.args[0]}") from exc
        return cls(
            data_dir=Path(env.get("SLOWMOVIE_RELAY_DATA", "relay-data")),
            password_hash=password_hash, device_token=token,
            min_duration=int(env.get("SLOWMOVIE_RELAY_MIN_DURATION", 60)),
            max_duration=int(env.get("SLOWMOVIE_RELAY_MAX_DURATION", 3600)),
            queue_max=int(env.get("SLOWMOVIE_RELAY_QUEUE_MAX", 10)),
            expiry_seconds=int(env.get("SLOWMOVIE_RELAY_EXPIRY", 21600)),
            trust_proxy=env.get("SLOWMOVIE_RELAY_TRUST_PROXY", "") == "1",
        )

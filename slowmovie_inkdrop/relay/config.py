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
    caption_max: int = 128       # longueur maximale du texte affiché sous l'image
    lease_seconds: int = 120     # une image « récupérée » mais sans accusé est reproposée après ce délai
    admin_password_hash: str = ""   # vide : l'administration est désactivée
    session_secret: str = ""        # vide : clé aléatoire à chaque démarrage
    command_ttl_seconds: int = 600
    state_max_bytes: int = 65536
    session_seconds: int = 28800
    pipiquant_api_url: str = "https://pi-piquant.charlescoiffier.fr"
    pipiquant_api_key: str = ""      # vide : l'onglet π-piquant est désactivé
    pipiquant_timeout: float = 30.0

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
            caption_max=int(env.get("SLOWMOVIE_RELAY_CAPTION_MAX", 128)),
            admin_password_hash=env.get("SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH", ""),
            session_secret=env.get("SLOWMOVIE_RELAY_SESSION_SECRET", ""),
            pipiquant_api_url=env.get("SLOWMOVIE_RELAY_PIPIQUANT_API_URL", cls.pipiquant_api_url).rstrip("/"),
            pipiquant_api_key=env.get("SLOWMOVIE_RELAY_PIPIQUANT_API_KEY", ""),
            trust_proxy=env.get("SLOWMOVIE_RELAY_TRUST_PROXY", "") == "1",
        )

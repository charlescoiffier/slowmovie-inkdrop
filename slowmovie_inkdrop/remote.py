"""Contrat des commandes envoyées du relais au Pi : le relais valide la forme, le Pi revalide tout."""
from __future__ import annotations

import math

from .album_url import valid_album_url
from .config import MODES, SLIDE_INTERVAL

REMOTE_SETTINGS = (
    "delay", "increment", "random_file", "random_frames", "loop", "fullscreen", "subtitles", "timecode",
    "subtitle_track", "contrast", "min_image_seconds", "max_image_seconds", "queue_max",
    "queue_expiry_seconds", "caption_max_chars", "caption_margin", "active_hours", "relay_poll_seconds",
    "mode", "pipiquant_interval", "photos_interval", "pipiquant_title", "photos_album_url",
)
SECRET_SETTINGS = ("photos_album_url",)      # modifiable à distance, mais jamais renvoyé dans l'instantané du Pi
SETTING_TYPES: dict[str, type] = {
    "delay": int, "increment": int, "random_file": bool, "random_frames": bool, "loop": bool,
    "fullscreen": bool, "subtitles": bool, "timecode": bool, "subtitle_track": int, "contrast": float,
    "min_image_seconds": int, "max_image_seconds": int, "queue_max": int, "queue_expiry_seconds": int,
    "caption_max_chars": int, "caption_margin": int, "active_hours": str, "relay_poll_seconds": int,
    "mode": str, "pipiquant_interval": int, "photos_interval": int, "pipiquant_title": bool,
    "photos_album_url": str,
}
SIMPLE_COMMANDS = ("pause", "resume", "skip")
FILM_COMMANDS = ("play", "seek", "set_start", "set_end", "reset")
COMMAND_KINDS = SIMPLE_COMMANDS + FILM_COMMANDS + ("settings",)
POLL_RANGE = (5, 300)
MAX_INT = 10_000_000
MAX_SECONDS = 2 * 24 * 3600
MAX_CHANGES = 20


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def check_setting_value(name: str, value):
    """Valeur typée pour un réglage modifiable à distance, ou ValueError (message en français)."""
    if name not in SETTING_TYPES:
        raise ValueError(f"réglage non modifiable à distance : {name}")
    kind = SETTING_TYPES[name]
    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{name} : vrai ou faux attendu")
        return value
    if kind is str:
        if name == "photos_album_url":
            if not isinstance(value, str) or (value.strip() and not valid_album_url(value.strip())):
                raise ValueError("photos_album_url : lien de partage Google Photos attendu")
            return value.strip()
        if not isinstance(value, str) or len(value) > 50:
            raise ValueError(f"{name} : texte de 50 caractères au plus attendu")
        if name == "mode" and value not in MODES:
            raise ValueError("mode : slowmovie, pipiquant ou photos attendu")
        return value.strip()
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_INT:
            raise ValueError(f"{name} : nombre entier raisonnable attendu")
        number = value
    else:
        if not _is_number(value) or abs(value) > MAX_INT:
            raise ValueError(f"{name} : nombre attendu")
        number = float(value)
    if name == "relay_poll_seconds" and not POLL_RANGE[0] <= number <= POLL_RANGE[1]:
        raise ValueError("relay_poll_seconds : entre 5 et 300")
    if name in ("pipiquant_interval", "photos_interval") and not SLIDE_INTERVAL[0] <= number <= SLIDE_INTERVAL[1]:
        raise ValueError(f"{name} : entre {SLIDE_INTERVAL[0]} et {SLIDE_INTERVAL[1]} secondes")
    return number


def validate_command(payload) -> dict:
    """Commande normalisée `{"kind": ..., ...}` ou ValueError."""
    if not isinstance(payload, dict):
        raise ValueError("commande invalide")
    kind = payload.get("kind")
    if kind not in COMMAND_KINDS:
        raise ValueError(f"commande inconnue : {kind!r}")
    if kind in SIMPLE_COMMANDS:
        return {"kind": kind}
    if kind == "settings":
        changes = payload.get("changes")
        if not isinstance(changes, dict) or not changes or len(changes) > MAX_CHANGES:
            raise ValueError("aucun réglage à modifier")
        return {"kind": kind, "changes": {n: check_setting_value(n, v) for n, v in changes.items()}}
    video = payload.get("video")
    if (not isinstance(video, str) or not video or len(video) > 255 or video.startswith(".")
            or any(c in video for c in "/\\\x00")):
        raise ValueError("nom de film invalide")
    out = {"kind": kind, "video": video}
    if kind in ("seek", "set_start", "set_end"):
        seconds = payload.get("seconds")
        if not _is_number(seconds) or not 0 <= seconds <= MAX_SECONDS:
            raise ValueError("position invalide")
        out["seconds"] = float(seconds)
    return out

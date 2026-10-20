"""Lecture de l'état et formatage pour la TUI (aucune dépendance à Textual)."""
from __future__ import annotations

import json
from collections import deque

from ..config import Paths
from ..starts import StartPoints, frame_to_seconds
from ..videos import read_progress

# (champ de Settings, libellé affiché, type d'édition)
SETTING_FIELDS: list[tuple[str, str, str]] = [
    ("delay", "Délai entre deux images (s)", "int"),
    ("increment", "Images avancées à chaque rafraîchissement", "int"),
    ("random_file", "Film au hasard", "bool"),
    ("random_frames", "Images au hasard", "bool"),
    ("loop", "Boucler sur un seul film", "bool"),
    ("fullscreen", "Remplir l'écran (recadrer)", "bool"),
    ("subtitles", "Sous-titres", "bool"),
    ("timecode", "Afficher le timecode", "bool"),
    ("subtitle_track", "Piste de sous-titres intégrée (0 = première)", "int"),
    ("contrast", "Contraste (0.1 à 3.0)", "float"),
    ("min_image_seconds", "Durée minimale d'une image envoyée (s)", "int"),
    ("max_image_seconds", "Durée maximale d'une image envoyée (s)", "int"),
    ("queue_max", "Taille maximale de la file", "int"),
    ("queue_expiry_seconds", "Expiration d'une image en attente (s)", "int"),
    ("caption_max_chars", "Longueur maximale du texte sous l'image", "int"),
    ("active_hours", "Plage horaire (HH:MM-HH:MM, vide = toujours)", "str"),
    ("relay_url", "Adresse du relais", "str"),
    ("relay_poll_seconds", "Interrogation du relais (s)", "int"),
]


def read_state(paths: Paths) -> dict | None:
    try:
        return json.loads(paths.state.read_text())
    except (OSError, ValueError):
        return None


def film_rows(paths: Paths) -> list[dict]:
    try:
        entries = json.loads(paths.library.read_text())
    except (OSError, ValueError):
        return []
    points = StartPoints(paths.starts)
    starts, ends = points.all(), points.ends()
    rows = []
    for name, entry in sorted(entries.items()):
        percent = position = None
        fps = entry.get("fps")
        if entry.get("status") == "ready" and entry.get("frames"):
            done = read_progress(paths.progress, paths.base / name)
            percent = round(100 * done / entry["frames"], 1)
            position = round(frame_to_seconds(done, fps), 1) if fps else None
        rows.append({"name": name, "status": entry.get("status", "?"), "percent": percent,
                     "error": entry.get("error", ""), "position_seconds": position,
                     "start_seconds": starts.get(name, 0.0), "end_seconds": ends.get(name, 0.0),
                     "duration_seconds": entry.get("duration")})
    return rows


def tail_log(paths: Paths, lines: int = 200) -> str:
    try:
        with paths.log.open(encoding="utf-8", errors="replace") as f:
            return "\n".join(deque((line.rstrip("\n") for line in f), maxlen=lines))
    except OSError:
        return ""

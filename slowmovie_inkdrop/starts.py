"""Points de départ par film et conversions de timecodes."""
from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path


def parse_timecode(text: str) -> float:
    raw = text.strip()
    if not raw:
        raise ValueError("timecode vide : utilisez hh:mm:ss")
    parts = raw.split(":")
    if len(parts) > 3:
        raise ValueError("timecode invalide : utilisez hh:mm:ss")
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        raise ValueError("timecode invalide : utilisez hh:mm:ss") from None
    if any(not math.isfinite(n) or n < 0 for n in numbers):
        raise ValueError("timecode invalide : utilisez hh:mm:ss")
    if len(numbers) > 1 and any(n >= 60 for n in numbers[1:]):
        raise ValueError("les minutes et les secondes doivent être inférieures à 60")
    seconds = 0.0
    for n in numbers:
        seconds = seconds * 60 + n
    return seconds


def format_timecode(seconds: float) -> str:
    total = max(int(seconds), 0)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def seconds_to_frame(seconds: float, fps: float, frame_count: int) -> int:
    return int(min(max(seconds * fps, 0), frame_count))


def frame_to_seconds(frame: int, fps: float) -> float:
    return frame / fps if fps > 0 else 0.0


class StartPoints:
    """Point de départ et point de fin (en secondes) de chaque film, dans `films.json`."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, float]]:
        try:
            data = json.loads(self.path.read_text())
            items = data.items()
        except (OSError, ValueError, AttributeError):
            return {}
        out: dict[str, dict[str, float]] = {}
        for name, entry in items:
            try:
                start = float(entry.get("start_seconds", 0))
                end = float(entry.get("end_seconds", 0))
            except (ValueError, TypeError, AttributeError):
                continue
            if start > 0 or end > 0:
                out[str(name)] = {"start_seconds": max(start, 0.0), "end_seconds": max(end, 0.0)}
        return out

    def all(self) -> dict[str, float]:
        """Points de départ non nuls."""
        with self._lock:
            return {k: v["start_seconds"] for k, v in self._load().items() if v["start_seconds"] > 0}

    def ends(self) -> dict[str, float]:
        """Points de fin non nuls."""
        with self._lock:
            return {k: v["end_seconds"] for k, v in self._load().items() if v["end_seconds"] > 0}

    def get(self, name: str) -> float:
        return self.all().get(name, 0.0)

    def get_end(self, name: str) -> float:
        return self.ends().get(name, 0.0)

    def _update(self, name: str, key: str, seconds: float) -> None:
        with self._lock:
            raw = self._load()
            entry = raw.get(name, {"start_seconds": 0.0, "end_seconds": 0.0})
            entry[key] = float(seconds) if seconds > 0 else 0.0
            if entry["start_seconds"] > 0 or entry["end_seconds"] > 0:
                raw[name] = entry
            else:
                raw.pop(name, None)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(raw))
            os.replace(tmp, self.path)

    def set(self, name: str, seconds: float) -> None:
        self._update(name, "start_seconds", seconds)

    def set_end(self, name: str, seconds: float) -> None:
        self._update(name, "end_seconds", seconds)

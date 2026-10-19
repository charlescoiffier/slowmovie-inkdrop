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
    """Point de départ (en secondes) de chaque film, dans `films.json`."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def all(self) -> dict[str, float]:
        with self._lock:
            return self._read()

    def _read(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text())
            return {str(k): float(v["start_seconds"]) for k, v in data.items() if float(v["start_seconds"]) > 0}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    def get(self, name: str) -> float:
        return self.all().get(name, 0.0)

    def set(self, name: str, seconds: float) -> None:
        with self._lock:
            current = self._read()
            if seconds > 0:
                current[name] = float(seconds)
            else:
                current.pop(name, None)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({k: {"start_seconds": v} for k, v in current.items()}))
            os.replace(tmp, self.path)

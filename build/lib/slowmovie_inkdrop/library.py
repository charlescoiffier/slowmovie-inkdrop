"""Bibliothèque de films : n'expose que les fichiers complets et lisibles."""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from .videos import VideoInfo, is_supported


class Library:
    def __init__(self, videos_dir: Path, json_path: Path,
                 probe: Callable[[Path], VideoInfo], settle_seconds: float = 10.0):
        self.videos_dir = Path(videos_dir)
        self.json_path = Path(json_path)
        self.probe = probe
        self.settle = settle_seconds
        self._lock = threading.RLock()
        self._entries: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.json_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self.json_path.with_name(self.json_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self._entries))
            os.replace(tmp, self.json_path)
        except OSError:
            pass

    def poll(self, now: float) -> None:
        with self._lock:
            current: dict[str, int] = {}
            try:
                children = list(self.videos_dir.iterdir())
            except OSError:
                children = []
            for p in children:
                if is_supported(p.name) and p.is_file():
                    try:
                        current[p.name] = p.stat().st_size
                    except OSError:
                        continue
            for name in [n for n in self._entries if n not in current]:
                del self._entries[name]
            for name, size in current.items():
                entry = self._entries.get(name)
                if entry is None or entry["size"] != size:
                    self._entries[name] = {"size": size, "status": "pending", "changed_at": now}
                    continue
                if entry["status"] == "pending" and now - entry["changed_at"] >= self.settle:
                    try:
                        info = self.probe(self.videos_dir / name)
                    except Exception as exc:  # ffprobe : tout échec = film inutilisable
                        entry.update(status="error", error=str(exc)[:200])
                    else:
                        entry.update(status="ready", frames=info.frame_count, duration=info.duration)
            self._save()

    def ready(self) -> list[Path]:
        with self._lock:
            return sorted(self.videos_dir / n for n, e in self._entries.items()
                          if e["status"] == "ready")

    def rows(self) -> list[dict]:
        with self._lock:
            return [{"name": n, **e} for n, e in sorted(self._entries.items())]

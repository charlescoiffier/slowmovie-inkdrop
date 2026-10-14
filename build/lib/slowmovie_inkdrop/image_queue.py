"""File persistée des images reçues, en attente d'affichage."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_ID = re.compile(r"[0-9a-f]{32}")
SEEN_LIMIT = 200


class QueueFull(Exception):
    pass


@dataclass(frozen=True)
class QueuedImage:
    id: str
    path: Path
    duration: int
    received_at: float


class ImageQueue:
    def __init__(self, directory: Path, max_items: int = 10, expiry_seconds: float = 21600):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_items = max_items
        self.expiry_seconds = expiry_seconds

    # -- lecture ---------------------------------------------------------
    def items(self) -> list[QueuedImage]:
        found: list[QueuedImage] = []
        for meta in self.directory.glob("*.json"):
            if meta.name == "seen.json":
                continue
            try:
                data = json.loads(meta.read_text())
                image_id = data["id"]
                if not _ID.fullmatch(image_id):
                    raise ValueError(image_id)
                found.append(QueuedImage(image_id, self.directory / f"{image_id}.png",
                                         int(data["duration"]), float(data["received_at"])))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(found, key=lambda i: i.received_at)

    def __len__(self) -> int:
        return len(self.items())

    def _seen(self) -> list[str]:
        try:
            return list(json.loads((self.directory / "seen.json").read_text()))
        except (OSError, ValueError):
            return []

    def known(self, image_id: str) -> bool:
        return image_id in self._seen() or any(i.id == image_id for i in self.items())

    # -- écriture --------------------------------------------------------
    def add(self, image_id: str, data: bytes, duration: int, now: float) -> QueuedImage:
        if not _ID.fullmatch(image_id):
            raise ValueError(f"identifiant d'image invalide : {image_id!r}")
        if len(self) >= self.max_items:
            raise QueueFull()
        path = self.directory / f"{image_id}.png"
        path.write_bytes(data)
        meta = self.directory / f"{image_id}.json"
        tmp = meta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"id": image_id, "duration": int(duration), "received_at": now}))
        os.replace(tmp, meta)  # le .json en dernier : une écriture partielle reste invisible
        return QueuedImage(image_id, path, int(duration), now)

    def peek(self, now: float) -> QueuedImage | None:
        for item in self.items():
            if now - item.received_at > self.expiry_seconds:
                self._delete(item)
                continue
            return item
        return None

    def _delete(self, item: QueuedImage) -> None:
        for p in (item.path, self.directory / f"{item.id}.json"):
            try:
                p.unlink()
            except OSError:
                pass

    def discard(self, item: QueuedImage) -> None:
        self._delete(item)
        seen = (self._seen() + [item.id])[-SEEN_LIMIT:]
        try:
            (self.directory / "seen.json").write_text(json.dumps(seen))
        except OSError:
            pass

    def remove(self, image_id: str) -> QueuedImage | None:
        for item in self.items():
            if item.id == image_id:
                self.discard(item)
                return item
        return None

    def clear(self) -> list[QueuedImage]:
        removed = self.items()
        for item in removed:
            self.discard(item)
        return removed

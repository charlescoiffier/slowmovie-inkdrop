"""Stockage du relais : SQLite pour l'état, fichiers PNG pour les images."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

FINAL = ("shown", "failed", "expired")


class Store:
    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        self.images = data_dir / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(data_dir / "relay.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, "
                             "status TEXT NOT NULL, duration INTEGER NOT NULL, created_at REAL NOT NULL, "
                             "fetched_at REAL)")
            columns = [r["name"] for r in self._db.execute("PRAGMA table_info(items)")]
            if "fetched_at" not in columns:               # base créée avant l'ajout du bail
                self._db.execute("ALTER TABLE items ADD COLUMN fetched_at REAL")
            self._db.commit()

    def image_path(self, image_id: str) -> Path:
        return self.images / f"{image_id}.png"

    def add(self, image_id: str, duration: int, png: bytes, now: float) -> None:
        self.image_path(image_id).write_bytes(png)
        with self._lock:
            self._db.execute("INSERT INTO items (id, status, duration, created_at) "
                             "VALUES (?, 'queued', ?, ?)", (image_id, duration, now))
            self._db.commit()

    def active_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM items WHERE status IN ('queued','fetched')"
                                    ).fetchone()[0]

    def get(self, image_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM items WHERE id = ?", (image_id,)).fetchone()
        return dict(row) if row else None

    def offerable(self, now: float, lease: float) -> list[dict]:
        """Images à proposer au Pi : en attente, ou récupérées depuis plus longtemps que le bail."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM items WHERE status = 'queued' OR "
                "(status = 'fetched' AND COALESCE(fetched_at, 0) < ?) ORDER BY created_at",
                (now - lease,)).fetchall()
        return [dict(r) for r in rows]

    def mark_fetched(self, image_id: str, now: float) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET status = 'fetched', fetched_at = ? WHERE id = ?",
                             (now, image_id))
            self._db.commit()

    def set_status(self, image_id: str, status: str) -> None:
        with self._lock:
            self._db.execute("UPDATE items SET status = ? WHERE id = ?", (status, image_id))
            self._db.commit()
        if status in FINAL:
            try:
                self.image_path(image_id).unlink()
            except OSError:
                pass

    def position_and_eta(self, image_id: str) -> tuple[int, int]:
        with self._lock:
            rows = self._db.execute("SELECT id, duration FROM items WHERE status IN ('queued','fetched') "
                                    "ORDER BY created_at").fetchall()
        eta = 0
        for index, row in enumerate(rows, start=1):
            if row["id"] == image_id:
                return index, eta
            eta += row["duration"]
        return 0, 0

    def purge(self, now: float, expiry: float) -> None:
        with self._lock:
            stale = [r["id"] for r in self._db.execute(
                "SELECT id FROM items WHERE status IN ('queued','fetched') AND created_at < ?",
                (now - expiry,)).fetchall()]
        for image_id in stale:
            self.set_status(image_id, "expired")
        with self._lock:
            self._db.execute("DELETE FROM items WHERE status IN ('shown','failed','expired') "
                             "AND created_at < ?", (now - 86400,))
            self._db.commit()

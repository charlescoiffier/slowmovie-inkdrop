"""Stockage du relais : SQLite pour l'état, fichiers PNG pour les images."""
from __future__ import annotations

import json
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
            self._db.execute("PRAGMA secure_delete = ON")     # les lignes effacées (liens d'album) ne traînent pas dans le fichier
            self._db.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, "
                             "status TEXT NOT NULL, duration INTEGER NOT NULL, created_at REAL NOT NULL, "
                             "fetched_at REAL, caption TEXT NOT NULL DEFAULT '')")
            columns = [r["name"] for r in self._db.execute("PRAGMA table_info(items)")]
            if "fetched_at" not in columns:               # base créée avant l'ajout du bail
                self._db.execute("ALTER TABLE items ADD COLUMN fetched_at REAL")
            if "caption" not in columns:                  # base créée avant l'ajout du texte
                self._db.execute("ALTER TABLE items ADD COLUMN caption TEXT NOT NULL DEFAULT ''")
            if "caption_size" not in columns:             # base créée avant le corps de légende
                self._db.execute("ALTER TABLE items ADD COLUMN caption_size INTEGER NOT NULL DEFAULT 0")
            self._db.execute("CREATE TABLE IF NOT EXISTS device_state (id INTEGER PRIMARY KEY CHECK (id = 1), "
                             "body TEXT NOT NULL, received_at REAL NOT NULL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                             "payload TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', "
                             "created_at REAL NOT NULL, done_at REAL)")
            self._db.commit()

    def image_path(self, image_id: str) -> Path:
        return self.images / f"{image_id}.png"

    def add(self, image_id: str, duration: int, png: bytes, now: float, caption: str = "",
            caption_size: int = 0) -> None:
        self.image_path(image_id).write_bytes(png)
        with self._lock:
            self._db.execute("INSERT INTO items (id, status, duration, created_at, caption, caption_size) "
                             "VALUES (?, 'queued', ?, ?, ?, ?)", (image_id, duration, now, caption, caption_size))
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

    # -- pilotage : instantané du Pi et commandes --------------------------
    def put_state(self, body: str, now: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO device_state (id, body, received_at) VALUES (1, ?, ?) "
                             "ON CONFLICT(id) DO UPDATE SET body = excluded.body, received_at = excluded.received_at",
                             (body, now))
            self._db.commit()

    def get_state(self) -> tuple[dict, float] | None:
        with self._lock:
            row = self._db.execute("SELECT body, received_at FROM device_state WHERE id = 1").fetchone()
        return (json.loads(row["body"]), row["received_at"]) if row else None

    def add_command(self, command_id: str, kind: str, payload: dict, now: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO commands (id, kind, payload, status, created_at) "
                             "VALUES (?, ?, ?, 'pending', ?)", (command_id, kind, json.dumps(payload), now))
            self._db.commit()

    def pending_commands(self, now: float, ttl: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, kind, payload FROM commands WHERE status = 'pending' "
                                    "AND created_at >= ? ORDER BY created_at, id", (now - ttl,)).fetchall()
        return [{"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in rows]

    def pending_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM commands WHERE status = 'pending'").fetchone()[0]

    def expire_commands(self, now: float, ttl: float) -> None:
        with self._lock:
            self._db.execute("UPDATE commands SET status = 'expired', done_at = ? "
                             "WHERE status = 'pending' AND created_at < ?", (now, now - ttl))
            self._db.execute("DELETE FROM commands WHERE created_at < ?", (now - 86400,))
            self._redact_finished()
            self._db.commit()

    def _redact_finished(self) -> None:
        """Le lien d'un album est un secret : on l'efface des commandes terminées (appliquées, refusées, expirées)."""
        rows = self._db.execute("SELECT id, payload FROM commands WHERE status != 'pending' "
                                "AND payload LIKE '%photos_album_url%'").fetchall()
        for row in rows:
            payload = json.loads(row["payload"])
            changes = payload.get("changes") if isinstance(payload, dict) else None
            if isinstance(changes, dict) and changes.get("photos_album_url") not in (None, "", "***"):
                changes["photos_album_url"] = "***"
                self._db.execute("UPDATE commands SET payload = ? WHERE id = ?", (json.dumps(payload), row["id"]))

    def ack_command(self, command_id: str, status: str, message: str, now: float) -> bool:
        with self._lock:
            cursor = self._db.execute("UPDATE commands SET status = ?, message = ?, done_at = ? "
                                      "WHERE id = ? AND status = 'pending'",
                                      (status, message[:300], now, command_id))
            self._redact_finished()
            self._db.commit()
            return cursor.rowcount == 1

    def get_command(self, command_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT id, kind, status, message, created_at FROM commands WHERE id = ?",
                                   (command_id,)).fetchone()
        return dict(row) if row else None

    def recent_commands(self, limit: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, kind, status, message, created_at FROM commands "
                                    "ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

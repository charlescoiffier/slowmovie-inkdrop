"""Stockage du relais : SQLite pour l'état, fichiers PNG pour les images."""
from __future__ import annotations

import json
import os
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
            self._db.execute("CREATE TABLE IF NOT EXISTS mode_drafts (mode TEXT PRIMARY KEY, changes TEXT NOT NULL, "
                             "command_id TEXT, sent TEXT)")
            self._db.commit()
        self.current_dir = data_dir / "current"
        self.current_dir.mkdir(parents=True, exist_ok=True)

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

    def pending_mode_command(self, mode: str) -> bool:
        """Une commande d'activation de ce mode attend encore l'accusé du Pi."""
        with self._lock:
            rows = self._db.execute("SELECT payload FROM commands WHERE status = 'pending' AND kind = 'settings'").fetchall()
        for row in rows:
            changes = json.loads(row["payload"]).get("changes")
            if isinstance(changes, dict) and changes.get("mode") == mode:
                return True
        return False

    def pending_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM commands WHERE status = 'pending'").fetchone()[0]

    def expire_commands(self, now: float, ttl: float) -> None:
        with self._lock:
            expired = [r["id"] for r in self._db.execute(
                "SELECT id FROM commands WHERE status = 'pending' AND created_at < ?", (now - ttl,))]
            self._db.execute("UPDATE commands SET status = 'expired', done_at = ? "
                             "WHERE status = 'pending' AND created_at < ?", (now, now - ttl))
            for command_id in expired:
                self._settle_drafts(command_id, applied=False)
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
            row = self._db.execute("SELECT payload FROM commands WHERE id = ?", (command_id,)).fetchone()
            cursor = self._db.execute("UPDATE commands SET status = ?, message = ?, done_at = ? "
                                      "WHERE id = ? AND status = 'pending'",
                                      (status, message[:300], now, command_id))
            self._redact_finished()
            if cursor.rowcount == 1:
                self._settle_drafts(command_id, applied=status == "applied")
                changes = (json.loads(row["payload"]).get("changes") if row else None) or {}
                if status == "applied" and "photos_album_url" in changes:
                    self.delete_current_slide("photos")     # la photo de l'ancien album ne doit plus être montrée
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

    # -- brouillons de réglages : transmis au Pi seulement à l'activation du mode ---------------
    def drafts(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT mode, changes FROM mode_drafts").fetchall()
        return {r["mode"]: json.loads(r["changes"]) for r in rows if json.loads(r["changes"])}

    def draft_for(self, mode: str) -> dict:
        return self.drafts().get(mode, {})

    def merge_draft(self, mode: str, changes: dict) -> None:
        with self._lock:
            row = self._db.execute("SELECT changes FROM mode_drafts WHERE mode = ?", (mode,)).fetchone()
            merged = {**(json.loads(row["changes"]) if row else {}), **changes}
            self._db.execute("INSERT INTO mode_drafts (mode, changes) VALUES (?, ?) "
                             "ON CONFLICT(mode) DO UPDATE SET changes = excluded.changes",
                             (mode, json.dumps(merged)))
            self._db.commit()

    def draft_attached(self, mode: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT command_id FROM mode_drafts WHERE mode = ?", (mode,)).fetchone()
        return bool(row and row["command_id"])

    def clear_draft(self, mode: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM mode_drafts WHERE mode = ?", (mode,))
            self._db.commit()

    def attach_draft(self, mode: str, command_id: str, sent: dict) -> None:
        """Le brouillon part avec la commande d'activation ; on se souvient de ce qui a été envoyé."""
        with self._lock:
            self._db.execute("UPDATE mode_drafts SET command_id = ?, sent = ? WHERE mode = ?",
                             (command_id, json.dumps(sent), mode))
            self._db.commit()

    def _settle_drafts(self, command_id: str, *, applied: bool) -> None:
        """Commande terminée : appliquée, le brouillon perd ce qui a été envoyé ; sinon il est conservé."""
        rows = self._db.execute("SELECT mode, changes, sent FROM mode_drafts WHERE command_id = ?",
                                (command_id,)).fetchall()
        for row in rows:
            remaining = json.loads(row["changes"])
            if applied:
                for key, value in json.loads(row["sent"] or "{}").items():
                    if remaining.get(key) == value:
                        del remaining[key]
            if remaining:
                self._db.execute("UPDATE mode_drafts SET changes = ?, command_id = NULL, sent = NULL WHERE mode = ?",
                                 (json.dumps(remaining), row["mode"]))
            else:
                self._db.execute("DELETE FROM mode_drafts WHERE mode = ?", (row["mode"],))

    # -- dernière diapositive envoyée au Pi (miniature et détails), une par source -------------
    def save_current_slide(self, source: str, details: dict, thumbnail: bytes, now: float) -> None:
        for suffix, data in ((".jpg", thumbnail), (".json", json.dumps({"details": details, "at": now}).encode())):
            tmp = self.current_dir / f"{source}{suffix}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, self.current_dir / f"{source}{suffix}")      # jamais de paire à moitié écrite

    def current_at(self, source: str) -> float | None:
        """Quand la dernière diapositive de cette source a été envoyée au Pi (la page s'en sert pour recharger sa carte)."""
        try:
            return float(json.loads((self.current_dir / f"{source}.json").read_text())["at"])
        except (OSError, ValueError, KeyError):
            return None

    def delete_current_slide(self, source: str) -> None:
        for suffix in (".jpg", ".json"):
            try:
                (self.current_dir / f"{source}{suffix}").unlink()
            except OSError:
                pass

    def current_slide(self, source: str) -> tuple[dict, bytes, float] | None:
        try:
            meta = json.loads((self.current_dir / f"{source}.json").read_text())
            return meta["details"], (self.current_dir / f"{source}.jpg").read_bytes(), float(meta["at"])
        except (OSError, ValueError, KeyError):
            return None

"""Le Pi interroge le relais : connexions sortantes uniquement."""
from __future__ import annotations

import io
import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
from PIL import Image

from .config import Settings
from .image_queue import ImageQueue

log = logging.getLogger(__name__)

MAX_PIXELS = 40_000_000          # le relais réencode à 2000 px max ; au-delà, on ne fait pas confiance
MAX_DOWNLOAD = 15 * 1024 * 1024
FORGOTTEN = (404, 410, 422)      # le relais ne connaît plus cet envoi : l'accusé n'a plus d'objet


class RelayError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RelayClient:
    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None,
                 timeout: float = 15.0, max_download: int = MAX_DOWNLOAD):
        self.max_download = max_download
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._client.request(method, self.base_url + path, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise RelayError(f"relais injoignable : {exc}") from exc
        if response.status_code >= 400:
            raise RelayError(f"relais : HTTP {response.status_code}", response.status_code)
        return response

    def pending(self) -> list[dict]:
        return self._request("GET", "/api/device/pending").json()

    def download(self, image_id: str) -> bytes:
        url = f"{self.base_url}/api/device/image/{image_id}"
        try:
            with self._client.stream("GET", url, headers=self._headers) as response:
                if response.status_code >= 400:
                    raise RelayError(f"relais : HTTP {response.status_code}", response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > self.max_download:
                        raise RelayError("image trop lourde pour le Pi")
                return bytes(data)
        except httpx.HTTPError as exc:
            raise RelayError(f"relais injoignable : {exc}") from exc

    def ack(self, image_id: str, status: str) -> None:
        self._request("POST", f"/api/device/ack/{image_id}", json={"status": status})


class AckOutbox:
    """Accusés à envoyer au relais, conservés sur disque jusqu'à livraison."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            return list(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            return []

    def _write(self, entries: list[dict]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(entries))
        os.replace(tmp, self.path)

    def add(self, image_id: str, status: str) -> None:
        with self._lock:
            self._write(self._read() + [{"id": image_id, "status": status}])

    def pending(self) -> list[dict]:
        with self._lock:
            return self._read()

    def done(self, image_id: str) -> None:
        with self._lock:
            self._write([e for e in self._read() if e["id"] != image_id])


def sync_cycle(client: RelayClient, queue: ImageQueue, outbox: AckOutbox, settings: Settings,
               now: float) -> str:
    """Un tour de synchronisation. Renvoie un statut lisible ; ne lève jamais."""
    try:
        for entry in outbox.pending():
            try:
                client.ack(entry["id"], entry["status"])
            except RelayError as exc:
                if exc.status_code not in FORGOTTEN:
                    log.warning("accusé %s en attente : %s", entry["id"], exc)
                    continue                      # on le réessaiera ; ne bloque pas la suite
                log.info("accusé %s abandonné : le relais ne connaît plus cet envoi", entry["id"])
            outbox.done(entry["id"])
        added = 0
        for item in client.pending():
            image_id = str(item.get("id", ""))
            if queue.known(image_id):
                continue
            if len(queue) >= settings.queue_max:
                break
            try:
                duration = int(item.get("duration", settings.min_image_seconds))
            except (TypeError, ValueError):
                client.ack(image_id, "failed")
                continue
            data = client.download(image_id)
            try:
                with Image.open(io.BytesIO(data)) as probe:
                    if probe.width * probe.height > MAX_PIXELS:
                        raise ValueError("trop de pixels")
                    probe.verify()
            except Exception:  # noqa: BLE001 - toute image douteuse est refusée
                client.ack(image_id, "failed")
                continue
            try:
                queue.add(image_id, data, duration, now)
            except ValueError:
                log.warning("identifiant refusé par le Pi : %r", image_id)
                continue
            added += 1
        return f"ok ({added} nouvelle(s) image(s))"
    except RelayError as exc:
        log.warning("%s", exc)
        return str(exc)
    except Exception as exc:  # jamais de plantage du thread de synchronisation
        log.exception("synchronisation du relais")
        return f"erreur : {exc}"


def relay_loop(player, queue: ImageQueue, outbox: AckOutbox, stop: threading.Event,
               clock: Callable[[], float]) -> None:
    cached: tuple[tuple[str, str], RelayClient] | None = None
    while not stop.is_set():
        settings = player.settings
        if settings.relay_url and settings.relay_token:
            key = (settings.relay_url, settings.relay_token)
            if cached is None or cached[0] != key:
                cached = (key, RelayClient(*key))
            player.relay_status = sync_cycle(cached[1], queue, outbox, settings, clock())
        else:
            player.relay_status = "relais non configuré"
        stop.wait(settings.relay_poll_seconds)

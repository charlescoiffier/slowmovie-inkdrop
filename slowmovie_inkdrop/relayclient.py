"""Le Pi interroge le relais : connexions sortantes uniquement."""
from __future__ import annotations

import io
import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

import httpx
from PIL import Image

from .config import Settings
from .image_queue import ImageQueue
from .imaging import caption_size_or_zero, clean_caption
from .remote import validate_command

log = logging.getLogger(__name__)

MAX_PIXELS = 40_000_000          # le relais réencode à 2000 px max ; au-delà, on ne fait pas confiance
MAX_DOWNLOAD = 15 * 1024 * 1024
SLIDE_TIMEOUT = httpx.Timeout(15.0, read=80.0)   # la passerelle prépare l'image avant de répondre (service, album)
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

    def slide(self, body: dict) -> tuple[bytes, str, int]:
        """Demande une diapositive : (image, légende, corps de légende). Le corps JSON porte le lien d'album."""
        url = f"{self.base_url}/api/device/slide"
        try:
            with self._client.stream("POST", url, json=body, headers=self._headers,
                                     timeout=SLIDE_TIMEOUT) as response:
                if response.status_code >= 400:
                    response.read()
                    detail = ""
                    try:
                        payload = response.json()
                        detail = str(payload.get("detail", "")) if isinstance(payload, dict) else ""
                    except ValueError:
                        pass
                    raise RelayError(detail[:200] or f"relais : HTTP {response.status_code}", response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > self.max_download:
                        raise RelayError("image trop lourde pour le Pi")
                caption = unquote(response.headers.get("x-slide-caption", ""))
                try:
                    size = int(response.headers.get("x-slide-caption-size", "0"))
                except ValueError:
                    size = 0
                return bytes(data), caption, size
        except httpx.HTTPError as exc:
            raise RelayError(f"relais injoignable : {type(exc).__name__}") from exc

    def put_state(self, snapshot: dict) -> None:
        self._request("PUT", "/api/device/state", json=snapshot)

    def commands(self) -> list[dict]:
        return self._request("GET", "/api/device/commands").json()

    def ack_command(self, command_id: str, status: str, message: str = "") -> None:
        self._request("POST", f"/api/device/commands/{command_id}/ack",
                      json={"status": status, "message": message[:300]})


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


class CommandLog:
    """Commandes déjà traitées dont l'accusé n'est pas confirmé : jamais rejouées, accusé réessayé."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.path)

    def get(self, command_id: str) -> dict | None:
        with self._lock:
            return self._read().get(command_id)

    def put(self, command_id: str, status: str, message: str) -> None:
        with self._lock:
            data = self._read()
            data[command_id] = {"status": status, "message": message}
            self._write(data)

    def done(self, command_id: str) -> None:
        with self._lock:
            data = self._read()
            data.pop(command_id, None)
            self._write(data)

    def pending(self) -> dict:
        with self._lock:
            return self._read()


def apply_remote_command(player, command, timeout: float = 60) -> dict:
    """Revalide la commande puis la fait appliquer par le lecteur. Ne lève pas d'exception."""
    try:
        cmd = validate_command(command)
    except ValueError as exc:
        return {"status": "failed", "message": str(exc)}
    payload = {"cmd": cmd.pop("kind"), **cmd}
    try:
        reply = player.submit(payload).result(timeout=timeout)
    except Exception:  # noqa: BLE001
        return {"status": "failed", "message": "le lecteur n'a pas répondu à temps : résultat inconnu "
                                              "(la commande sera peut-être appliquée plus tard)"}
    if reply.get("ok"):
        return {"status": "applied", "message": ""}
    return {"status": "failed", "message": str(reply.get("error", "échec"))[:300]}


def _ack(client: RelayClient, log: CommandLog, command_id: str, status: str, message: str) -> None:
    try:
        client.ack_command(command_id, status, message)
    except RelayError as exc:
        if exc.status_code not in FORGOTTEN:
            log.put(command_id, status, message)          # à réessayer au prochain tour
            return
    log.done(command_id)


def sync_control(client: RelayClient, player, log: CommandLog, now: float) -> str:
    """Un tour de pilotage : accusés en attente, nouvelles commandes, puis état. Ne lève jamais."""
    try:
        for command_id, entry in log.pending().items():
            _ack(client, log, command_id, entry["status"], entry["message"])
        applied = 0
        for item in client.commands():
            command_id = str(item.get("id", ""))
            if log.get(command_id) is not None:
                continue                                    # déjà appliquée : son accusé est réessayé ci-dessus
            # noté AVANT d'appliquer : une coupure en plein milieu ne doit jamais rejouer la commande
            log.put(command_id, "failed", "interrompue par un redémarrage du Pi : résultat inconnu")
            result = apply_remote_command(player, item.get("command"))
            log.put(command_id, result["status"], result["message"])
            _ack(client, log, command_id, result["status"], result["message"])
            applied += 1
        client.put_state(player.remote_snapshot(now))
        return f"ok ({applied} commande(s))"
    except RelayError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        log_ = logging.getLogger(__name__)
        log_.exception("pilotage du relais")
        return f"erreur : {exc}"


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
                caption = clean_caption(str(item.get("caption") or ""), settings.caption_max_chars)
                queue.add(image_id, data, duration, now, caption=caption,
                          caption_size=caption_size_or_zero(item.get("caption_size")))
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
               clock: Callable[[], float], command_log: CommandLog | None = None) -> None:
    cached: tuple[tuple[str, str], RelayClient] | None = None
    while not stop.is_set():
        settings = player.settings
        if settings.relay_url and settings.relay_token:
            key = (settings.relay_url, settings.relay_token)
            if cached is None or cached[0] != key:
                cached = (key, RelayClient(*key))
            player.relay_status = sync_cycle(cached[1], queue, outbox, settings, clock())
            if command_log is not None:
                control = sync_control(cached[1], player, command_log, clock())
                if not control.startswith("ok"):
                    player.relay_status = f"{player.relay_status} | {control}"
        else:
            player.relay_status = "relais non configuré"
        stop.wait(settings.relay_poll_seconds)

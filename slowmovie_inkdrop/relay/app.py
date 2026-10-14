"""API du relais : page d'envoi, envoi d'images, routes de l'appareil."""
from __future__ import annotations

import hmac
import logging
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from ..imaging import InvalidImage, sanitize_upload
from .config import RelayConfig
from .security import RateLimiter, verify_password
from .store import Store

log = logging.getLogger("slowmovie.relay")
_ID = re.compile(r"[0-9a-f]{32}")
_STATUS_FOR_CODE = {"too_large": 413, "format": 415, "corrupt": 422}


class Ack(BaseModel):
    status: str


def create_app(config: RelayConfig, *, clock: Callable[[], float] = time.time) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(config.data_dir)
    limiter = RateLimiter(clock)
    page = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

    @app.middleware("http")
    async def reject_huge_bodies(request: Request, call_next):
        length = request.headers.get("content-length")
        if request.method == "POST" and request.url.path == "/api/send" and not length:
            return JSONResponse({"detail": "Taille de l'envoi manquante."}, status_code=411)
        if length and length.isdigit() and int(length) > config.max_upload_bytes + 65536:
            return JSONResponse({"detail": "Image trop lourde."}, status_code=413)
        return await call_next(request)

    def client_ip(request: Request) -> str:
        if config.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[-1].strip()   # ajouté par notre proxy de confiance
        return request.client.host if request.client else "inconnu"

    def require_device(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {config.device_token}".encode()
        if not authorization or not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(401, "Jeton d'appareil invalide.")

    def check_id(image_id: str) -> str:
        if not _ID.fullmatch(image_id):
            raise HTTPException(404, "Envoi introuvable.")
        return image_id

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/api/config")
    def public_config() -> dict:
        return {"min_duration": config.min_duration, "max_duration": config.max_duration,
                "max_mb": config.max_upload_bytes // (1024 * 1024)}

    @app.post("/api/send", status_code=202)
    def send(request: Request, password: str = Form(default=""), duration: str = Form(default=""),
             image: UploadFile = File(...)) -> JSONResponse:
        ip = client_ip(request)
        wait = limiter.blocked(ip)
        if wait > 0:
            raise HTTPException(429, f"Trop d'essais. Réessayez dans {int(wait // 60) + 1} minute(s).")
        if not verify_password(password, config.password_hash):
            limiter.fail(ip)
            log.warning("mot de passe refusé depuis %s", ip)
            raise HTTPException(401, "Mot de passe incorrect.")
        limiter.success(ip)
        if not limiter.allow_send(ip):
            raise HTTPException(429, "Trop d'envois récents. Réessayez plus tard.")
        text = duration.strip()
        if not (text.isascii() and text.isdigit()) or len(text) > 6:
            raise HTTPException(422, "Durée invalide : indiquez un nombre entier de secondes.")
        seconds = int(text)
        if not config.min_duration <= seconds <= config.max_duration:
            raise HTTPException(422, f"Durée invalide : entre {config.min_duration} et "
                                     f"{config.max_duration} secondes.")
        store.purge(clock(), config.expiry_seconds)
        if store.active_count() >= config.queue_max:
            raise HTTPException(409, "La file d'attente est pleine. Réessayez dans quelques minutes.")
        data = image.file.read(config.max_upload_bytes + 1)
        try:
            png = sanitize_upload(data, max_bytes=config.max_upload_bytes)
        except InvalidImage as exc:
            raise HTTPException(_STATUS_FOR_CODE.get(exc.code, 422), exc.message) from None
        image_id = uuid.uuid4().hex
        store.add(image_id, seconds, png, clock())
        position, eta = store.position_and_eta(image_id)
        return JSONResponse({"id": image_id, "position": position, "eta_seconds": eta}, status_code=202)

    @app.get("/api/status/{image_id}")
    def status(image_id: str) -> dict:
        item = store.get(check_id(image_id))
        if item is None:
            raise HTTPException(404, "Envoi introuvable.")
        position, eta = store.position_and_eta(image_id)
        return {"status": item["status"], "position": position, "eta_seconds": eta}

    @app.get("/api/device/pending", dependencies=[Depends(require_device)])
    def pending() -> list[dict]:
        store.purge(clock(), config.expiry_seconds)
        offered = store.offerable(clock(), config.lease_seconds)
        return [{"id": i["id"], "duration": i["duration"]} for i in offered]

    @app.get("/api/device/image/{image_id}", dependencies=[Depends(require_device)])
    def device_image(image_id: str) -> Response:
        item = store.get(check_id(image_id))
        path = store.image_path(image_id)
        if item is None or item["status"] not in ("queued", "fetched") or not path.exists():
            raise HTTPException(404, "Image indisponible.")
        store.mark_fetched(image_id, clock())
        return FileResponse(path, media_type="image/png")

    @app.post("/api/device/ack/{image_id}", dependencies=[Depends(require_device)])
    def ack(image_id: str, body: Ack) -> dict:
        if body.status not in ("shown", "failed"):
            raise HTTPException(422, "Statut inconnu.")
        if store.get(check_id(image_id)) is None:
            raise HTTPException(404, "Envoi introuvable.")
        store.set_status(image_id, body.status)
        return {"ok": True}

    return app

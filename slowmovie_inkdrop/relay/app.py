"""API du relais : page d'envoi, envoi d'images, routes de l'appareil."""
from __future__ import annotations

import hmac
import json
import logging
import random
import re
import secrets
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from ..imaging import InvalidImage, clean_caption, sanitize_upload
from ..remote import FILM_COMMANDS, validate_command
from .config import RelayConfig
from .security import RateLimiter, make_session_token, verify_password, verify_session_token
from .store import Store

log = logging.getLogger("slowmovie.relay")
_ID = re.compile(r"[0-9a-f]{32}")
_STATUS_FOR_CODE = {"too_large": 413, "format": 415, "corrupt": 422}
PIPIQUANT_DECIMALS = (1, 200)
PIPIQUANT_ANGLES = (1, 359)
def pipiquant_legend(decimals: int, angle: int) -> str:
    return f"π-piquant • 1 = {angle}° • {decimals} {'décimale' if decimals == 1 else 'décimales'}"


PIPIQUANT_SIZE = 800      # grand côté en pixels : la largeur de l'écran


class Ack(BaseModel):
    status: str


class CommandAck(BaseModel):
    status: str
    message: str = ""


def create_app(config: RelayConfig, *, clock: Callable[[], float] = time.time,
               http_client: httpx.Client | None = None, rng: random.Random | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(config.data_dir)
    limiter = RateLimiter(clock)
    admin_limiter = RateLimiter(clock)
    admin_enabled = bool(config.admin_password_hash)
    pipiquant_enabled = bool(config.pipiquant_api_key)
    rng = rng or random.SystemRandom()
    http = http_client or httpx.Client(timeout=config.pipiquant_timeout, follow_redirects=False)
    session_key = (config.session_secret or secrets.token_hex(32)).encode()
    COOKIE = "sm_admin"
    CSRF_HEADER = "x-requested-with"
    page = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    envoi_page = (Path(__file__).parent / "static" / "envoi.html").read_text(encoding="utf-8")

    @app.middleware("http")
    async def reject_huge_bodies(request: Request, call_next):
        length = request.headers.get("content-length")
        path = request.url.path
        if request.method == "POST" and path in ("/api/send", "/api/send-pipiquant") and not length:
            return JSONResponse({"detail": "Taille de l'envoi manquante."}, status_code=411)
        limit = 65536 if path == "/api/send-pipiquant" else config.max_upload_bytes + 65536   # ce formulaire n'a pas d'image
        if length and length.isdigit() and int(length) > limit:
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

    def require_admin(request: Request) -> None:
        if not admin_enabled:
            raise HTTPException(503, "Administration désactivée.")
        if request.headers.get(CSRF_HEADER) != "slowmovie":
            raise HTTPException(403, "En-tête de requête manquant.")
        if not verify_session_token(session_key, request.cookies.get(COOKIE, ""), clock()):
            raise HTTPException(401, "Connexion administrateur requise.")

    async def read_json(request: Request, limit: int):
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "Corps de requête trop gros.")
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks) or b"null")
        except ValueError:
            raise HTTPException(422, "JSON invalide.") from None

    def device_online() -> tuple[dict | None, float | None, bool]:
        stored = store.get_state()
        if stored is None:
            return None, None, False
        state, received = stored
        poll = ((state.get("settings") or {}).get("relay_poll_seconds") if isinstance(state, dict) else None)
        interval = poll if isinstance(poll, int) and not isinstance(poll, bool) and 5 <= poll <= 300 else 20
        return state, received, clock() - received < max(60, 3 * interval)

    def check_id(image_id: str) -> str:
        if not _ID.fullmatch(image_id):
            raise HTTPException(404, "Envoi introuvable.")
        return image_id

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/envoi/{image_id}", response_class=HTMLResponse)
    def envoi_page_route(image_id: str) -> str:
        return envoi_page

    @app.get("/api/config")
    def public_config() -> dict:
        return {"min_duration": config.min_duration, "max_duration": config.max_duration,
                "max_mb": config.max_upload_bytes // (1024 * 1024), "caption_max": config.caption_max,
                "admin": admin_enabled, "pipiquant": pipiquant_enabled}

    def check_sender(request: Request, password: str, duration: str) -> int:
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
        return seconds

    def check_queue() -> None:
        store.purge(clock(), config.expiry_seconds)
        if store.active_count() >= config.queue_max:
            raise HTTPException(409, "La file d'attente est pleine. Réessayez dans quelques minutes.")

    def enqueue(seconds: int, data: bytes, caption: str) -> dict:
        try:
            png = sanitize_upload(data, max_bytes=config.max_upload_bytes)
        except InvalidImage as exc:
            raise HTTPException(_STATUS_FOR_CODE.get(exc.code, 422), exc.message) from None
        image_id = uuid.uuid4().hex
        store.add(image_id, seconds, png, clock(), caption)
        position, eta = store.position_and_eta(image_id)
        return {"id": image_id, "position": position, "eta_seconds": eta}

    @app.post("/api/send", status_code=202)
    def send(request: Request, password: str = Form(default=""), duration: str = Form(default=""),
             caption: str = Form(default=""), image: UploadFile = File(...)) -> JSONResponse:
        seconds = check_sender(request, password, duration)
        text_caption = clean_caption(caption, 10_000)
        if len(text_caption) > config.caption_max:
            raise HTTPException(422, f"Texte trop long : {config.caption_max} caractères au maximum.")
        check_queue()
        data = image.file.read(config.max_upload_bytes + 1)
        return JSONResponse(enqueue(seconds, data, text_caption), status_code=202)

    def fetch_pipiquant(decimals: int, angle: int) -> bytes:
        """Demande le PNG au service π-piquant ; toute panne devient une erreur lisible (jamais le détail du service)."""
        params = {"source": "pi", "decimales": decimals, "angle": angle, "taille": PIPIQUANT_SIZE,
                  "titre": "non"}      # la légende est écrite par le Pi, en plus gros
        headers = {"Authorization": f"Bearer {config.pipiquant_api_key}"}
        unavailable = HTTPException(502, "Le service π-piquant ne répond pas. Réessayez dans un instant.")
        try:
            with http.stream("GET", f"{config.pipiquant_api_url}/api/png", params=params, headers=headers) as r:
                if r.status_code == 503:
                    raise HTTPException(503, "Le service π-piquant est saturé. Réessayez dans un instant.")
                if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/png"):
                    log.error("π-piquant a répondu %s", r.status_code)
                    raise HTTPException(502, "Le service π-piquant a refusé la demande.")
                data = bytearray()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > config.max_upload_bytes:
                        log.error("π-piquant a renvoyé une image trop lourde")
                        raise HTTPException(502, "Le service π-piquant a renvoyé une image trop lourde.")
                return bytes(data)
        except httpx.HTTPError as exc:
            log.error("π-piquant injoignable : %s", type(exc).__name__)
            raise unavailable from None

    @app.post("/api/send-pipiquant", status_code=202)
    def send_pipiquant(request: Request, password: str = Form(default=""),
                       duration: str = Form(default="")) -> JSONResponse:
        if not pipiquant_enabled:
            raise HTTPException(503, "Le pi-piquant est désactivé.")
        seconds = check_sender(request, password, duration)
        check_queue()
        decimals = rng.randint(*PIPIQUANT_DECIMALS)
        angle = rng.randint(*PIPIQUANT_ANGLES)
        data = fetch_pipiquant(decimals, angle)
        try:
            body = enqueue(seconds, data, pipiquant_legend(decimals, angle))
        except HTTPException as exc:
            if exc.status_code in (413, 415, 422):
                raise HTTPException(502, "Le service π-piquant a renvoyé une image illisible.") from None
            raise
        return JSONResponse({**body, "decimals": decimals, "angle": angle}, status_code=202)

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
        return [{"id": i["id"], "duration": i["duration"], "caption": i["caption"]} for i in offered]

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

    # -- appareil : instantané et commandes ------------------------------
    @app.put("/api/device/state", dependencies=[Depends(require_device)])
    async def put_state(request: Request) -> dict:
        data = await read_json(request, config.state_max_bytes)
        if not isinstance(data, dict):
            raise HTTPException(422, "Objet JSON attendu.")
        store.put_state(json.dumps(data), clock())
        return {"ok": True}

    @app.get("/api/device/commands", dependencies=[Depends(require_device)])
    def device_commands() -> list[dict]:
        store.expire_commands(clock(), config.command_ttl_seconds)
        return [{"id": c["id"], "command": c["payload"]}
                for c in store.pending_commands(clock(), config.command_ttl_seconds)]

    @app.post("/api/device/commands/{command_id}/ack", dependencies=[Depends(require_device)])
    def device_command_ack(command_id: str, body: CommandAck) -> dict:
        if body.status not in ("applied", "failed"):
            raise HTTPException(422, "Statut inconnu.")
        if not _ID.fullmatch(command_id) or not store.ack_command(command_id, body.status, body.message, clock()):
            raise HTTPException(404, "Commande introuvable ou déjà traitée.")
        return {"ok": True}

    # -- administrateur ----------------------------------------------------
    @app.post("/api/admin/login")
    async def admin_login(request: Request) -> JSONResponse:
        if not admin_enabled:
            raise HTTPException(503, "Administration désactivée.")
        if request.headers.get(CSRF_HEADER) != "slowmovie":
            raise HTTPException(403, "En-tête de requête manquant.")
        ip = client_ip(request)
        wait = admin_limiter.blocked(ip)
        if wait > 0:
            raise HTTPException(429, f"Trop d'essais. Réessayez dans {int(wait // 60) + 1} minute(s).")
        data = await read_json(request, 1024)
        password = data.get("password") if isinstance(data, dict) else None
        if not isinstance(password, str):
            password = ""
        if not verify_password(password, config.admin_password_hash):
            admin_limiter.fail(ip)
            log.warning("connexion administrateur refusée depuis %s", ip)
            raise HTTPException(401, "Mot de passe administrateur incorrect.")
        admin_limiter.success(ip)
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, make_session_token(session_key, clock(), config.session_seconds),
                            max_age=config.session_seconds, httponly=True, secure=True,
                            samesite="strict", path="/api/admin")
        return response

    @app.post("/api/admin/logout", dependencies=[Depends(require_admin)])
    def admin_logout() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/api/admin")
        return response

    @app.get("/api/admin/state", dependencies=[Depends(require_admin)])
    def admin_state() -> dict:
        store.expire_commands(clock(), config.command_ttl_seconds)
        state, received, online = device_online()
        return {"online": online, "last_seen_seconds": None if received is None else int(clock() - received),
                "state": state, "commands": store.recent_commands(10)}

    @app.post("/api/admin/commands", status_code=202, dependencies=[Depends(require_admin)])
    async def admin_command(request: Request) -> JSONResponse:
        try:
            command = validate_command(await read_json(request, 4096))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        state, _, online = device_online()
        if command["kind"] in FILM_COMMANDS:
            known = {f.get("name") for f in (state or {}).get("films", []) if isinstance(f, dict)}
            if command["video"] not in known:
                raise HTTPException(422, "Film inconnu du Pi (son état n'est peut-être pas encore arrivé).")
        store.expire_commands(clock(), config.command_ttl_seconds)
        if store.pending_count() >= 20:
            raise HTTPException(429, "Trop de commandes en attente. Réessayez dans un instant.")
        command_id = uuid.uuid4().hex
        store.add_command(command_id, command["kind"], command, clock())
        return JSONResponse({"id": command_id, "online": online}, status_code=202)

    @app.get("/api/admin/commands/{command_id}", dependencies=[Depends(require_admin)])
    def admin_command_status(command_id: str) -> dict:
        found = store.get_command(command_id) if _ID.fullmatch(command_id) else None
        if found is None:
            raise HTTPException(404, "Commande introuvable.")
        return {"status": found["status"], "message": found["message"]}

    return app

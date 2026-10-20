"""API du relais : page d'envoi, envoi d'images, routes de l'appareil."""
from __future__ import annotations

import base64
import hmac
import io
import json
import logging
import random
import re
import secrets
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from PIL import Image
from pydantic import BaseModel

from ..album_url import valid_album_url
from ..imaging import InvalidImage, clean_caption, sanitize_upload
from ..remote import FILM_COMMANDS, validate_command
from .config import RelayConfig
from .photos import AlbumPhotos, PhotosError
from .security import RateLimiter, make_session_token, verify_password, verify_session_token
from .store import Store

log = logging.getLogger("slowmovie.relay")
_ID = re.compile(r"[0-9a-f]{32}")
_STATUS_FOR_CODE = {"too_large": 413, "format": 415, "corrupt": 422}
PIPIQUANT_DECIMALS = (1, 200)
PIPIQUANT_ANGLES = (1, 359)
def make_thumbnail(png: bytes) -> bytes:
    """Miniature JPEG de la diapositive envoyée au Pi (affichée derrière le mot de passe général)."""
    with Image.open(io.BytesIO(png)) as img:
        img = img.convert("RGB")
        img.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=70)
        return out.getvalue()


def pipiquant_legend(decimals: int, angle: int) -> str:
    return f"π-piquant • 1 = {angle}° • {decimals} {'décimale' if decimals == 1 else 'décimales'}"


PIPIQUANT_CAPTION_SIZE = 14      # corps (px) de la légende d'un π-piquant
SLOWMOVIE_RHYTHMS = (30, 60, 120, 300, 600, 1800)    # pastilles du mot de passe général, en secondes
SLIDE_RHYTHMS = (300, 600, 1800, 3600, 10800, 21600, 43200, 86400)
# Réglages propres à chaque mode : modifiables à tout moment, transmis au Pi seulement à l'activation du mode.
DRAFT_FIELDS = {
    "slowmovie": {"delay", "increment", "random_file", "random_frames", "loop", "fullscreen", "subtitles",
                  "subtitle_track", "timecode"},
    "pipiquant": {"pipiquant_interval", "pipiquant_title"},
    "photos": {"photos_interval", "photos_album_url"},
}
THUMBNAIL_WIDTH = 320
MODE_SOURCES = ("slowmovie", "pipiquant", "photos")
PIPIQUANT_SIZE = 800      # grand côté en pixels : la largeur de l'écran


class Ack(BaseModel):
    status: str


class CommandAck(BaseModel):
    status: str
    message: str = ""


def create_app(config: RelayConfig, *, clock: Callable[[], float] = time.time,
               http_client: httpx.Client | None = None, rng: random.Random | None = None,
               album_photos: AlbumPhotos | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(config.data_dir)
    limiter = RateLimiter(clock)
    admin_limiter = RateLimiter(clock)
    mode_limiter = RateLimiter(clock)
    admin_enabled = bool(config.admin_password_hash)
    pipiquant_enabled = bool(config.pipiquant_api_key)
    rng = rng or random.SystemRandom()
    http = http_client or httpx.Client(timeout=config.pipiquant_timeout, follow_redirects=False)
    albums = album_photos or AlbumPhotos(http, max_bytes=config.max_upload_bytes)
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

    def enqueue(seconds: int, data: bytes, caption: str, caption_size: int = 0) -> dict:
        try:
            png = sanitize_upload(data, max_bytes=config.max_upload_bytes)
        except InvalidImage as exc:
            raise HTTPException(_STATUS_FOR_CODE.get(exc.code, 422), exc.message) from None
        image_id = uuid.uuid4().hex
        store.add(image_id, seconds, png, clock(), caption, caption_size)
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

    def make_pipiquant(title: bool) -> tuple[bytes, str, int, int]:
        """Tire un dessin et le demande au service : (image, légende, décimales, angle)."""
        decimals = rng.randint(*PIPIQUANT_DECIMALS)
        angle = rng.randint(*PIPIQUANT_ANGLES)
        data = fetch_pipiquant(decimals, angle)
        return data, pipiquant_legend(decimals, angle) if title else "", decimals, angle

    @app.post("/api/send-pipiquant", status_code=202)
    def send_pipiquant(request: Request, password: str = Form(default=""), duration: str = Form(default=""),
                       title: str = Form(default="oui")) -> JSONResponse:
        if not pipiquant_enabled:
            raise HTTPException(503, "Le pi-piquant est désactivé.")
        if title not in ("oui", "non"):
            raise HTTPException(422, "Choix du titre invalide.")
        seconds = check_sender(request, password, duration)
        check_queue()
        data, legend, decimals, angle = make_pipiquant(title == "oui")
        try:
            body = enqueue(seconds, data, legend, PIPIQUANT_CAPTION_SIZE if legend else 0)
        except HTTPException as exc:
            if exc.status_code in (413, 415, 422):
                raise HTTPException(502, "Le service π-piquant a renvoyé une image illisible.") from None
            raise
        return JSONResponse({**body, "decimals": decimals, "angle": angle}, status_code=202)

    def build_slide(body) -> Response:
        if not isinstance(body, dict):
            raise HTTPException(422, "Objet JSON attendu.")
        source, legend, size, details = body.get("source"), "", 0, {}
        if source == "pipiquant":
            if not isinstance(body.get("title"), bool):
                raise HTTPException(422, "Le choix du titre doit être vrai ou faux.")
            if not pipiquant_enabled:
                raise HTTPException(503, "Le pi-piquant est désactivé.")
            data, legend, decimals, angle = make_pipiquant(body["title"])
            size = PIPIQUANT_CAPTION_SIZE if legend else 0
            details = {"decimals": decimals, "angle": angle}
        elif source == "photos":
            url = body.get("album_url")
            if not valid_album_url(url):
                raise HTTPException(422, "Lien d'album invalide.")
            try:
                data, index, count = albums.photo_with_position(url)
                details = {"index": index, "count": count}
            except PhotosError as exc:
                raise HTTPException(502, exc.message) from None
            except Exception as exc:  # noqa: BLE001 - jamais de trace (elle pourrait contenir le lien)
                log.error("album : erreur inattendue %s", type(exc).__name__)
                raise HTTPException(502, "Album illisible : la page de l'album n'a pas pu être lue.") from None
        else:
            raise HTTPException(422, "Source inconnue.")
        try:
            png = sanitize_upload(data, max_bytes=config.max_upload_bytes)
        except InvalidImage:
            raise HTTPException(502, "L'image reçue est illisible.") from None
        try:
            store.save_current_slide(source, details, make_thumbnail(png), clock())
        except (OSError, ValueError):
            log.warning("miniature de la diapositive non enregistrée")
        return Response(png, media_type="image/png",
                        headers={"X-Slide-Caption": quote(legend, safe=""), "X-Slide-Caption-Size": str(size)})

    @app.post("/api/device/slide", dependencies=[Depends(require_device)])
    async def device_slide(request: Request) -> Response:
        body = await read_json(request, 2048)
        return await run_in_threadpool(build_slide, body)

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
        return [{"id": i["id"], "duration": i["duration"], "caption": i["caption"],
                 "caption_size": i["caption_size"]} for i in offered]

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

    # -- choix du mode (mot de passe général) -----------------------------
    async def general_request(request: Request) -> dict:
        if request.headers.get(CSRF_HEADER) != "slowmovie":
            raise HTTPException(403, "En-tête de requête manquant.")
        ip = client_ip(request)
        wait = mode_limiter.blocked(ip)
        if wait > 0:
            raise HTTPException(429, f"Trop d'essais. Réessayez dans {int(wait // 60) + 1} minute(s).")
        data = await read_json(request, 1024)
        if not isinstance(data, dict):
            raise HTTPException(422, "Objet JSON attendu.")
        password = data.get("password")
        if not isinstance(password, str) or not await run_in_threadpool(verify_password, password,
                                                                         config.password_hash):
            mode_limiter.fail(ip)
            log.warning("mot de passe refusé depuis %s", ip)
            raise HTTPException(401, "Mot de passe incorrect.")
        mode_limiter.success(ip)
        return data

    def active_source() -> str | None:
        state, _, _ = device_online()
        source = state.get("source") if isinstance(state, dict) else None
        return source if source in MODE_SOURCES else None

    def public_drafts() -> dict:
        """Brouillons sans le lien d'album (un secret) : seulement le fait qu'il est en attente."""
        return {mode: {k: (True if k == "photos_album_url" and v else v) for k, v in changes.items()}
                for mode, changes in store.drafts().items()}

    def settings_for_mode(mode: str, changes: dict) -> JSONResponse:
        """Réglages d'un mode : envoyés tout de suite si le mode est actif, sinon gardés en brouillon."""
        try:
            command = validate_command({"kind": "settings", "changes": changes})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if active_source() == mode:
            return queue_settings(command["changes"])
        store.merge_draft(mode, command["changes"])
        return JSONResponse({"draft": True, "mode": mode}, status_code=202)

    def queue_settings(changes: dict) -> JSONResponse:
        command = validate_command({"kind": "settings", "changes": changes})
        store.expire_commands(clock(), config.command_ttl_seconds)
        if store.pending_count() >= 20:
            raise HTTPException(429, "Trop de commandes en attente. Réessayez dans un instant.")
        command_id = uuid.uuid4().hex
        store.add_command(command_id, command["kind"], command, clock())
        return JSONResponse({"id": command_id, "online": device_online()[2]}, status_code=202)

    def whole(value):
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def film_info(state) -> dict | None:
        if not isinstance(state, dict) or not isinstance(state.get("video"), str):
            return None
        return {"name": state["video"][:255], "position_seconds": state.get("position_seconds"),
                "duration_seconds": state.get("duration_seconds")}

    def displaying(state) -> str | None:
        return state.get("mode") if isinstance(state, dict) and state.get("mode") in ("FILM", "IMAGE") else None

    @app.post("/api/mode/state")
    async def mode_state(request: Request) -> dict:
        await general_request(request)
        state, received, online = device_online()
        settings = state.get("settings") if isinstance(state, dict) and isinstance(state.get("settings"), dict) else {}
        source = state.get("source") if isinstance(state, dict) else None
        next_at, sent_at = (state or {}).get("next_at"), (state or {}).get("sent_at")
        next_in = None
        if all(isinstance(v, int | float) and not isinstance(v, bool) for v in (next_at, sent_at)) and next_at > 0:
            next_in = max(0, int(next_at - sent_at - (clock() - received)))
        host = settings.get("photos_album_host")
        error = (state or {}).get("error")
        return {
            "online": online, "last_seen_seconds": None if received is None else int(clock() - received),
            "source": source if source in MODE_SOURCES else None,
            "rhythms": {"slowmovie": whole(settings.get("delay")), "pipiquant": whole(settings.get("pipiquant_interval")),
                        "photos": whole(settings.get("photos_interval"))},
            "title": settings.get("pipiquant_title") if isinstance(settings.get("pipiquant_title"), bool) else None,
            "album_set": settings.get("photos_album_set") is True,
            "album_host": host if isinstance(host, str) and valid_album_url(f"https://{host}/x") else "",
            "next_in_seconds": next_in,
            "error": error[:200] if isinstance(error, str) else "",
            "film": film_info(state), "displaying": displaying(state), "drafts": public_drafts(),
            "pipiquant_available": pipiquant_enabled,
            "pi_outdated": isinstance(state, dict) and "source" not in state,
            "presets": {"slowmovie": list(SLOWMOVIE_RHYTHMS), "slide": list(SLIDE_RHYTHMS)},
        }

    @app.post("/api/mode/command")
    async def mode_command(request: Request) -> dict:
        """Résultat d'une commande créée depuis l'onglet Mode (accusé du Pi) : appliquée, refusée ou expirée."""
        data = await general_request(request)
        command_id = data.get("id")
        store.expire_commands(clock(), config.command_ttl_seconds)
        found = store.get_command(command_id) if isinstance(command_id, str) and _ID.fullmatch(command_id) else None
        if found is None:
            raise HTTPException(404, "Commande introuvable.")
        return {"status": found["status"], "message": found["message"][:300]}

    @app.post("/api/mode/current")
    async def mode_current(request: Request) -> dict:
        """Miniature et détails de la dernière diapositive envoyée au Pi (π-piquant ou photo)."""
        data = await general_request(request)
        source = data.get("source")
        if source not in ("pipiquant", "photos"):
            raise HTTPException(422, "Mode inconnu.")
        current = store.current_slide(source)
        if current is None:
            raise HTTPException(404, "Aucune diapositive pour l'instant.")
        details, thumbnail, at = current
        return {"source": source, "details": details, "age_seconds": max(0, int(clock() - at)),
                "thumbnail": "data:image/jpeg;base64," + base64.b64encode(thumbnail).decode()}

    @app.post("/api/mode/set", status_code=202)
    async def mode_set(request: Request) -> JSONResponse:
        data = await general_request(request)
        source = data.get("source")
        if source not in MODE_SOURCES:
            raise HTTPException(422, "Mode inconnu.")
        if source == "pipiquant" and not pipiquant_enabled:
            raise HTTPException(503, "Le pi-piquant est désactivé.")
        draft = store.draft_for(source)
        if source == "photos":
            state, _, _ = device_online()
            settings = (state or {}).get("settings") if isinstance(state, dict) else None
            known = isinstance(settings, dict) and settings.get("photos_album_set") is True
            if not known and not draft.get("photos_album_url"):
                raise HTTPException(409, "Aucun album n'est configuré (réglages administrateur).")
        response = queue_settings({"mode": source, **draft})
        if draft:
            store.attach_draft(source, json.loads(response.body)["id"], draft)
        return response

    @app.post("/api/mode/rhythm", status_code=202)
    async def mode_rhythm(request: Request) -> JSONResponse:
        data = await general_request(request)
        source, seconds = data.get("source"), data.get("seconds")
        field = {"slowmovie": "delay", "pipiquant": "pipiquant_interval", "photos": "photos_interval"}.get(source)
        allowed = SLOWMOVIE_RHYTHMS if source == "slowmovie" else SLIDE_RHYTHMS
        if field is None or isinstance(seconds, bool) or not isinstance(seconds, int) or seconds not in allowed:
            raise HTTPException(422, "Rythme non proposé.")
        return settings_for_mode(source, {field: seconds})

    @app.post("/api/mode/title", status_code=202)
    async def mode_title(request: Request) -> JSONResponse:
        data = await general_request(request)
        if not isinstance(data.get("title"), bool):
            raise HTTPException(422, "Le choix du titre doit être vrai ou faux.")
        return settings_for_mode("pipiquant", {"pipiquant_title": data["title"]})

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
        if not await run_in_threadpool(verify_password, password, config.admin_password_hash):
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
                "state": state, "commands": store.recent_commands(10),
                "source": active_source(), "drafts": public_drafts()}

    @app.post("/api/admin/commands", status_code=202, dependencies=[Depends(require_admin)])
    async def admin_command(request: Request) -> JSONResponse:
        payload = await read_json(request, 4096)
        for_mode = payload.pop("for_mode", None) if isinstance(payload, dict) else None
        if for_mode is not None:
            changes = payload.get("changes") if payload.get("kind") == "settings" else None
            if for_mode not in MODE_SOURCES or not isinstance(changes, dict):
                raise HTTPException(422, "Réglages d'un mode : mode ou réglages invalides.")
            if not set(changes) <= DRAFT_FIELDS[for_mode]:
                raise HTTPException(422, "Un de ces réglages n'appartient pas à ce mode.")
            return settings_for_mode(for_mode, changes)
        try:
            command = validate_command(payload)
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

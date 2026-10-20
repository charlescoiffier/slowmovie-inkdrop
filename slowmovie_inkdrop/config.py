"""Réglages (slowmovie.conf) et chemins du projet."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields
from datetime import datetime, time
from pathlib import Path

from .album_url import valid_album_url

log = logging.getLogger(__name__)

MODES = ("slowmovie", "pipiquant", "photos")
SLIDE_INTERVAL = (120, 86400)      # bornes du rythme des diaporamas, en secondes


class ConfigError(ValueError):
    """Réglage invalide ; le message est affichable à l'utilisateur."""


def parse_active_hours(spec: str) -> tuple[time, time] | None:
    spec = spec.strip()
    if not spec:
        return None
    try:
        start_s, end_s = spec.split("-")
        start = datetime.strptime(start_s.strip(), "%H:%M").time()
        end = datetime.strptime(end_s.strip(), "%H:%M").time()
    except ValueError as exc:
        raise ConfigError(f"active-hours doit être de la forme HH:MM-HH:MM (reçu : {spec!r})") from exc
    if start == end:
        raise ConfigError("active-hours : le début et la fin sont identiques")
    return start, end


def in_active_hours(spec: str, when: datetime) -> bool:
    window = parse_active_hours(spec)
    if window is None:
        return True
    start, end = window
    now = when.time()
    if start < end:
        return start <= now < end
    return now >= start or now < end


def _coerce(kind: type, raw: str):
    raw = raw.strip()
    if kind is bool:
        lowered = raw.lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise ValueError(raw)
    return kind(raw)


def read_conf(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


@dataclass
class Settings:
    # Lecture (noms et valeurs par défaut de l'amont)
    file: str = ""            # film initial uniquement ; la TUI utilise la commande `play`
    directory: str = "Videos"
    loop: bool = False
    random_file: bool = False
    random_frames: bool = False
    delay: int = 120
    increment: int = 4
    start: int = 0
    fullscreen: bool = False
    subtitles: bool = False
    timecode: bool = False
    subtitle_track: int = 0     # piste de sous-titres intégrée (parmi tous les flux de sous-titres)
    epd: str = "waveshare_epd.epd7in5_V2"
    contrast: float = 1.0
    clear: bool = False
    loglevel: str = "INFO"
    # Images envoyées
    min_image_seconds: int = 60
    max_image_seconds: int = 3600
    queue_max: int = 10
    queue_expiry_seconds: int = 21600
    caption_margin: int = 40         # px libres sous le texte (cadre de l'écran qui en masque le bas)
    caption_max_chars: int = 128     # texte sous l'image ; le Pi ne fait pas confiance au relais
    active_hours: str = ""    # "08:00-22:00" ; vide = toujours actif
    # Modes de défilement
    mode: str = "slowmovie"          # slowmovie | pipiquant | photos
    pipiquant_interval: int = 600    # secondes entre deux π-piquants
    photos_interval: int = 1800      # secondes entre deux photos
    pipiquant_title: bool = True     # légende sous le π-piquant
    photos_album_url: str = ""       # lien de partage de l'album : un secret, jamais journalisé
    # Relais
    relay_url: str = ""
    relay_token: str = ""
    relay_poll_seconds: int = 20

    def problems(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []

        def check(ok: bool, name: str, message: str) -> None:
            if not ok:
                out.append((name, message))

        check(self.delay >= 1, "delay", "doit être au moins 1")
        check(self.increment >= 1, "increment", "doit être au moins 1")
        check(0 <= self.subtitle_track <= 50, "subtitle_track", "doit être entre 0 et 50")
        check(0.1 <= self.contrast <= 3.0, "contrast", "doit être entre 0.1 et 3.0")
        check(self.min_image_seconds >= 1, "min_image_seconds", "doit être au moins 1")
        check(self.max_image_seconds >= self.min_image_seconds, "max_image_seconds",
              "doit être ≥ min_image_seconds")
        check(self.queue_max >= 1, "queue_max", "doit être au moins 1")
        check(self.queue_expiry_seconds >= 60, "queue_expiry_seconds", "doit être au moins 60")
        check(0 <= self.caption_margin <= 200, "caption_margin", "doit être entre 0 et 200")
        check(1 <= self.caption_max_chars <= 500, "caption_max_chars", "doit être entre 1 et 500")
        check(self.mode in MODES, "mode", "doit être slowmovie, pipiquant ou photos")
        for name in ("pipiquant_interval", "photos_interval"):
            check(SLIDE_INTERVAL[0] <= getattr(self, name) <= SLIDE_INTERVAL[1], name,
                  "doit être entre 120 et 86400")
        check(self.photos_album_url == "" or valid_album_url(self.photos_album_url), "photos_album_url",
              "doit être un lien de partage Google Photos")
        check(self.relay_poll_seconds >= 5, "relay_poll_seconds", "doit être au moins 5")
        check(self.loglevel.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
              "loglevel", "niveau inconnu")
        try:
            parse_active_hours(self.active_hours)
        except ConfigError as exc:
            out.append(("active_hours", str(exc)))
        return out

    @classmethod
    def load(cls, path: Path) -> Settings:
        settings = cls()
        values = read_conf(path)
        for f in fields(cls):
            key = f.name.replace("_", "-")
            if key not in values:
                continue
            kind = type(getattr(settings, f.name))
            try:
                setattr(settings, f.name, _coerce(kind, values[key]))
            except ValueError:
                log.warning("slowmovie.conf : valeur invalide pour %s : %r (ignorée)", key, values[key])
        defaults = cls()
        for name, message in settings.problems():
            log.warning("slowmovie.conf : %s %s (valeur par défaut utilisée)", name, message)
            setattr(settings, name, getattr(defaults, name))
        return settings

    def save(self, path: Path) -> None:
        problems = self.problems()
        if problems:
            raise ConfigError(f"{problems[0][0]} {problems[0][1]}")
        lines = ["# SlowMovie-Inkdrop — écrit par `slowmovie`", ""]
        for f in fields(self):
            value = getattr(self, f.name)
            text = ("true" if value else "false") if isinstance(value, bool) else str(value)
            lines.append(f"{f.name.replace('_', '-')} = {text}")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)  # peut contenir le jeton du relais
        os.replace(tmp, path)


@dataclass(frozen=True)
class Paths:
    base: Path

    @property
    def conf(self) -> Path:
        return self.base / "slowmovie.conf"

    @property
    def state(self) -> Path:
        return self.base / "state.json"

    @property
    def library(self) -> Path:
        return self.base / "library.json"

    @property
    def queue_dir(self) -> Path:
        return self.base / "queue"

    @property
    def socket(self) -> Path:
        override = os.environ.get("SLOWMOVIE_SOCKET")
        return Path(override) if override else self.base / "run" / "slowmovie.sock"

    @property
    def acks(self) -> Path:
        return self.base / "acks.json"

    @property
    def log(self) -> Path:
        return self.base / "slowmovie.log"

    @property
    def progress(self) -> Path:
        return self.base / "progress"

    @property
    def starts(self) -> Path:
        return self.base / "films.json"

    @property
    def commands(self) -> Path:
        return self.base / "commands.json"

    @property
    def now_playing(self) -> Path:
        return self.base / "nowPlaying"

    def videos_dir(self, settings: Settings) -> Path:
        p = Path(settings.directory)
        return p if p.is_absolute() else self.base / p

    def ensure(self, settings: Settings) -> None:
        for d in (self.base, self.progress, self.queue_dir, self.socket.parent,
                  self.videos_dir(settings)):
            d.mkdir(parents=True, exist_ok=True)


def default_base() -> Path:
    return Path(os.environ.get("SLOWMOVIE_HOME", "~/SlowMovie")).expanduser()

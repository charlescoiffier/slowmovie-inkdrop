# slowmovie-inkdrop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire évoluer SlowMovie (film image par image sur e-paper, Raspberry Pi) avec l'envoi d'images à distance via un relais VPS, une TUI Textual au clavier et un ajout de films simplifié.

**Architecture:** Trois blocs. Un *relais* FastAPI sur le VPS (page d'envoi, validation, file) ; un *lecteur* sur le Pi (service systemd, seul propriétaire de l'écran, machine à états `FILM`/`IMAGE`, interroge le relais en connexions sortantes) ; une *TUI/CLI* qui lit `state.json` et envoie des commandes au lecteur par un socket Unix. Tout le matériel est derrière une interface `Display` pour tester sur Mac.

**Tech Stack:** Python ≥ 3.11, Pillow, httpx, ffmpeg-python, omni-epd (Pi seulement), Textual, FastAPI + uvicorn + python-multipart, SQLite (stdlib), pytest, ruff, systemd, Samba, rsync.

**Spec:** `docs/superpowers/specs/2026-10-14-slowmovie-inkdrop-design.md`

## Global Constraints

- Python ≥ 3.11 ; dépôt `slowmovie-inkdrop`, paquet `slowmovie_inkdrop`, commandes `slowmovie` et `slowmovie-relay`.
- Fichiers d'état compatibles avec l'amont : `slowmovie.conf`, `nowPlaying`, `progress/<film>.progress`, `Videos/`.
- Le lecteur est le seul à toucher l'écran ; la TUI et la CLI ne font que lire `state.json` et envoyer des commandes.
- Aucun port ouvert sur le Pi : le Pi n'initie que des connexions sortantes vers le relais.
- Durée d'une image : minimum 60 s, maximum 3600 s (réglables). File : 10 images au plus. Expiration d'une image en attente : 21600 s (6 h).
- Envoi : 10 Mo maximum ; formats JPEG, PNG, WebP seulement ; SVG et images animées refusés ; réencodage Pillow.
- Mot de passe stocké haché (scrypt, stdlib) ; 5 échecs depuis une IP → blocage 900 s ; jamais de mot de passe dans les journaux.
- Jeton d'appareil distinct du mot de passe ; comparaison à temps constant.
- Écran supposé : `waveshare_epd.epd7in5_V2` (800×480). Utilisateur Pi `admin`, dossier `/home/admin/SlowMovie`, nom mDNS `slowmovie.local`.
- Interface et messages utilisateur en français. Thème TUI « galaxy » repris de cadav.
- Plage horaire (`active-hours`) : option désactivée par défaut.
- Licence du SlowMovie amont à confirmer avant toute publication (`legacy/slowmovie.py` en dérive).
- Ne jamais pousser sur GitHub sans demande explicite de l'utilisateur.

## Review Focus

1. **Envois bizarres depuis un téléphone** : JPEG avec rotation EXIF (portrait), HEIC iPhone, GIF animé, fichier tronqué, SVG, image énorme en pixels. Attendu : rotation correcte ou refus avec un message clair en français, jamais un plantage ni un affichage couché. *(Tâches 3 et 9.)*
2. **Redémarrage ou horloge du Pi pendant une image** : le Pi n'a pas d'horloge matérielle, l'heure peut sauter au démarrage. Attendu : l'image reprend jusqu'à l'échéance, mais une échéance absurde (heure erronée) ne bloque jamais l'écran. *(Tâche 7.)*
3. **Dossier de films inhabituel** : dossier vide, film copié à moitié par Samba, fichier corrompu. Attendu : jamais de plantage ni de boucle de redémarrage systemd (l'amont fait `sys.exit` sans film), un message « aucun film prêt », et un fichier incomplet n'est jamais joué. *(Tâche 5 et 7.)*
4. **Saisies du formulaire** : durée vide, `1.5`, négative, `0`, `59`, `3601`, énorme ; file pleine ; mots de passe répétés faux. Attendu : message français précis, code HTTP adapté, pas d'exception. *(Tâche 9.)*
5. **Relais injoignable ou accusé perdu** : relais coupé, accusé « affichée » non reçu, même image livrée deux fois. Attendu : le film continue, l'image n'est affichée qu'une fois, un accusé perdu ne bloque pas la file du relais. *(Tâche 10.)*

---

## File Structure

```
pyproject.toml            .gitignore            README.md
.github/workflows/ci.yml
legacy/                   anciens slowmovie.py, slowmovie_config.py, slowmovie_plus.py
slowmovie_inkdrop/
  __init__.py             version
  config.py               Settings (slowmovie.conf), Paths, plage horaire
  imaging.py              sanitize_upload (relais), to_panel (Pi)
  display.py              Display, FileDisplay, make_display (omni_epd en import paresseux)
  videos.py               extensions, VideoInfo, ffprobe, progression, film suivant, sous-titres
  library.py              Library : films prêts / en attente / erreur, persisté en JSON
  image_queue.py          ImageQueue : file persistée des images à afficher
  framesource.py          FfmpegFrameSource : extraction d'une image de film
  player.py               Player : machine à états, commandes, state.json
  control.py              ControlServer (socket Unix) et send_command
  relayclient.py          RelayClient, AckOutbox, sync_cycle, relay_loop
  runner.py               assemble et lance le service (`slowmovie run`)
  cli.py                  sous-commandes, ssh vers le Pi, rsync
  tui/model.py            lecture d'état, lignes de films, formats
  tui/app.py              application Textual (5 onglets)
  tui/theme.py            thème « galaxy »
  relay/config.py         RelayConfig (variables d'environnement)
  relay/security.py       hash_password, verify_password, RateLimiter
  relay/store.py          Store : SQLite + images sur disque
  relay/app.py            create_app (FastAPI)
  relay/cli.py            hash-password, serve
  relay/static/index.html page d'envoi pour téléphone
deploy/                   slowmovie.service, smb.conf.snippet, install-pi.sh, relay/*
docs/deploiement.md
tests/                    un fichier de test par module, conftest.py
```

---

### Task 1: Dépôt, scaffolding, CI

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`, `slowmovie_inkdrop/__init__.py`, `tests/test_smoke.py`
- Move: `slowmovie.py`, `slowmovie_config.py`, `slowmovie_plus.py` → `legacy/`

**Interfaces:**
- Produces: `slowmovie_inkdrop.__version__ == "0.1.0"` ; environnement `.venv` avec extras `video,tui,relay,dev`.

- [ ] **Step 1: Initialiser git et ranger l'existant**

```bash
cd /Users/charlescoiffier/dev/slowmovie-inkdrop
git init -b main
git remote add origin https://github.com/charlescoiffier/slowmovie-inkdrop.git
mkdir -p legacy slowmovie_inkdrop tests .github/workflows
mv slowmovie.py slowmovie_config.py slowmovie_plus.py legacy/
rm -f .DS_Store
```

- [ ] **Step 2: Écrire `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "slowmovie-inkdrop"
version = "0.1.0"
description = "SlowMovie avec envoi d'images à distance, TUI et ajout de films simplifié"
requires-python = ">=3.11"
dependencies = ["Pillow>=10", "httpx>=0.27"]

[project.optional-dependencies]
video = ["ffmpeg-python>=0.2"]
tui = ["textual>=0.86"]
epd = ["omni-epd"]
relay = ["fastapi>=0.110", "uvicorn>=0.29", "python-multipart>=0.0.9"]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.5"]

[project.scripts]
slowmovie = "slowmovie_inkdrop.cli:main"
slowmovie-relay = "slowmovie_inkdrop.relay.cli:main"

[tool.setuptools.packages.find]
include = ["slowmovie_inkdrop*"]

[tool.setuptools.package-data]
"slowmovie_inkdrop.relay" = ["static/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
exclude = ["legacy", ".venv"]
```

- [ ] **Step 3: Écrire `.gitignore`, `__init__.py`, le test, la CI**

`.gitignore` :
```
.venv/
__pycache__/
*.egg-info/
.DS_Store
.pytest_cache/
.ruff_cache/
Videos/
progress/
nowPlaying
*.log
state.json
library.json
queue/
run/
slowmovie.conf
relay-data/
```

`slowmovie_inkdrop/__init__.py` :
```python
__version__ = "0.1.0"
```

`tests/test_smoke.py` :
```python
import slowmovie_inkdrop


def test_version():
    assert slowmovie_inkdrop.__version__ == "0.1.0"
```

`.github/workflows/ci.yml` :
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: sudo apt-get update && sudo apt-get install -y ffmpeg
      - run: pip install -e '.[video,tui,relay,dev]'
      - run: ruff check .
      - run: pytest -q
```

- [ ] **Step 4: Installer et lancer les tests**

Run: `python3 -m venv .venv && .venv/bin/pip install -e '.[video,tui,relay,dev]' && .venv/bin/pytest -q`
Expected: `1 passed`

- [ ] **Step 5: Commit** (inclut la spécification et ce plan)

```bash
git add -A
git commit -m "chore: scaffolding, CI, ancien code rangé dans legacy/"
```

---

### Task 2: Réglages et chemins (`config.py`)

**Files:**
- Create: `slowmovie_inkdrop/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `ConfigError(ValueError)`
  - `parse_active_hours(spec: str) -> tuple[time, time] | None`
  - `in_active_hours(spec: str, when: datetime) -> bool`
  - `Settings` (dataclass, champs ci-dessous) avec `Settings.load(path: Path) -> Settings`, `.save(path: Path) -> None`, `.problems() -> list[tuple[str, str]]`
  - `Paths(base: Path)` : propriétés `conf, state, library, queue_dir, socket, acks, log, progress, now_playing` ; `videos_dir(settings) -> Path` ; `ensure(settings) -> None`
  - `default_base() -> Path`

- [ ] **Step 1: Écrire les tests**

```python
# tests/test_config.py
from datetime import datetime

import pytest

from slowmovie_inkdrop.config import (
    ConfigError, Paths, Settings, in_active_hours, parse_active_hours,
)


def test_defaults_match_upstream():
    s = Settings()
    assert (s.delay, s.increment, s.contrast) == (120, 4, 1.0)
    assert s.epd == "waveshare_epd.epd7in5_V2"
    assert s.min_image_seconds == 60 and s.max_image_seconds == 3600
    assert s.queue_max == 10 and s.queue_expiry_seconds == 21600
    assert s.active_hours == ""


def test_load_reads_upstream_style_conf(tmp_path):
    conf = tmp_path / "slowmovie.conf"
    conf.write_text("# commentaire\nrandom-frames = true\ndelay=60\nincrement = 6\ncontrast=1.2\nclé-inconnue = 1\n")
    s = Settings.load(conf)
    assert s.random_frames is True and s.delay == 60 and s.increment == 6
    assert s.contrast == 1.2


def test_load_missing_file_gives_defaults(tmp_path):
    assert Settings.load(tmp_path / "absent.conf") == Settings()


def test_load_ignores_invalid_values(tmp_path):
    conf = tmp_path / "slowmovie.conf"
    conf.write_text("delay = abc\nincrement = 0\nactive-hours = n'importe quoi\nloop = peut-être\n")
    s = Settings.load(conf)
    assert s == Settings()


def test_save_roundtrip_and_permissions(tmp_path):
    conf = tmp_path / "slowmovie.conf"
    s = Settings(delay=30, loop=True, active_hours="08:00-22:00", relay_token="secret")
    s.save(conf)
    assert Settings.load(conf) == s
    assert oct(conf.stat().st_mode & 0o777) == "0o600"


def test_save_refuses_invalid(tmp_path):
    with pytest.raises(ConfigError):
        Settings(delay=0).save(tmp_path / "x.conf")


def test_active_hours_same_day():
    assert in_active_hours("08:00-22:00", datetime(2026, 1, 1, 12, 0))
    assert not in_active_hours("08:00-22:00", datetime(2026, 1, 1, 23, 0))
    assert not in_active_hours("08:00-22:00", datetime(2026, 1, 1, 22, 0))


def test_active_hours_wraps_past_midnight():
    assert in_active_hours("22:00-06:00", datetime(2026, 1, 1, 23, 30))
    assert in_active_hours("22:00-06:00", datetime(2026, 1, 1, 2, 0))
    assert not in_active_hours("22:00-06:00", datetime(2026, 1, 1, 12, 0))


def test_active_hours_empty_means_always():
    assert in_active_hours("", datetime(2026, 1, 1, 3, 0))


@pytest.mark.parametrize("spec", ["8h-22h", "08:00", "08:00-08:00", "25:00-26:00"])
def test_active_hours_invalid(spec):
    with pytest.raises(ConfigError):
        parse_active_hours(spec)


def test_paths_ensure(tmp_path):
    paths = Paths(tmp_path)
    settings = Settings()
    paths.ensure(settings)
    assert paths.videos_dir(settings) == tmp_path / "Videos"
    for d in (paths.progress, paths.queue_dir, paths.socket.parent, paths.videos_dir(settings)):
        assert d.is_dir()


def test_videos_dir_absolute(tmp_path):
    assert Paths(tmp_path).videos_dir(Settings(directory="/mnt/films")).as_posix() == "/mnt/films"
```

- [ ] **Step 2: Lancer pour voir l'échec**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: FAIL (`ModuleNotFoundError: slowmovie_inkdrop.config`)

- [ ] **Step 3: Implémenter `config.py`**

```python
"""Réglages (slowmovie.conf) et chemins du projet."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields
from datetime import datetime, time
from pathlib import Path

log = logging.getLogger(__name__)


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
    epd: str = "waveshare_epd.epd7in5_V2"
    contrast: float = 1.0
    clear: bool = False
    loglevel: str = "INFO"
    # Images envoyées
    min_image_seconds: int = 60
    max_image_seconds: int = 3600
    queue_max: int = 10
    queue_expiry_seconds: int = 21600
    active_hours: str = ""    # "08:00-22:00" ; vide = toujours actif
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
        check(0.1 <= self.contrast <= 3.0, "contrast", "doit être entre 0.1 et 3.0")
        check(self.min_image_seconds >= 1, "min_image_seconds", "doit être au moins 1")
        check(self.max_image_seconds >= self.min_image_seconds, "max_image_seconds",
              "doit être ≥ min_image_seconds")
        check(self.queue_max >= 1, "queue_max", "doit être au moins 1")
        check(self.queue_expiry_seconds >= 60, "queue_expiry_seconds", "doit être au moins 60")
        check(self.relay_poll_seconds >= 5, "relay_poll_seconds", "doit être au moins 5")
        check(self.loglevel.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
              "loglevel", "niveau inconnu")
        try:
            parse_active_hours(self.active_hours)
        except ConfigError as exc:
            out.append(("active_hours", str(exc)))
        return out

    @classmethod
    def load(cls, path: Path) -> "Settings":
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
```

- [ ] **Step 4: Lancer les tests**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: tous PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/config.py tests/test_config.py
git commit -m "feat(config): réglages slowmovie.conf compatibles amont, chemins, plage horaire"
```

---

### Task 3: Traitement d'images (`imaging.py`)

**Files:**
- Create: `slowmovie_inkdrop/imaging.py`
- Test: `tests/test_imaging.py`

**Interfaces:**
- Produces:
  - `InvalidImage(ValueError)` avec attributs `.message: str` (français, affichable) et `.code: str` parmi `"too_large" | "format" | "corrupt"`
  - `sanitize_upload(data: bytes, *, max_bytes=10*1024*1024, max_pixels=40_000_000, max_side=2000) -> bytes` (PNG réencodé)
  - `to_panel(img: Image.Image, size: tuple[int, int], *, contrast: float = 1.0) -> Image.Image` (RGB, exactement `size`)

- [ ] **Step 1: Écrire les tests**

```python
# tests/test_imaging.py
import io

import pytest
from PIL import Image

from slowmovie_inkdrop.imaging import InvalidImage, sanitize_upload, to_panel


def _encode(img, fmt, **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def test_jpeg_exif_rotation_is_applied():
    img = Image.new("RGB", (40, 20), (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # tourner de 90°
    out = Image.open(io.BytesIO(sanitize_upload(_encode(img, "JPEG", exif=exif))))
    assert out.size == (20, 40)
    assert out.format == "PNG"


def test_output_has_no_metadata_and_is_downscaled():
    img = Image.new("RGB", (4000, 1000), "white")
    out = Image.open(io.BytesIO(sanitize_upload(_encode(img, "PNG"))))
    assert max(out.size) == 2000
    assert not out.info.get("exif")


def test_transparent_png_is_flattened_on_white():
    img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    out = Image.open(io.BytesIO(sanitize_upload(_encode(img, "PNG")))).convert("RGB")
    assert out.getpixel((5, 5)) == (255, 255, 255)


def test_animated_gif_rejected():
    frames = [Image.new("RGB", (8, 8), c) for c in ("red", "blue")]
    data = _encode(frames[0], "GIF", save_all=True, append_images=frames[1:])
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(data)
    assert exc.value.code == "format"


def test_svg_rejected():
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    assert exc.value.code in ("format", "corrupt")


def test_truncated_file_rejected():
    data = _encode(Image.new("RGB", (200, 200), "red"), "PNG")
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(data[:60])
    assert exc.value.code == "corrupt"


def test_too_many_bytes_rejected():
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(b"x" * 11, max_bytes=10)
    assert exc.value.code == "too_large"


def test_too_many_pixels_rejected():
    data = _encode(Image.new("RGB", (100, 100), "white"), "PNG")
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(data, max_pixels=1000)
    assert exc.value.code == "too_large"


def test_messages_are_french_and_safe():
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(b"n'importe quoi")
    assert "Traceback" not in exc.value.message and exc.value.message


def test_to_panel_pads_portrait_to_exact_size():
    out = to_panel(Image.new("RGB", (100, 400), "black"), (800, 480))
    assert out.size == (800, 480) and out.mode == "RGB"
    assert out.getpixel((0, 240)) == (255, 255, 255)  # bandes blanches à gauche


def test_to_panel_contrast_changes_pixels():
    base = Image.new("RGB", (800, 480), (100, 100, 100))
    base.paste((200, 200, 200), (400, 0, 800, 480))        # image non uniforme
    assert to_panel(base, (800, 480), contrast=2.0).getpixel((5, 5)) != (100, 100, 100)
```

- [ ] **Step 2: Lancer pour voir l'échec**

Run: `.venv/bin/pytest tests/test_imaging.py -q` — Expected: FAIL (module absent)

- [ ] **Step 3: Implémenter**

```python
"""Traitement des images : validation côté relais, mise en forme côté écran."""
from __future__ import annotations

import io

from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class InvalidImage(ValueError):
    """Image refusée. `message` peut être montré tel quel à l'expéditeur."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code  # "too_large" | "format" | "corrupt"


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return img.convert("RGB")


def sanitize_upload(data: bytes, *, max_bytes: int = 10 * 1024 * 1024,
                    max_pixels: int = 40_000_000, max_side: int = 2000) -> bytes:
    if len(data) > max_bytes:
        raise InvalidImage(f"Image trop lourde ({max_bytes // (1024 * 1024)} Mo maximum).", "too_large")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise InvalidImage("Format non pris en charge : utilisez JPEG, PNG ou WebP.", "format")
            if getattr(probe, "n_frames", 1) > 1 or getattr(probe, "is_animated", False):
                raise InvalidImage("Les images animées ne sont pas acceptées.", "format")
            if probe.width * probe.height > max_pixels:
                raise InvalidImage("Image trop grande (trop de pixels).", "too_large")
            probe.load()
            img = ImageOps.exif_transpose(probe)
            img = _flatten(img)
    except InvalidImage:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise InvalidImage("Fichier illisible ou corrompu.", "corrupt") from None
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def to_panel(img: Image.Image, size: tuple[int, int], *, contrast: float = 1.0) -> Image.Image:
    img = _flatten(img)
    img = ImageOps.pad(img, size, method=Image.LANCZOS, color=(255, 255, 255))
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    return img
```

- [ ] **Step 4: Lancer les tests**

Run: `.venv/bin/pytest tests/test_imaging.py -q` — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/imaging.py tests/test_imaging.py
git commit -m "feat(imaging): validation des envois et mise en forme pour l'écran"
```

---

### Task 4: Abstraction de l'écran (`display.py`)

**Files:**
- Create: `slowmovie_inkdrop/display.py`
- Test: `tests/test_display.py`

**Interfaces:**
- Produces:
  - `Display` (Protocol) : attributs `width: int`, `height: int` ; méthodes `prepare()`, `display(image)`, `sleep()`, `clear()`, `close()`
  - `FileDisplay(directory: Path, width=800, height=480)` : écrit `last.png`, garde `events: list[str]` (`"prepare" | "display" | "sleep" | "clear" | "close"`) et `shown: list[Image]`
  - `make_display(epd_name: str, fake_dir: Path | None = None) -> Display` : si `fake_dir` ou variable `SLOWMOVIE_FAKE_DISPLAY`, renvoie un `FileDisplay` ; sinon charge `omni_epd.displayfactory`

- [ ] **Step 1: Écrire les tests**

```python
# tests/test_display.py
from PIL import Image

from slowmovie_inkdrop.display import FileDisplay, make_display


def test_file_display_records_events_and_writes_png(tmp_path):
    d = FileDisplay(tmp_path)
    d.prepare()
    d.display(Image.new("RGB", (800, 480), "red"))
    d.sleep()
    assert d.events == ["prepare", "display", "sleep"]
    assert (tmp_path / "last.png").exists()
    assert (d.width, d.height) == (800, 480)


def test_make_display_uses_fake_dir(tmp_path):
    d = make_display("waveshare_epd.epd7in5_V2", fake_dir=tmp_path)
    assert isinstance(d, FileDisplay)


def test_make_display_uses_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWMOVIE_FAKE_DISPLAY", str(tmp_path))
    assert isinstance(make_display("peu importe"), FileDisplay)
```

- [ ] **Step 2: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_display.py -q` → FAIL

- [ ] **Step 3: Implémenter**

```python
"""Pilote d'écran : vrai e-paper (omni_epd) sur le Pi, fichier PNG ailleurs."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from PIL import Image


class Display(Protocol):
    width: int
    height: int

    def prepare(self) -> None: ...
    def display(self, image: Image.Image) -> None: ...
    def sleep(self) -> None: ...
    def clear(self) -> None: ...
    def close(self) -> None: ...


class FileDisplay:
    """Écran factice : écrit la dernière image dans `last.png` et journalise les appels."""

    def __init__(self, directory: Path, width: int = 800, height: int = 480):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.width, self.height = width, height
        self.events: list[str] = []
        self.shown: list[Image.Image] = []

    def prepare(self) -> None:
        self.events.append("prepare")

    def display(self, image: Image.Image) -> None:
        self.events.append("display")
        self.shown = (self.shown + [image.copy()])[-50:]
        image.save(self.directory / "last.png")

    def sleep(self) -> None:
        self.events.append("sleep")

    def clear(self) -> None:
        self.events.append("clear")

    def close(self) -> None:
        self.events.append("close")


def make_display(epd_name: str, fake_dir: Path | None = None) -> Display:
    fake = fake_dir or os.environ.get("SLOWMOVIE_FAKE_DISPLAY")
    if fake:
        return FileDisplay(Path(fake))
    from omni_epd import EPDNotFoundError, displayfactory  # import paresseux : Pi seulement

    try:
        return displayfactory.load_display_driver(epd_name)
    except EPDNotFoundError as exc:
        valid = "\n".join(map(str, displayfactory.list_supported_displays()))
        raise SystemExit(f"Écran inconnu : {epd_name!r}. Écrans pris en charge :\n{valid}") from exc
```

- [ ] **Step 4: Lancer les tests** — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/display.py tests/test_display.py
git commit -m "feat(display): interface d'écran et écran factice pour les tests"
```

---

### Task 5: Films et bibliothèque (`videos.py`, `library.py`)

**Files:**
- Create: `slowmovie_inkdrop/videos.py`, `slowmovie_inkdrop/library.py`
- Test: `tests/test_videos.py`, `tests/test_library.py`

**Interfaces:**
- Produces (`videos.py`):
  - `FILE_TYPES = (".avi", ".mp4", ".m4v", ".mkv", ".mov")`, `is_supported(name: str) -> bool`
  - `VideoInfo(fps: float, duration: float, frame_count: int, width: int, height: int)` frozen, propriétés `frame_time_ms`, `aspect_ratio`
  - `parse_probe(probe: dict) -> VideoInfo` (lève `ValueError` en français si inexploitable) ; `probe_video(path: Path) -> VideoInfo`
  - `read_progress(progress_dir: Path, video: Path) -> int` ; `write_progress(progress_dir: Path, video: Path, frame: int) -> None`
  - `next_video(videos: Sequence[Path], current: Path | None) -> Path | None`
  - `find_subtitles(video: Path) -> Path | None`
- Produces (`library.py`): `Library(videos_dir, json_path, probe, settle_seconds=10.0)` avec `.poll(now: float)`, `.ready() -> list[Path]` (triée), `.rows() -> list[dict]` (clés `name, size, status, changed_at, frames, duration, error`).

- [ ] **Step 1: Écrire les tests de `videos`**

```python
# tests/test_videos.py
from pathlib import Path

import pytest

from slowmovie_inkdrop.videos import (
    VideoInfo, find_subtitles, is_supported, next_video, parse_probe, read_progress,
    write_progress,
)

MP4 = {"streams": [{"avg_frame_rate": "24000/1001", "nb_frames": "2400", "width": 1920, "height": 1080}],
       "format": {"duration": "100.1"}}
MKV = {"streams": [{"avg_frame_rate": "25/1", "width": 1280, "height": 720}],
       "format": {"duration": "60"}}


def test_parse_probe_mp4_uses_nb_frames():
    info = parse_probe(MP4)
    assert info.frame_count == 2400 and round(info.fps, 3) == 23.976
    assert round(info.frame_time_ms, 1) == 41.7 and round(info.aspect_ratio, 2) == 1.78


def test_parse_probe_mkv_computes_frames():
    assert parse_probe(MKV).frame_count == 1500


@pytest.mark.parametrize("bad", [
    {"streams": [], "format": {"duration": "1"}},
    {"streams": [{"avg_frame_rate": "0/0", "width": 1, "height": 1}], "format": {"duration": "1"}},
    {"streams": [{"avg_frame_rate": "25/1", "width": 1, "height": 1}], "format": {}},
])
def test_parse_probe_rejects_unusable(bad):
    with pytest.raises(ValueError):
        parse_probe(bad)


def test_is_supported():
    assert is_supported("film.MP4") and is_supported("a.mkv")
    assert not is_supported(".caché.mp4") and not is_supported("notes.txt")


def test_progress_roundtrip_and_garbage(tmp_path):
    video = Path("/x/film.mp4")
    assert read_progress(tmp_path, video) == 0
    write_progress(tmp_path, video, 128)
    assert read_progress(tmp_path, video) == 128
    (tmp_path / "film.mp4.progress").write_text("abc")
    assert read_progress(tmp_path, video) == 0


def test_next_video_wraps():
    vids = [Path("/v/a.mp4"), Path("/v/b.mp4")]
    assert next_video(vids, None) == vids[0]
    assert next_video(vids, vids[0]) == vids[1]
    assert next_video(vids, vids[1]) == vids[0]
    assert next_video([], None) is None


def test_find_subtitles(tmp_path):
    (tmp_path / "f.mp4").touch()
    (tmp_path / "f.srt").touch()
    assert find_subtitles(tmp_path / "f.mp4") == tmp_path / "f.srt"
    assert find_subtitles(tmp_path / "g.mp4") is None
```

- [ ] **Step 2: Écrire les tests de `library`**

```python
# tests/test_library.py
import json

from slowmovie_inkdrop.library import Library
from slowmovie_inkdrop.videos import VideoInfo

INFO = VideoInfo(24.0, 100.0, 2400, 1920, 1080)


def make(tmp_path, probe=lambda p: INFO):
    vdir = tmp_path / "Videos"
    vdir.mkdir(exist_ok=True)
    return vdir, Library(vdir, tmp_path / "library.json", probe, settle_seconds=10)


def test_empty_directory_has_no_ready_videos(tmp_path):
    _, lib = make(tmp_path)
    lib.poll(0)
    assert lib.ready() == []


def test_new_file_needs_to_settle(tmp_path):
    vdir, lib = make(tmp_path)
    (vdir / "a.mp4").write_bytes(b"x" * 10)
    lib.poll(0)
    assert lib.ready() == []
    lib.poll(5)
    assert lib.ready() == []
    lib.poll(11)
    assert lib.ready() == [vdir / "a.mp4"]


def test_growing_file_is_never_ready(tmp_path):
    vdir, lib = make(tmp_path)
    f = vdir / "a.mp4"
    f.write_bytes(b"x" * 10)
    lib.poll(0)
    f.write_bytes(b"x" * 20)   # la copie Samba continue
    lib.poll(11)
    assert lib.ready() == []
    lib.poll(22)               # taille stable depuis 11 s
    assert lib.ready() == [f]


def test_corrupt_file_becomes_error_not_ready(tmp_path):
    def boom(path):
        raise ValueError("illisible")

    vdir, lib = make(tmp_path, probe=boom)
    (vdir / "a.mp4").write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    assert lib.ready() == []
    [row] = lib.rows()
    assert row["status"] == "error" and "illisible" in row["error"]


def test_hidden_and_unsupported_files_ignored(tmp_path):
    vdir, lib = make(tmp_path)
    (vdir / ".a.mp4").write_bytes(b"x")
    (vdir / "notes.txt").write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    assert lib.rows() == []


def test_removed_file_disappears(tmp_path):
    vdir, lib = make(tmp_path)
    f = vdir / "a.mp4"
    f.write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    f.unlink()
    lib.poll(12)
    assert lib.ready() == []


def test_known_ready_file_survives_restart_without_waiting(tmp_path):
    vdir, lib = make(tmp_path)
    (vdir / "a.mp4").write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    _, lib2 = make(tmp_path)
    lib2.poll(1000)
    assert lib2.ready() == [vdir / "a.mp4"]
    assert json.loads((tmp_path / "library.json").read_text())["a.mp4"]["status"] == "ready"
```

- [ ] **Step 3: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_videos.py tests/test_library.py -q` → FAIL

- [ ] **Step 4: Implémenter `videos.py`**

```python
"""Films : extensions, métadonnées ffprobe, progression, film suivant."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Sequence

FILE_TYPES = (".avi", ".mp4", ".m4v", ".mkv", ".mov")
SUBTITLE_TYPES = (".srt", ".ssa", ".ass")


def is_supported(name: str) -> bool:
    return Path(name).suffix.lower() in FILE_TYPES and not name.startswith(".")


@dataclass(frozen=True)
class VideoInfo:
    fps: float
    duration: float
    frame_count: int
    width: int
    height: int

    @property
    def frame_time_ms(self) -> float:
        return 1000 / self.fps

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height


def parse_probe(probe: dict) -> VideoInfo:
    try:
        streams = probe["streams"]
        if not streams:
            raise ValueError("aucun flux vidéo")
        stream = streams[0]
        fps = float(Fraction(stream["avg_frame_rate"]))
        if fps <= 0:
            raise ValueError("fréquence d'images invalide")
        duration = float(probe["format"]["duration"])
        try:
            frames = int(stream["nb_frames"])
        except (KeyError, ValueError):
            frames = int(duration * fps)
        if frames < 1:
            raise ValueError("film vide")
        return VideoInfo(fps, duration, frames, int(stream["width"]), int(stream["height"]))
    except (KeyError, TypeError, ZeroDivisionError) as exc:
        raise ValueError(f"métadonnées vidéo inexploitables ({exc!r})") from exc


def probe_video(path: Path) -> VideoInfo:
    import ffmpeg  # import paresseux

    return parse_probe(ffmpeg.probe(str(path), select_streams="v"))


def _progress_file(progress_dir: Path, video: Path) -> Path:
    return Path(progress_dir) / f"{video.name}.progress"


def read_progress(progress_dir: Path, video: Path) -> int:
    try:
        return max(int(_progress_file(progress_dir, video).read_text().strip()), 0)
    except (OSError, ValueError):
        return 0


def write_progress(progress_dir: Path, video: Path, frame: int) -> None:
    _progress_file(progress_dir, video).write_text(str(int(frame)))


def next_video(videos: Sequence[Path], current: Path | None) -> Path | None:
    ordered = sorted(videos, key=lambda p: p.name)
    if not ordered:
        return None
    if current is not None:
        names = [p.name for p in ordered]
        if current.name in names:
            index = names.index(current.name) + 1
            if index < len(ordered):
                return ordered[index]
    return ordered[0]


def find_subtitles(video: Path) -> Path | None:
    for candidate in sorted(video.parent.glob(video.stem + ".*")):
        if candidate.suffix.lower() in SUBTITLE_TYPES:
            return candidate
    return None
```

- [ ] **Step 5: Implémenter `library.py`**

```python
"""Bibliothèque de films : n'expose que les fichiers complets et lisibles."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

from .videos import VideoInfo, is_supported


class Library:
    def __init__(self, videos_dir: Path, json_path: Path,
                 probe: Callable[[Path], VideoInfo], settle_seconds: float = 10.0):
        self.videos_dir = Path(videos_dir)
        self.json_path = Path(json_path)
        self.probe = probe
        self.settle = settle_seconds
        self._lock = threading.RLock()
        self._entries: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.json_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self.json_path.with_name(self.json_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self._entries))
            os.replace(tmp, self.json_path)
        except OSError:
            pass

    def poll(self, now: float) -> None:
        with self._lock:
            current: dict[str, int] = {}
            try:
                children = list(self.videos_dir.iterdir())
            except OSError:
                children = []
            for p in children:
                if is_supported(p.name) and p.is_file():
                    try:
                        current[p.name] = p.stat().st_size
                    except OSError:
                        continue
            for name in [n for n in self._entries if n not in current]:
                del self._entries[name]
            for name, size in current.items():
                entry = self._entries.get(name)
                if entry is None or entry["size"] != size:
                    self._entries[name] = {"size": size, "status": "pending", "changed_at": now}
                    continue
                if entry["status"] == "pending" and now - entry["changed_at"] >= self.settle:
                    try:
                        info = self.probe(self.videos_dir / name)
                    except Exception as exc:  # ffprobe : tout échec = film inutilisable
                        entry.update(status="error", error=str(exc)[:200])
                    else:
                        entry.update(status="ready", frames=info.frame_count, duration=info.duration)
            self._save()

    def ready(self) -> list[Path]:
        with self._lock:
            return sorted(self.videos_dir / n for n, e in self._entries.items()
                          if e["status"] == "ready")

    def rows(self) -> list[dict]:
        with self._lock:
            return [{"name": n, **e} for n, e in sorted(self._entries.items())]
```

- [ ] **Step 6: Lancer les tests** — `.venv/bin/pytest tests/test_videos.py tests/test_library.py -q` → PASS

- [ ] **Step 7: Commit**

```bash
git add slowmovie_inkdrop/videos.py slowmovie_inkdrop/library.py tests/test_videos.py tests/test_library.py
git commit -m "feat(videos): métadonnées, progression et bibliothèque qui ignore les fichiers incomplets"
```

---

### Task 6: File d'images (`image_queue.py`)

**Files:**
- Create: `slowmovie_inkdrop/image_queue.py`
- Test: `tests/test_image_queue.py`

**Interfaces:**
- Produces:
  - `QueuedImage(id: str, path: Path, duration: int, received_at: float)` frozen
  - `QueueFull(Exception)`
  - `ImageQueue(directory: Path, max_items: int = 10, expiry_seconds: float = 21600)` ; attributs publics `max_items`, `expiry_seconds` modifiables
  - `.items() -> list[QueuedImage]` (plus ancien d'abord) ; `len(q)`
  - `.known(id: str) -> bool` (dans la file **ou** déjà vu)
  - `.add(id: str, data: bytes, duration: int, now: float) -> QueuedImage` (lève `QueueFull`, `ValueError` si `id` n'est pas 32 caractères hexadécimaux)
  - `.peek(now: float) -> QueuedImage | None` (supprime les images expirées)
  - `.discard(item: QueuedImage) -> None` (supprime les fichiers, mémorise l'id comme « vu », 200 derniers)
  - `.remove(id: str) -> QueuedImage | None` ; `.clear() -> list[QueuedImage]` (comme `discard`, renvoient ce qu'ils ont retiré)

- [ ] **Step 1: Écrire les tests**

```python
# tests/test_image_queue.py
import pytest

from slowmovie_inkdrop.image_queue import ImageQueue, QueueFull

A, B, C = "a" * 32, "b" * 32, "c" * 32


def test_fifo_order(tmp_path):
    q = ImageQueue(tmp_path, max_items=5)
    q.add(B, b"2", 60, now=20)
    q.add(A, b"1", 60, now=10)
    assert [i.id for i in q.items()] == [A, B]
    assert q.peek(now=30).id == A


def test_full_queue_raises(tmp_path):
    q = ImageQueue(tmp_path, max_items=1)
    q.add(A, b"1", 60, now=0)
    with pytest.raises(QueueFull):
        q.add(B, b"2", 60, now=1)


def test_expired_images_are_dropped(tmp_path):
    q = ImageQueue(tmp_path, expiry_seconds=100)
    q.add(A, b"1", 60, now=0)
    q.add(B, b"2", 60, now=90)
    assert q.peek(now=150).id == B
    assert len(q) == 1


def test_persists_across_instances(tmp_path):
    ImageQueue(tmp_path).add(A, b"1", 60, now=0)
    assert [i.id for i in ImageQueue(tmp_path).items()] == [A]


def test_discard_removes_files_and_remembers_id(tmp_path):
    q = ImageQueue(tmp_path)
    item = q.add(A, b"1", 60, now=0)
    q.discard(item)
    assert not item.path.exists() and len(q) == 0
    assert q.known(A)
    assert q.known(A) and ImageQueue(tmp_path).known(A)  # mémorisé sur disque


def test_known_covers_queued_items(tmp_path):
    q = ImageQueue(tmp_path)
    q.add(A, b"1", 60, now=0)
    assert q.known(A) and not q.known(B)


@pytest.mark.parametrize("bad", ["../evil", "A" * 32, "abc", "", "g" * 32])
def test_rejects_unsafe_ids(tmp_path, bad):
    with pytest.raises(ValueError):
        ImageQueue(tmp_path).add(bad, b"1", 60, now=0)


def test_remove_and_clear(tmp_path):
    q = ImageQueue(tmp_path)
    q.add(A, b"1", 60, now=0)
    q.add(B, b"2", 60, now=1)
    q.add(C, b"3", 60, now=2)
    assert q.remove(B).id == B and q.remove(B) is None
    assert [i.id for i in q.clear()] == [A, C]
    assert len(q) == 0


def test_corrupt_metadata_is_ignored(tmp_path):
    q = ImageQueue(tmp_path)
    (tmp_path / f"{A}.json").write_text("{pas du json")
    assert q.items() == []
```

- [ ] **Step 2: Lancer pour voir l'échec** — FAIL

- [ ] **Step 3: Implémenter**

```python
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
```

- [ ] **Step 4: Lancer les tests** — PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/image_queue.py tests/test_image_queue.py
git commit -m "feat(queue): file d'images persistée, expiration, ids sûrs"
```

---

### Task 7: Machine à états du lecteur (`player.py`)

**Files:**
- Create: `slowmovie_inkdrop/player.py`, `tests/conftest.py`
- Test: `tests/test_player.py`

**Interfaces:**
- Consumes: `Settings`, `Paths`, `in_active_hours` (T2) ; `to_panel` (T3) ; `Display` (T4) ; `VideoInfo, next_video, probe_video, read_progress, write_progress` (T5) ; `Library` avec `.ready()` (T5) ; `ImageQueue, QueuedImage` (T6).
- Produces:
  - `FrameSource` (Protocol) : `frame(video: Path, info: VideoInfo, frame: int, settings: Settings, size: tuple[int, int]) -> Image`
  - `Player(*, paths, settings, display, frames, image_queue, library, clock=time.time, rng=None, probe=probe_video, on_done=None)` où `on_done: Callable[[QueuedImage, str], None]` reçoit `"shown"` ou `"failed"`
  - `.tick() -> float` (secondes à attendre avant le prochain appel, ≤ 5) ; `.run_forever(stop: threading.Event)` ; `.submit(cmd: dict) -> Future[dict]` ; `.snapshot(now) -> dict` ; attributs `mode ("FILM"|"IMAGE")`, `paused`, `video`, `frame`, `error`, `relay_status`, `settings`, `wake: threading.Event`
  - Commandes : `pause`, `resume`, `skip`, `play` (`video`: nom), `reload`, `reset` (`video`: nom), `queue_remove` (`id`), `queue_clear`. Réponse : `{"ok": True}` ou `{"ok": False, "error": "..."}`
  - `state.json` : `mode, paused, video, frame, frame_count, percent, remaining_seconds, next_frame_at, image_until, active, queue[{id,duration}], error, relay_status, updated_at, delay, increment`

- [ ] **Step 1: Écrire `tests/conftest.py`**

```python
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from slowmovie_inkdrop.config import Paths, Settings
from slowmovie_inkdrop.display import FileDisplay
from slowmovie_inkdrop.image_queue import ImageQueue
from slowmovie_inkdrop.player import Player
from slowmovie_inkdrop.videos import VideoInfo

INFO = VideoInfo(fps=24.0, duration=100.0, frame_count=1000, width=1920, height=1080)


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeFrames:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []
        self.fail_on: set[int] = set()

    def frame(self, video, info, frame, settings, size):
        self.calls.append((video.name, frame))
        if frame in self.fail_on:
            raise RuntimeError("ffmpeg a échoué")
        return Image.new("RGB", size, (frame % 256, 0, 0))


class FakeLibrary:
    def __init__(self, videos):
        self.videos = list(videos)

    def ready(self):
        return list(self.videos)


def png_bytes(size=(60, 40), color="blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def make_player(tmp_path):
    def _make(*, videos=("a.mp4",), clock=None, base=None, **settings_kw):
        base = Path(base or tmp_path)
        settings = Settings(**settings_kw)
        paths = Paths(base)
        paths.ensure(settings)
        clock = clock or FakeClock()
        display = FileDisplay(base / "display")
        frames = FakeFrames()
        queue = ImageQueue(paths.queue_dir, settings.queue_max, settings.queue_expiry_seconds)
        library = FakeLibrary([paths.videos_dir(settings) / v for v in videos])
        done: list[tuple[str, str]] = []
        player = Player(paths=paths, settings=settings, display=display, frames=frames,
                        image_queue=queue, library=library, clock=clock,
                        probe=lambda p: INFO, on_done=lambda item, status: done.append((item.id, status)))
        return SimpleNamespace(player=player, clock=clock, display=display, frames=frames,
                               queue=queue, paths=paths, library=library, done=done)
    return _make
```

- [ ] **Step 2: Écrire `tests/test_player.py`**

```python
import json
from datetime import datetime

from slowmovie_inkdrop.videos import read_progress, write_progress

from conftest import png_bytes

A, B = "a" * 32, "b" * 32


def test_film_advances_and_saves_progress(make_player):
    ctx = make_player()
    sleep = ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 0)]
    assert ctx.display.events == ["prepare", "display", "sleep"]
    assert read_progress(ctx.paths.progress, ctx.library.videos[0]) == 4
    assert 0 < sleep <= 5
    ctx.player.tick()
    assert len(ctx.frames.calls) == 1            # pas avant le délai
    ctx.clock.advance(120)
    ctx.player.tick()
    assert ctx.frames.calls[-1] == ("a.mp4", 4)


def test_resumes_from_progress_file(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 500)
    ctx = make_player()
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 500)]


def test_image_preempts_film_then_film_resumes_where_it_stopped(make_player):
    ctx = make_player()
    ctx.player.tick()                             # image 0 affichée, prochaine = 4
    ctx.queue.add(A, png_bytes(), 300, ctx.clock())
    ctx.clock.advance(1)
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE"
    assert ctx.done == [(A, "shown")]
    refreshes = ctx.display.events.count("display")
    ctx.clock.advance(299)
    ctx.player.tick()
    assert ctx.display.events.count("display") == refreshes   # un seul rafraîchissement
    ctx.clock.advance(2)
    ctx.player.tick()
    assert ctx.player.mode == "FILM"
    assert ctx.frames.calls[-1] == ("a.mp4", 4)


def test_queue_is_played_in_order_before_film_resumes(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(color="red"), 60, 0)
    ctx.queue.add(B, png_bytes(color="green"), 120, 1)
    ctx.player.tick()
    assert ctx.frames.calls == []
    ctx.clock.advance(61)
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE" and ctx.frames.calls == []
    assert [d[0] for d in ctx.done] == [A, B]
    ctx.clock.advance(121)
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 0)]


def test_duration_is_clamped(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 5, ctx.clock())
    ctx.player.tick()
    assert ctx.player.image_until - ctx.clock() == 60
    ctx2 = make_player(base=ctx.paths.base / "other")
    ctx2.queue.add(B, png_bytes(), 99999, ctx2.clock())
    ctx2.player.tick()
    assert ctx2.player.image_until - ctx2.clock() == 3600


def test_restart_during_image_resumes_until_deadline(make_player, tmp_path):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 300, ctx.clock())
    ctx.player.tick()
    again = make_player(clock=ctx.clock)
    assert again.player.mode == "IMAGE"
    again.player.tick()
    assert again.display.events == []             # pas de nouveau rafraîchissement
    ctx.clock.advance(301)
    again.player.tick()
    assert again.player.mode == "FILM"


def test_absurd_deadline_after_clock_jump_is_ignored(make_player, tmp_path):
    ctx = make_player()
    ctx.paths.state.write_text(json.dumps({"mode": "IMAGE", "image_until": ctx.clock() + 10**9}))
    again = make_player(clock=ctx.clock)
    assert again.player.mode == "FILM"


def test_clock_jump_while_showing_image_does_not_freeze_screen(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 60, ctx.clock())
    ctx.player.tick()
    ctx.player.image_until = ctx.clock() + 10**9       # l'heure a sauté en arrière
    ctx.player.tick()
    assert ctx.player.image_until - ctx.clock() <= 3600


def test_paused_player_does_nothing(make_player):
    ctx = make_player()
    assert ctx.player.submit({"cmd": "pause"}) is not None
    ctx.player.tick()
    assert ctx.display.events == []
    ctx.player.submit({"cmd": "resume"})
    ctx.player.tick()
    assert ctx.display.events.count("display") == 1


def test_no_videos_does_not_crash(make_player):
    ctx = make_player(videos=())
    sleep = ctx.player.tick()
    assert 0 < sleep <= 5
    state = json.loads(ctx.paths.state.read_text())
    assert "Aucun film" in state["error"] and state["video"] is None
    ctx.library.videos = [ctx.paths.videos_dir(ctx.player.settings) / "a.mp4"]
    ctx.clock.advance(31)
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 0)]


def test_images_still_shown_when_no_video(make_player):
    ctx = make_player(videos=())
    ctx.queue.add(A, png_bytes(), 60, ctx.clock())
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE"


def test_frame_extraction_failure_moves_on(make_player):
    ctx = make_player()
    ctx.frames.fail_on = {0}
    ctx.player.tick()
    assert ctx.display.events == []
    assert "ffmpeg" in ctx.player.error
    ctx.clock.advance(11)
    ctx.player.tick()
    assert ctx.frames.calls[-1] == ("a.mp4", 4) and ctx.player.error == ""


def test_unreadable_queued_image_is_discarded_and_reported(make_player):
    ctx = make_player()
    ctx.queue.add(A, b"pas une image", 60, ctx.clock())
    ctx.player.tick()
    assert ctx.done == [(A, "failed")] and len(ctx.queue) == 0
    assert ctx.player.mode == "FILM"


def test_outside_active_hours_nothing_is_drawn(make_player):
    ctx = make_player(active_hours="08:00-22:00")
    ctx.clock.t = datetime(2026, 1, 1, 23, 30).timestamp()
    ctx.player.tick()
    assert ctx.display.events == []
    ctx.clock.t = datetime(2026, 1, 2, 9, 0).timestamp()
    ctx.player.tick()
    assert ctx.display.events.count("display") == 1


def test_end_of_video_moves_to_next_and_writes_now_playing(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 998)
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 998)]
    assert ctx.paths.now_playing.read_text().endswith("b.mp4")
    assert read_progress(ctx.paths.progress, tmp_path / "Videos" / "b.mp4") == 0


def test_skip_ends_current_image(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 600, ctx.clock())
    ctx.player.tick()
    ctx.player.submit({"cmd": "skip"})
    ctx.player.tick()
    assert ctx.player.mode == "FILM"


def test_play_command_switches_video(make_player):
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.tick()
    fut = ctx.player.submit({"cmd": "play", "video": "b.mp4"})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.frames.calls[-1][0] == "b.mp4"


def test_play_unknown_video_is_refused(make_player):
    ctx = make_player()
    fut = ctx.player.submit({"cmd": "play", "video": "inconnu.mp4"})
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False


def test_reload_applies_new_settings(make_player):
    ctx = make_player()
    conf = ctx.paths.conf
    conf.write_text("delay = 10\nqueue-max = 3\n")
    fut = ctx.player.submit({"cmd": "reload"})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.player.settings.delay == 10 and ctx.queue.max_items == 3


def test_queue_remove_and_clear_report_failed(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 60, 0)
    ctx.queue.add(B, png_bytes(), 60, 1)
    ctx.player.submit({"cmd": "queue_remove", "id": A})
    ctx.player.submit({"cmd": "queue_clear"})
    ctx.player.tick()
    assert ctx.done == [(A, "failed"), (B, "failed")]
    assert len(ctx.queue) == 0 and ctx.player.mode == "FILM"


def test_unknown_command_is_refused(make_player):
    ctx = make_player()
    fut = ctx.player.submit({"cmd": "dance"})
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False


def test_state_snapshot_fields(make_player):
    ctx = make_player()
    ctx.queue.add(A, png_bytes(), 60, ctx.clock())
    ctx.player.tick()
    ctx.queue.add(B, png_bytes(), 90, ctx.clock())
    ctx.player.tick()
    state = json.loads(ctx.paths.state.read_text())
    assert state["mode"] == "IMAGE" and state["queue"] == [{"id": B, "duration": 90}]
    for key in ("paused", "video", "frame", "frame_count", "percent", "remaining_seconds",
                "next_frame_at", "image_until", "active", "error", "relay_status", "updated_at"):
        assert key in state
```

- [ ] **Step 3: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_player.py -q` → FAIL (module `player` absent)

- [ ] **Step 4: Implémenter `player.py`**

```python
"""Machine à états du lecteur : FILM <-> IMAGE."""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Callable, Protocol

from PIL import Image

from .config import Paths, Settings, in_active_hours
from .display import Display
from .image_queue import ImageQueue, QueuedImage
from .imaging import to_panel
from .videos import VideoInfo, next_video, probe_video, read_progress, write_progress

log = logging.getLogger(__name__)

TICK_MAX = 5.0      # on se réveille au moins toutes les 5 s (commandes, file, état)
IDLE_TICK = 30.0    # sans film ou hors plage horaire
RETRY_SECONDS = 10.0


class FrameSource(Protocol):
    def frame(self, video: Path, info: VideoInfo, frame: int, settings: Settings,
              size: tuple[int, int]) -> Image.Image: ...


def _clamp(n: float, low: float, high: float) -> float:
    return max(low, min(n, high))


class Player:
    def __init__(self, *, paths: Paths, settings: Settings, display: Display, frames: FrameSource,
                 image_queue: ImageQueue, library, clock: Callable[[], float] = time.time,
                 rng: random.Random | None = None, probe=probe_video,
                 on_done: Callable[[QueuedImage, str], None] | None = None):
        self.paths, self.settings, self.display = paths, settings, display
        self.frames, self.queue, self.library = frames, image_queue, library
        self.clock, self.rng, self.probe, self.on_done = clock, rng or random.Random(), probe, on_done
        self.mode = "FILM"
        self.paused = False
        self.image_until = 0.0
        self.next_frame_at = 0.0
        self.video: Path | None = None
        self.info: VideoInfo | None = None
        self.frame = 0
        self.error = ""
        self.relay_status = ""
        self.wake = threading.Event()
        self._commands: SimpleQueue = SimpleQueue()
        self._infos: dict[Path, VideoInfo] = {}
        self._start_pending = settings.start > 0
        self._restore()

    # -- démarrage -------------------------------------------------------
    def _restore(self) -> None:
        try:
            data = json.loads(self.paths.state.read_text())
        except (OSError, ValueError):
            return
        self.paused = bool(data.get("paused", False))
        now = self.clock()
        try:
            until = float(data.get("image_until") or 0)
        except (TypeError, ValueError):
            return
        # Le Pi n'a pas d'horloge matérielle : une échéance lointaine trahit une heure fausse.
        if data.get("mode") == "IMAGE" and 0 < until - now <= self.settings.max_image_seconds:
            self.mode, self.image_until = "IMAGE", until

    # -- boucle ----------------------------------------------------------
    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.wake.clear()
            try:
                delay = self.tick()
            except Exception:  # un bug ne doit jamais faire tomber le service
                log.exception("erreur inattendue dans le lecteur")
                delay = RETRY_SECONDS
            self.wake.wait(delay)

    def tick(self) -> float:
        self._drain_commands()
        now = self.clock()
        if self.paused:
            self._write_state(now)
            return TICK_MAX
        if not self._active(now):
            self._write_state(now)
            return min(IDLE_TICK, TICK_MAX)
        if self.mode == "IMAGE":
            if self.image_until - now > self.settings.max_image_seconds:
                self.image_until = now          # l'heure a sauté : ne pas figer l'écran
            if now < self.image_until:
                self._write_state(now)
                return min(self.image_until - now, TICK_MAX)
            self.mode = "FILM"
            self.next_frame_at = 0.0
        item = self.queue.peek(now)
        if item is not None:
            self._show_image(item)
            return min(max(self.image_until - self.clock(), 0.0), TICK_MAX)
        if now < self.next_frame_at:
            self._write_state(now)
            return min(self.next_frame_at - now, TICK_MAX)
        self._step_film(now)
        return min(max(self.next_frame_at - self.clock(), 0.0), TICK_MAX)

    def _active(self, now: float) -> bool:
        return in_active_hours(self.settings.active_hours, datetime.fromtimestamp(now))

    # -- images envoyées -------------------------------------------------
    def _show_image(self, item: QueuedImage) -> None:
        s = self.settings
        try:
            with Image.open(item.path) as raw:
                raw.load()
                panel = to_panel(raw, (self.display.width, self.display.height), contrast=s.contrast)
        except (OSError, ValueError) as exc:
            log.warning("image %s illisible : %s", item.id, exc)
            self.error = f"Image reçue illisible ({item.id[:8]})"
            self.queue.discard(item)
            self._notify(item, "failed")
            self._write_state(self.clock())
            return
        duration = int(_clamp(item.duration, s.min_image_seconds, s.max_image_seconds))
        self.display.prepare()
        self.display.display(panel)       # un seul rafraîchissement : l'e-paper garde l'image
        self.display.sleep()
        self.queue.discard(item)
        self._notify(item, "shown")
        self.mode = "IMAGE"
        self.image_until = self.clock() + duration
        self.error = ""
        self._write_state(self.clock())

    def _notify(self, item: QueuedImage, status: str) -> None:
        if self.on_done:
            try:
                self.on_done(item, status)
            except Exception:
                log.exception("on_done a échoué")

    # -- film ------------------------------------------------------------
    def _pick_video(self, videos: list[Path]) -> Path:
        s = self.settings
        by_name = {v.name: v for v in videos}
        if s.file and Path(s.file).name in by_name:
            return by_name[Path(s.file).name]
        if s.random_file:
            return self.rng.choice(videos)
        try:
            name = Path(self.paths.now_playing.read_text().strip()).name
            if name in by_name:
                return by_name[name]
        except OSError:
            pass
        return videos[0]

    def _load_video(self, video: Path) -> None:
        info = self._infos.get(video) or self.probe(video)
        self._infos[video] = info
        self.video, self.info = video, info
        if self.settings.random_frames:
            self.frame = 0
        else:
            frame = read_progress(self.paths.progress, video)
            if self._start_pending:
                frame, self._start_pending = self.settings.start, False
            self.frame = int(_clamp(frame, 0, info.frame_count))
        try:
            self.paths.now_playing.write_text(str(video.resolve()))
        except OSError:
            pass

    def _step_film(self, now: float) -> None:
        videos = self.library.ready()
        if not videos:
            self.video = self.info = None
            self.error = "Aucun film prêt dans le dossier des films."
            self.next_frame_at = now + IDLE_TICK
            self._write_state(now)
            return
        if self.video is None or self.video not in videos:
            try:
                self._load_video(self._pick_video(videos))
            except (OSError, ValueError) as exc:
                self.video = self.info = None
                self.error = f"Film illisible : {exc}"
                self.next_frame_at = now + IDLE_TICK
                self._write_state(now)
                return
        s, info, video = self.settings, self.info, self.video
        start = self.clock()
        try:
            if s.random_frames:
                self.frame = self.rng.randint(0, info.frame_count)
            image = self.frames.frame(video, info, self.frame, s,
                                      (self.display.width, self.display.height))
            self.display.prepare()
            self.display.display(image)
            self.display.sleep()
            self.error = ""
        except Exception as exc:  # ffmpeg ou écran : on continue plutôt que de planter le service
            log.exception("échec d'affichage de %s image %s", video.name, self.frame)
            self.error = f"Erreur d'affichage : {exc}"
            self._advance(videos)
            self.next_frame_at = self.clock() + RETRY_SECONDS
            self._write_state(self.clock())
            return
        self._advance(videos)
        self.next_frame_at = start + s.delay
        self._write_state(self.clock())

    def _advance(self, videos: list[Path]) -> None:
        s = self.settings
        try:
            if s.random_frames:
                if s.random_file:
                    self._load_video(self.rng.choice(videos))
                return
            self.frame += s.increment
            if self.frame > self.info.frame_count:
                if not s.loop:
                    following = (self.rng.choice(videos) if s.random_file
                                 else next_video(videos, self.video))
                    self._load_video(following)
                self.frame = 0
            write_progress(self.paths.progress, self.video, self.frame)
        except (OSError, ValueError) as exc:
            self.error = f"Film suivant illisible : {exc}"

    # -- commandes -------------------------------------------------------
    def submit(self, cmd: dict) -> Future:
        fut: Future = Future()
        self._commands.put((cmd, fut))
        self.wake.set()
        return fut

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd, fut = self._commands.get_nowait()
            except Empty:
                return
            try:
                fut.set_result(self._handle(cmd))
            except Exception as exc:
                fut.set_result({"ok": False, "error": str(exc)})

    def _handle(self, cmd: dict) -> dict:
        name = cmd.get("cmd")
        if name == "pause":
            self.paused = True
        elif name == "resume":
            self.paused = False
            self.next_frame_at = 0.0
        elif name == "skip":
            if self.mode == "IMAGE":
                self.image_until = 0.0
            else:
                self.next_frame_at = 0.0
        elif name == "play":
            target = next((v for v in self.library.ready() if v.name == cmd.get("video")), None)
            if target is None:
                return {"ok": False, "error": "film introuvable ou pas encore prêt"}
            self._load_video(target)
            self.next_frame_at = 0.0
        elif name == "reload":
            self.settings = Settings.load(self.paths.conf)
            self.queue.max_items = self.settings.queue_max
            self.queue.expiry_seconds = self.settings.queue_expiry_seconds
        elif name == "reset":
            target = next((v for v in self.library.ready() if v.name == cmd.get("video")), None)
            if target is None:
                return {"ok": False, "error": "film introuvable"}
            write_progress(self.paths.progress, target, 0)
            if target == self.video:
                self.frame = 0
        elif name == "queue_remove":
            item = self.queue.remove(str(cmd.get("id", "")))
            if item is None:
                return {"ok": False, "error": "image introuvable dans la file"}
            self._notify(item, "failed")
        elif name == "queue_clear":
            for item in self.queue.clear():
                self._notify(item, "failed")
        else:
            return {"ok": False, "error": f"commande inconnue : {name}"}
        self._write_state(self.clock())
        return {"ok": True}

    # -- état ------------------------------------------------------------
    def snapshot(self, now: float) -> dict:
        s, info = self.settings, self.info
        percent = remaining = None
        if info is not None:
            percent = round(100 * self.frame / info.frame_count, 1)
            if not s.random_frames:
                remaining = int(max(info.frame_count - self.frame, 0) / s.increment * s.delay)
        return {
            "mode": self.mode, "paused": self.paused,
            "video": self.video.name if self.video else None,
            "frame": self.frame, "frame_count": info.frame_count if info else None,
            "percent": percent, "remaining_seconds": remaining,
            "next_frame_at": self.next_frame_at, "image_until": self.image_until,
            "active": self._active(now),
            "queue": [{"id": i.id, "duration": i.duration} for i in self.queue.items()],
            "error": self.error, "relay_status": self.relay_status,
            "delay": s.delay, "increment": s.increment, "updated_at": now,
        }

    def _write_state(self, now: float) -> None:
        tmp = self.paths.state.with_name(self.paths.state.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self.snapshot(now)))
            os.replace(tmp, self.paths.state)
        except OSError:
            log.warning("impossible d'écrire %s", self.paths.state)
```

- [ ] **Step 5: Lancer les tests**

Run: `.venv/bin/pytest tests/test_player.py -q`
Expected: PASS. Si `test_image_preempts…` échoue sur la durée de sommeil, vérifier que `tick()` renvoie bien `≤ 5`.

- [ ] **Step 6: Commit**

```bash
git add slowmovie_inkdrop/player.py tests/conftest.py tests/test_player.py
git commit -m "feat(player): machine à états FILM/IMAGE, commandes, reprise après redémarrage"
```

---

### Task 8: Extraction d'images, socket de contrôle, service (`framesource.py`, `control.py`, `runner.py`, `cli.py`)

**Files:**
- Create: `slowmovie_inkdrop/framesource.py`, `slowmovie_inkdrop/control.py`, `slowmovie_inkdrop/runner.py`, `slowmovie_inkdrop/cli.py`
- Test: `tests/test_control.py`, `tests/test_framesource.py`

**Interfaces:**
- Consumes: `Player`, `FrameSource` (T7) ; `Library`, `probe_video`, `find_subtitles` (T5) ; `make_display` (T4) ; `ImageQueue` (T6) ; `Settings`, `Paths`, `default_base` (T2).
- Produces:
  - `FfmpegFrameSource().frame(video, info, frame, settings, size) -> Image`
  - `ControlServer(player, path: Path)` avec `.start()` / `.stop()` ; `send_command(path: Path, command: dict, timeout: float = 10) -> dict` ; `ServiceNotRunning(RuntimeError)`
  - `runner.run(base: Path) -> int`
  - `cli.main(argv: list[str] | None = None) -> int` avec pour l'instant la sous-commande `run`

- [ ] **Step 1: Tests du socket de contrôle**

```python
# tests/test_control.py
import shutil
import tempfile
from concurrent.futures import Future
from pathlib import Path

import pytest

from slowmovie_inkdrop.control import ControlServer, ServiceNotRunning, send_command


class FakePlayer:
    def __init__(self):
        self.received = []

    def submit(self, cmd):
        self.received.append(cmd)
        fut = Future()
        fut.set_result({"ok": True, "echo": cmd.get("cmd")})
        return fut


@pytest.fixture
def sock_path():
    d = tempfile.mkdtemp(dir="/tmp")     # chemin court : limite des sockets Unix
    yield Path(d) / "c.sock"
    shutil.rmtree(d, ignore_errors=True)


def test_roundtrip(sock_path):
    player = FakePlayer()
    server = ControlServer(player, sock_path)
    server.start()
    try:
        assert send_command(sock_path, {"cmd": "pause"}) == {"ok": True, "echo": "pause"}
        assert player.received == [{"cmd": "pause"}]
    finally:
        server.stop()
    assert not sock_path.exists()


def test_garbage_request_gets_error_reply(sock_path):
    import socket
    server = ControlServer(FakePlayer(), sock_path)
    server.start()
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(sock_path))
            s.sendall(b"pas du json\n")
            assert b'"ok": false' in s.makefile("rb").readline()
    finally:
        server.stop()


def test_service_not_running(sock_path):
    with pytest.raises(ServiceNotRunning):
        send_command(sock_path, {"cmd": "pause"})
```

- [ ] **Step 2: Test d'extraction ffmpeg (ignoré si ffmpeg absent)**

```python
# tests/test_framesource.py
import shutil
import subprocess

import pytest

from slowmovie_inkdrop.config import Settings
from slowmovie_inkdrop.framesource import FfmpegFrameSource
from slowmovie_inkdrop.videos import probe_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "test.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24",
                    "-t", "2", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_extracts_frame_with_panel_size(clip):
    info = probe_video(clip)
    img = FfmpegFrameSource().frame(clip, info, 10, Settings(), (800, 480))
    assert img.size == (800, 480)


def test_fullscreen_and_timecode_options_run(clip):
    info = probe_video(clip)
    img = FfmpegFrameSource().frame(clip, info, 5, Settings(fullscreen=True, timecode=True), (800, 480))
    assert img.size == (800, 480)
```

- [ ] **Step 3: Lancer pour voir l'échec** — FAIL (modules absents)

- [ ] **Step 4: Implémenter `framesource.py`**

```python
"""Extraction d'une image de film avec ffmpeg (adapté de l'amont SlowMovie)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image

from .config import Settings
from .videos import VideoInfo, find_subtitles


class FfmpegFrameSource:
    def __init__(self, scratch: Path | None = None):
        shm = Path("/dev/shm")
        base = scratch or (shm if shm.is_dir() else Path(tempfile.gettempdir()))
        self.target = base / "slowmovie-frame.png"

    def frame(self, video: Path, info: VideoInfo, frame: int, settings: Settings,
              size: tuple[int, int]) -> Image.Image:
        import ffmpeg  # import paresseux

        width, height = size
        position = f"{int(frame * info.frame_time_ms)}ms"
        stream = ffmpeg.input(str(video), ss=position).filter("scale", "iw*sar", "ih")
        if settings.fullscreen:
            if info.aspect_ratio > width / height:
                stream = stream.filter("crop", f"ih*{width / height}", "ih")
            elif info.aspect_ratio < width / height:
                stream = stream.filter("crop", "iw", f"iw*{height / width}")
        stream = (stream.filter("scale", width, height, force_original_aspect_ratio=1)
                  .filter("pad", width, height, -1, -1))
        subtitles = find_subtitles(video) if settings.subtitles else None
        if subtitles:
            stream = stream.filter("subtitles", str(subtitles))
        elif settings.timecode:
            stream = stream.drawtext(escape_text=False, text="%{pts:hms}", fontcolor="white",
                                     fontsize=24, x="(w-text_w)/2", y="h-(text_h*2)",
                                     bordercolor="black", borderw=1)
        (stream.output(str(self.target), vframes=1, copyts=None)
         .overwrite_output().run(capture_stdout=True, capture_stderr=True))
        image = Image.open(self.target)
        image.load()
        return image
```

- [ ] **Step 5: Implémenter `control.py`**

```python
"""Socket Unix : la TUI et la CLI envoient des commandes au lecteur."""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
from pathlib import Path


class ServiceNotRunning(RuntimeError):
    pass


class ControlServer:
    def __init__(self, player, path: Path):
        self.player = player
        self.path = Path(path)
        self._server: socketserver.ThreadingUnixStreamServer | None = None

    def start(self) -> None:
        player = self.player

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    request = json.loads(self.rfile.readline(65536) or b"{}")
                    reply = player.submit(request).result(timeout=10)
                except Exception as exc:
                    reply = {"ok": False, "error": f"requête invalide : {exc}"}
                self.wfile.write(json.dumps(reply).encode() + b"\n")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._server = socketserver.ThreadingUnixStreamServer(str(self.path), Handler)
        self._server.daemon_threads = True
        os.chmod(self.path, 0o660)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self.path.exists():
            self.path.unlink()


def send_command(path: Path, command: dict, timeout: float = 10) -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(json.dumps(command).encode() + b"\n")
            return json.loads(s.makefile("rb").readline())
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise ServiceNotRunning("Le service slowmovie ne tourne pas.") from exc
```

- [ ] **Step 6: Implémenter `runner.py` et `cli.py` (sous-commande `run`)**

`runner.py` :
```python
"""Assemble et lance le service `slowmovie run`."""
from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path

from .config import Paths, Settings
from .control import ControlServer
from .display import make_display
from .framesource import FfmpegFrameSource
from .image_queue import ImageQueue
from .player import Player
from .videos import probe_video

log = logging.getLogger("slowmovie")


def setup_logging(paths: Paths, level: str) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-8s: %(name)s : %(message)s")
    for handler in (logging.FileHandler(paths.log), logging.StreamHandler()):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def _library_loop(library: Library, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            library.poll(time.time())
        except Exception:
            log.exception("analyse du dossier des films")
        stop.wait(5)


def run(base: Path) -> int:
    paths = Paths(base)
    settings = Settings.load(paths.conf)
    paths.ensure(settings)
    setup_logging(paths, settings.loglevel)
    display = make_display(settings.epd)
    library = Library(paths.videos_dir(settings), paths.library, probe_video)
    library.poll(time.time())
    queue = ImageQueue(paths.queue_dir, settings.queue_max, settings.queue_expiry_seconds)
    outbox_hook = None
    outbox = None
    try:
        from .relayclient import AckOutbox
        outbox = AckOutbox(paths.acks)
        outbox_hook = lambda item, status: outbox.add(item.id, status)  # noqa: E731
    except ImportError:  # relayclient arrive à la tâche 10
        pass
    player = Player(paths=paths, settings=settings, display=display, frames=FfmpegFrameSource(),
                    image_queue=queue, library=library, on_done=outbox_hook)
    stop = threading.Event()

    def shutdown(signum, frame) -> None:
        log.info("arrêt demandé")
        stop.set()
        player.wake.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    control = ControlServer(player, paths.socket)
    control.start()
    threading.Thread(target=_library_loop, args=(library, stop), daemon=True).start()
    if outbox is not None:
        from .relayclient import relay_loop
        threading.Thread(target=relay_loop, args=(player, queue, outbox, stop, time.time),
                         daemon=True).start()
    log.info("slowmovie démarré (%s)", base)
    try:
        player.run_forever(stop)
    finally:
        control.stop()
        try:
            if player.settings.clear:
                display.prepare()
                display.clear()
            display.close()
        except Exception:
            log.exception("fermeture de l'écran")
    return 0
```

`cli.py` :
```python
"""Ligne de commande : `slowmovie`."""
from __future__ import annotations

import argparse
import sys

from .config import default_base


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slowmovie", description="SlowMovie avec envoi d'images à distance")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="lance le service (utilisé par systemd)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        from .runner import run
        return run(default_base())
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Note : `runner.py` importe `relayclient` en `try/except ImportError` uniquement pour que cette tâche soit testable seule ; la tâche 10 crée ce module et le `try/except` y sera retiré.

- [ ] **Step 7: Lancer les tests**

Run: `.venv/bin/pytest tests/test_control.py tests/test_framesource.py -q`
Expected: PASS (les tests ffmpeg sont ignorés si ffmpeg est absent ; l'installer avec `brew install ffmpeg` pour les exécuter).

- [ ] **Step 8: Essai à blanc du service sur le Mac**

```bash
mkdir -p /tmp/sm-test/Videos
ffmpeg -v error -f lavfi -i testsrc=size=640x360:rate=24 -t 20 -pix_fmt yuv420p /tmp/sm-test/Videos/test.mp4
printf 'delay = 5\nincrement = 24\n' > /tmp/sm-test/slowmovie.conf
SLOWMOVIE_HOME=/tmp/sm-test SLOWMOVIE_FAKE_DISPLAY=/tmp/sm-test/display \
  SLOWMOVIE_SOCKET=/tmp/sm-test.sock .venv/bin/slowmovie run &
sleep 25; cat /tmp/sm-test/state.json; kill %1
```
Expected: `state.json` avec `"mode": "FILM"` et `"video": "test.mp4"` ; `/tmp/sm-test/display/last.png` existe. (Les premiers 10 s, le film est « en attente » : la bibliothèque laisse le fichier se stabiliser.)

- [ ] **Step 9: Commit**

```bash
git add slowmovie_inkdrop/framesource.py slowmovie_inkdrop/control.py slowmovie_inkdrop/runner.py slowmovie_inkdrop/cli.py tests/test_control.py tests/test_framesource.py
git commit -m "feat(service): extraction ffmpeg, socket de contrôle, commande run"
```

---

### Task 9: Relais (`slowmovie_inkdrop/relay/`)

**Files:**
- Create: `slowmovie_inkdrop/relay/__init__.py` (vide), `config.py`, `security.py`, `store.py`, `app.py`, `cli.py`, `static/index.html`
- Test: `tests/test_relay_security.py`, `tests/test_relay_api.py`

**Interfaces:**
- Consumes: `sanitize_upload`, `InvalidImage` (T3).
- Produces:
  - `RelayConfig(data_dir, password_hash, device_token, min_duration=60, max_duration=3600, queue_max=10, expiry_seconds=21600, max_upload_bytes=10*1024*1024, trust_proxy=False)` ; `RelayConfig.from_env(env=os.environ)` (variables `SLOWMOVIE_RELAY_DATA`, `SLOWMOVIE_RELAY_PASSWORD_HASH`, `SLOWMOVIE_RELAY_DEVICE_TOKEN`, `SLOWMOVIE_RELAY_MIN_DURATION`, `…_MAX_DURATION`, `…_QUEUE_MAX`, `…_EXPIRY`, `…_TRUST_PROXY`)
  - `hash_password(password: str) -> str`, `verify_password(password: str, stored: str) -> bool`
  - `RateLimiter(clock, max_failures=5, block_seconds=900, max_sends=20, window=3600)` : `blocked(ip) -> float`, `fail(ip)`, `success(ip)`, `allow_send(ip) -> bool`
  - `Store(data_dir)` : `add(id, duration, png, now)`, `active_count()`, `get(id)`, `queued()`, `set_status(id, status)`, `position_and_eta(id) -> tuple[int, int]`, `purge(now, expiry)`, `image_path(id)`
  - `create_app(config, *, clock=time.time) -> FastAPI`
  - API : `GET /` ; `GET /api/config` → `{min_duration, max_duration, max_mb}` ; `POST /api/send` (form : `password`, `duration`, `image`) → 202 `{id, position, eta_seconds}` ; `GET /api/status/{id}` → `{status, position, eta_seconds}` ; Pi : `GET /api/device/pending` → `[{id, duration}]`, `GET /api/device/image/{id}` (marque `fetched`), `POST /api/device/ack/{id}` json `{"status": "shown"|"failed"}` ; en-tête `Authorization: Bearer <jeton>`
  - Codes d'erreur : 401 mot de passe, 429 blocage ou trop d'envois, 413 trop lourd, 415 format, 422 fichier corrompu ou durée invalide, 409 file pleine

- [ ] **Step 1: Tests de sécurité**

```python
# tests/test_relay_security.py
from slowmovie_inkdrop.relay.security import RateLimiter, hash_password, verify_password


def test_hash_and_verify():
    stored = hash_password("correct horse")
    assert stored != "correct horse" and "correct" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("autre", stored)


def test_hashes_are_salted():
    assert hash_password("x") != hash_password("x")


def test_verify_rejects_malformed_hash():
    assert not verify_password("x", "n'importe quoi")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_blocks_after_five_failures_then_unblocks():
    clock = Clock()
    limiter = RateLimiter(clock)
    for _ in range(4):
        limiter.fail("1.2.3.4")
    assert limiter.blocked("1.2.3.4") == 0
    limiter.fail("1.2.3.4")
    assert limiter.blocked("1.2.3.4") > 0
    assert limiter.blocked("5.6.7.8") == 0
    clock.t = 901
    assert limiter.blocked("1.2.3.4") == 0


def test_success_resets_failures():
    limiter = RateLimiter(Clock())
    for _ in range(4):
        limiter.fail("ip")
    limiter.success("ip")
    limiter.fail("ip")
    assert limiter.blocked("ip") == 0


def test_send_window():
    clock = Clock()
    limiter = RateLimiter(clock, max_sends=2, window=100)
    assert limiter.allow_send("ip") and limiter.allow_send("ip")
    assert not limiter.allow_send("ip")
    clock.t = 101
    assert limiter.allow_send("ip")
```

- [ ] **Step 2: Tests de l'API**

```python
# tests/test_relay_api.py
import io
import logging

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password

PASSWORD = "correct horse battery"
TOKEN = "t" * 40
DEVICE = {"Authorization": f"Bearer {TOKEN}"}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def png(size=(50, 30), color="blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password(PASSWORD), device_token=TOKEN)
    return TestClient(create_app(config, clock=clock)), clock, config


def send(client, *, password=PASSWORD, duration="300", data=None, name="p.png", mime="image/png"):
    return client.post("/api/send", data={"password": password, "duration": duration},
                       files={"image": (name, data if data is not None else png(), mime)})


def test_page_and_config(env):
    client, *_ = env
    assert client.get("/").status_code == 200 and "<form" in client.get("/").text
    assert client.get("/api/config").json() == {"min_duration": 60, "max_duration": 3600, "max_mb": 10}


def test_upload_ok_returns_position_and_eta(env):
    client, *_ = env
    r = send(client)
    assert r.status_code == 202
    body = r.json()
    assert body["position"] == 1 and body["eta_seconds"] == 0 and len(body["id"]) == 32
    r2 = send(client, duration="120")
    assert r2.json()["position"] == 2 and r2.json()["eta_seconds"] == 300


def test_wrong_password(env):
    client, *_ = env
    assert send(client, password="faux").status_code == 401


def test_lockout_after_five_failures_even_with_good_password(env):
    client, clock, _ = env
    for _ in range(5):
        send(client, password="faux")
    r = send(client)
    assert r.status_code == 429 and "minute" in r.json()["detail"]
    clock.t += 901
    assert send(client).status_code == 202


def test_password_never_logged(env, caplog):
    client, *_ = env
    with caplog.at_level(logging.DEBUG):
        send(client, password="Secret-à-ne-pas-logger")
        send(client)
    assert "Secret-à-ne-pas-logger" not in caplog.text and PASSWORD not in caplog.text


@pytest.mark.parametrize("duration", ["", "abc", "1.5", "-5", "0", "59", "3601", "9" * 30, " 300x"])
def test_bad_durations(env, duration):
    client, *_ = env
    r = send(client, duration=duration)
    assert r.status_code == 422 and "durée" in r.json()["detail"].lower()


def test_svg_rejected_as_unsupported(env):
    client, *_ = env
    r = send(client, data=b'<svg xmlns="http://www.w3.org/2000/svg"/>', name="a.svg", mime="image/svg+xml")
    assert r.status_code in (415, 422)


def test_corrupt_image_rejected(env):
    client, *_ = env
    assert send(client, data=png()[:40]).status_code == 422


def test_oversize_rejected(env, tmp_path):
    import os
    noisy = io.BytesIO()
    Image.frombytes("RGB", (100, 100), os.urandom(30000)).save(noisy, "PNG")   # incompressible
    config = RelayConfig(data_dir=tmp_path / "x", password_hash=hash_password(PASSWORD),
                         device_token=TOKEN, max_upload_bytes=1000)
    client = TestClient(create_app(config))
    assert send(client, data=noisy.getvalue()).status_code == 413


def test_queue_full_is_refused_with_message(env, tmp_path):
    config = RelayConfig(data_dir=tmp_path / "y", password_hash=hash_password(PASSWORD),
                         device_token=TOKEN, queue_max=2)
    client = TestClient(create_app(config))
    assert send(client).status_code == 202 and send(client).status_code == 202
    r = send(client)
    assert r.status_code == 409 and "file" in r.json()["detail"].lower()


def test_device_endpoints_require_token(env):
    client, *_ = env
    assert client.get("/api/device/pending").status_code == 401
    assert client.get("/api/device/pending", headers={"Authorization": "Bearer faux"}).status_code == 401


def test_device_flow_pending_fetch_ack(env):
    client, *_ = env
    image_id = send(client, duration="120").json()["id"]
    pending = client.get("/api/device/pending", headers=DEVICE).json()
    assert pending == [{"id": image_id, "duration": 120}]
    data = client.get(f"/api/device/image/{image_id}", headers=DEVICE)
    assert data.status_code == 200 and data.content[:4] == b"\x89PNG"
    assert client.get("/api/device/pending", headers=DEVICE).json() == []   # déjà récupérée
    assert client.get(f"/api/status/{image_id}").json()["status"] == "fetched"
    r = client.post(f"/api/device/ack/{image_id}", headers=DEVICE, json={"status": "shown"})
    assert r.status_code == 200
    assert client.get(f"/api/status/{image_id}").json()["status"] == "shown"
    assert client.get(f"/api/device/image/{image_id}", headers=DEVICE).status_code == 404


def test_ack_failed_frees_queue(env):
    client, *_ = env
    image_id = send(client).json()["id"]
    client.get(f"/api/device/image/{image_id}", headers=DEVICE)
    client.post(f"/api/device/ack/{image_id}", headers=DEVICE, json={"status": "failed"})
    assert client.get(f"/api/status/{image_id}").json()["status"] == "failed"


def test_ack_rejects_unknown_status(env):
    client, *_ = env
    image_id = send(client).json()["id"]
    r = client.post(f"/api/device/ack/{image_id}", headers=DEVICE, json={"status": "nimporte"})
    assert r.status_code == 422


def test_unknown_id_is_404_and_path_traversal_is_safe(env):
    client, *_ = env
    assert client.get("/api/status/" + "0" * 32).status_code == 404
    assert client.get("/api/device/image/..%2F..%2Fetc%2Fpasswd", headers=DEVICE).status_code in (404, 422)


def test_unacked_items_expire_and_free_the_queue(env):
    client, clock, config = env
    image_id = send(client).json()["id"]
    client.get(f"/api/device/image/{image_id}", headers=DEVICE)    # récupérée, accusé jamais reçu
    clock.t += config.expiry_seconds + 1
    client.get("/api/device/pending", headers=DEVICE)               # déclenche le nettoyage
    assert client.get(f"/api/status/{image_id}").json()["status"] == "expired"
```

- [ ] **Step 3: Lancer pour voir l'échec** — FAIL (paquet `relay` absent)

- [ ] **Step 4: Implémenter `relay/config.py` et `relay/security.py`**

`relay/config.py` :
```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RelayConfig:
    data_dir: Path
    password_hash: str
    device_token: str
    min_duration: int = 60
    max_duration: int = 3600
    queue_max: int = 10
    expiry_seconds: int = 21600
    max_upload_bytes: int = 10 * 1024 * 1024
    trust_proxy: bool = False

    @classmethod
    def from_env(cls, env=os.environ) -> "RelayConfig":
        try:
            password_hash = env["SLOWMOVIE_RELAY_PASSWORD_HASH"]
            token = env["SLOWMOVIE_RELAY_DEVICE_TOKEN"]
        except KeyError as exc:
            raise SystemExit(f"Variable d'environnement manquante : {exc.args[0]}") from exc
        return cls(
            data_dir=Path(env.get("SLOWMOVIE_RELAY_DATA", "relay-data")),
            password_hash=password_hash, device_token=token,
            min_duration=int(env.get("SLOWMOVIE_RELAY_MIN_DURATION", 60)),
            max_duration=int(env.get("SLOWMOVIE_RELAY_MAX_DURATION", 3600)),
            queue_max=int(env.get("SLOWMOVIE_RELAY_QUEUE_MAX", 10)),
            expiry_seconds=int(env.get("SLOWMOVIE_RELAY_EXPIRY", 21600)),
            trust_proxy=env.get("SLOWMOVIE_RELAY_TRUST_PROXY", "") == "1",
        )
```

`relay/security.py` :
```python
"""Mot de passe haché (scrypt) et limitation des essais."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from collections import defaultdict, deque
from typing import Callable

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}".format(base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class RateLimiter:
    def __init__(self, clock: Callable[[], float], max_failures: int = 5,
                 block_seconds: float = 900, max_sends: int = 20, window: float = 3600):
        self.clock = clock
        self.max_failures, self.block_seconds = max_failures, block_seconds
        self.max_sends, self.window = max_sends, window
        self._failures: dict[str, deque] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._sends: dict[str, deque] = defaultdict(deque)

    def blocked(self, ip: str) -> float:
        until = self._blocked_until.get(ip, 0.0)
        remaining = until - self.clock()
        if remaining <= 0:
            self._blocked_until.pop(ip, None)
            return 0.0
        return remaining

    def fail(self, ip: str) -> None:
        now = self.clock()
        failures = self._failures[ip]
        failures.append(now)
        while failures and now - failures[0] > self.block_seconds:
            failures.popleft()
        if len(failures) >= self.max_failures:
            self._blocked_until[ip] = now + self.block_seconds
            failures.clear()

    def success(self, ip: str) -> None:
        self._failures.pop(ip, None)

    def allow_send(self, ip: str) -> bool:
        now = self.clock()
        sends = self._sends[ip]
        while sends and now - sends[0] > self.window:
            sends.popleft()
        if len(sends) >= self.max_sends:
            return False
        sends.append(now)
        return True
```

- [ ] **Step 5: Implémenter `relay/store.py`**

```python
"""Stockage du relais : SQLite pour l'état, fichiers PNG pour les images."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

ACTIVE = ("queued", "fetched")
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
                             "status TEXT NOT NULL, duration INTEGER NOT NULL, created_at REAL NOT NULL)")
            self._db.commit()

    def image_path(self, image_id: str) -> Path:
        return self.images / f"{image_id}.png"

    def add(self, image_id: str, duration: int, png: bytes, now: float) -> None:
        self.image_path(image_id).write_bytes(png)
        with self._lock:
            self._db.execute("INSERT INTO items VALUES (?, 'queued', ?, ?)", (image_id, duration, now))
            self._db.commit()

    def active_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM items WHERE status IN ('queued','fetched')"
                                    ).fetchone()[0]

    def get(self, image_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM items WHERE id = ?", (image_id,)).fetchone()
        return dict(row) if row else None

    def queued(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM items WHERE status = 'queued' ORDER BY created_at"
                                    ).fetchall()
        return [dict(r) for r in rows]

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
```

- [ ] **Step 6: Implémenter `relay/app.py`**

```python
"""API du relais : page d'envoi, envoi d'images, routes de l'appareil."""
from __future__ import annotations

import hmac
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Callable

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
        return [{"id": i["id"], "duration": i["duration"]} for i in store.queued()]

    @app.get("/api/device/image/{image_id}", dependencies=[Depends(require_device)])
    def device_image(image_id: str) -> Response:
        item = store.get(check_id(image_id))
        path = store.image_path(image_id)
        if item is None or item["status"] not in ("queued", "fetched") or not path.exists():
            raise HTTPException(404, "Image indisponible.")
        store.set_status(image_id, "fetched")
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
```

Attention : `device_image` renvoie le fichier puis, dans le test `test_device_flow…`, l'accusé `shown` supprime le PNG ; la lecture suivante doit répondre 404 (statut final) — c'est ce que vérifie le test.

- [ ] **Step 7: Implémenter `relay/cli.py`**

```python
"""`slowmovie-relay hash-password` et `slowmovie-relay serve`."""
from __future__ import annotations

import argparse
import getpass
import sys

from .security import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slowmovie-relay")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash-password", help="calcule le haché à mettre dans SLOWMOVIE_RELAY_PASSWORD_HASH")
    serve = sub.add_parser("serve", help="lance le relais")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8077)
    args = parser.parse_args(argv)
    if args.command == "hash-password":
        password = getpass.getpass("Mot de passe : ")
        if password != getpass.getpass("Confirmer : "):
            print("Les mots de passe diffèrent.", file=sys.stderr)
            return 1
        print(hash_password(password))
        return 0
    import uvicorn

    from .app import create_app
    from .config import RelayConfig

    uvicorn.run(create_app(RelayConfig.from_env()), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Écrire `relay/static/index.html`** (page pour téléphone, sans framework)

```html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Envoyer une image</title>
<style>
  :root { --bg:#fafaf7; --fg:#1b1b1b; --muted:#6b6b66; --line:#d9d8d2; --accent:#1b1b1b; --err:#a3281f; --ok:#256b3a; }
  @media (prefers-color-scheme: dark) { :root { --bg:#161616; --fg:#eeeeea; --muted:#9a9a94; --line:#333; --accent:#eeeeea; --err:#ff8a80; --ok:#7fd49a; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:17px/1.45 system-ui, sans-serif; }
  main { max-width:30rem; margin:0 auto; padding:1.5rem 16px 3rem; }
  h1 { font-size:1.4rem; margin:0 0 .25rem; } p.lead { color:var(--muted); margin:0 0 1.5rem; }
  label { display:block; font-weight:600; margin:1.1rem 0 .35rem; }
  input, select, button { width:100%; font:inherit; color:inherit; background:transparent; border:1px solid var(--line); border-radius:8px; padding:.7rem .8rem; }
  button { background:var(--accent); color:var(--bg); border-color:var(--accent); font-weight:600; margin-top:1.4rem; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  #preview { display:none; width:100%; max-height:14rem; object-fit:contain; margin-top:.8rem; border:1px solid var(--line); border-radius:8px; }
  #msg { margin-top:1.2rem; min-height:1.5rem; } .err { color:var(--err); } .ok { color:var(--ok); }
  .remember { display:flex; gap:.5rem; align-items:center; margin-top:.7rem; font-weight:400; } .remember input { width:auto; }
</style>
</head>
<body>
<main>
  <h1>Envoyer une image</h1>
  <p class="lead">Elle s'affichera sur l'écran à son tour, pendant la durée choisie.</p>
  <form id="form">
    <label for="image">Image (JPEG, PNG ou WebP)</label>
    <input id="image" name="image" type="file" accept="image/jpeg,image/png,image/webp" required>
    <img id="preview" alt="Aperçu">
    <label for="duration">Durée d'affichage</label>
    <select id="duration" name="duration"></select>
    <label for="password">Mot de passe</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <label class="remember"><input id="remember" type="checkbox"> Se souvenir sur cet appareil</label>
    <button id="submit" type="submit">Envoyer</button>
  </form>
  <div id="msg" role="status" aria-live="polite"></div>
</main>
<script>
const $ = (id) => document.getElementById(id);
const PRESETS = [[60, "1 minute"], [300, "5 minutes"], [900, "15 minutes"], [1800, "30 minutes"], [3600, "1 heure"]];
function stored(key, value) {
  try { if (value === undefined) return localStorage.getItem(key); if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch (e) { return null; }
}
function say(text, cls) { $("msg").textContent = text; $("msg").className = cls || ""; }
function hhmm(seconds) {
  const d = new Date(Date.now() + seconds * 1000);
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
fetch("/api/config").then((r) => r.json()).then((c) => {
  for (const [s, label] of PRESETS) if (s >= c.min_duration && s <= c.max_duration) $("duration").add(new Option(label, s));
}).catch(() => PRESETS.forEach(([s, l]) => $("duration").add(new Option(l, s))));
const saved = stored("sm-password");
if (saved) { $("password").value = saved; $("remember").checked = true; }
$("image").addEventListener("change", () => {
  const f = $("image").files[0];
  if (f) { $("preview").src = URL.createObjectURL(f); $("preview").style.display = "block"; }
});
async function follow(id) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 5000));
    let s;
    try { s = await (await fetch("/api/status/" + id)).json(); } catch (e) { continue; }
    if (s.status === "shown") return say("Votre image a été affichée.", "ok");
    if (s.status === "failed" || s.status === "expired") return say("L'image n'a pas pu être affichée.", "err");
    if (s.status === "queued" || s.status === "fetched")
      say(`Position ${s.position} dans la file — affichage vers ${hhmm(s.eta_seconds)}.`, "ok");
  }
}
$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("submit").disabled = true; say("Envoi en cours…");
  try {
    const r = await fetch("/api/send", { method: "POST", body: new FormData($("form")) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { say(body.detail || "Erreur " + r.status, "err"); return; }
    stored("sm-password", $("remember").checked ? $("password").value : null);
    say(`Reçue. Position ${body.position} — affichage vers ${hhmm(body.eta_seconds)}.`, "ok");
    follow(body.id);
  } catch (err) {
    say("Impossible de joindre le serveur.", "err");
  } finally { $("submit").disabled = false; }
});
</script>
</body>
</html>
```

- [ ] **Step 9: Lancer les tests**

Run: `.venv/bin/pytest tests/test_relay_security.py tests/test_relay_api.py -q`
Expected: PASS. Si `test_bad_durations[ 300x]` échoue, vérifier que `text.isdigit()` est testé après `strip()` ; si le test `9*30` échoue, vérifier la limite de longueur.

- [ ] **Step 10: Commit**

```bash
git add slowmovie_inkdrop/relay tests/test_relay_security.py tests/test_relay_api.py
git commit -m "feat(relay): API d'envoi, page mobile, mot de passe haché, limitation des essais"
```

---

### Task 10: Client du relais côté Pi (`relayclient.py`)

**Files:**
- Create: `slowmovie_inkdrop/relayclient.py`
- Modify: `slowmovie_inkdrop/runner.py` (retirer le `try/except ImportError`)
- Test: `tests/test_relayclient.py`, `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `ImageQueue` (T6) ; `Settings` (T2) ; API du relais (T9) ; `Player` (T7).
- Produces:
  - `RelayError(RuntimeError)`
  - `RelayClient(base_url, token, *, client: httpx.Client | None = None, timeout=15.0)` : `.pending() -> list[dict]`, `.download(image_id) -> bytes`, `.ack(image_id, status) -> None`
  - `AckOutbox(path: Path)` : `.add(image_id, status)`, `.pending() -> list[dict]`, `.done(image_id)`
  - `sync_cycle(client, queue, outbox, settings, now) -> str` (ne lève jamais)
  - `relay_loop(player, queue, outbox, stop, clock) -> None`

- [ ] **Step 1: Écrire les tests**

```python
# tests/test_relayclient.py
import io

import httpx
from PIL import Image

from slowmovie_inkdrop.config import Settings
from slowmovie_inkdrop.image_queue import ImageQueue
from slowmovie_inkdrop.relayclient import AckOutbox, RelayClient, sync_cycle

A, B, C = "a" * 32, "b" * 32, "c" * 32


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buf, "PNG")
    return buf.getvalue()


class FakeRelay:
    """Simule le relais avec httpx.MockTransport et garde la trace des appels."""

    def __init__(self, pending, images=None, fail=False):
        self.pending, self.images, self.fail = pending, images or {}, fail
        self.acks: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("relais éteint")
        assert request.headers["authorization"] == "Bearer jeton"
        path = request.url.path
        if path == "/api/device/pending":
            return httpx.Response(200, json=self.pending)
        if path.startswith("/api/device/image/"):
            return httpx.Response(200, content=self.images[path.rsplit("/", 1)[1]])
        if path.startswith("/api/device/ack/"):
            import json
            self.acks.append((path.rsplit("/", 1)[1], json.loads(request.content)["status"]))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    def client(self) -> RelayClient:
        return RelayClient("https://relais.test", "jeton",
                           client=httpx.Client(transport=httpx.MockTransport(self.handler)))


def make(tmp_path, relay, **kw):
    queue = ImageQueue(tmp_path / "queue", max_items=kw.get("max_items", 10))
    return queue, AckOutbox(tmp_path / "acks.json")


def test_downloads_new_images_into_queue(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 120}], {A: png()})
    queue, outbox = make(tmp_path, relay)
    status = sync_cycle(relay.client(), queue, outbox, Settings(), now=100.0)
    assert [i.id for i in queue.items()] == [A] and queue.items()[0].duration == 120
    assert "1" in status


def test_same_image_delivered_twice_is_queued_once(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 60}], {A: png()})
    queue, outbox = make(tmp_path, relay)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    queue.discard(queue.items()[0])               # affichée entre-temps
    sync_cycle(relay.client(), queue, outbox, Settings(), now=2)   # le relais la propose encore
    assert len(queue) == 0                         # pas d'affichage en double


def test_full_queue_leaves_items_pending(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 60}, {"id": B, "duration": 60}], {A: png(), B: png()})
    queue, outbox = make(tmp_path, relay, max_items=1)
    sync_cycle(relay.client(), queue, outbox, Settings(queue_max=1), now=1)
    assert [i.id for i in queue.items()] == [A]
    assert relay.acks == []                        # B reste en attente côté relais


def test_invalid_image_is_acked_failed(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 60}], {A: b"pas une image"})
    queue, outbox = make(tmp_path, relay)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert len(queue) == 0 and relay.acks == [(A, "failed")]


def test_malicious_id_from_relay_is_ignored(tmp_path):
    relay = FakeRelay([{"id": "../../evil", "duration": 60}], {"../../evil": png()})
    queue, outbox = make(tmp_path, relay)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert len(queue) == 0
    assert not (tmp_path / "evil.png").exists()


def test_relay_down_is_reported_not_raised(tmp_path):
    relay = FakeRelay([], fail=True)
    queue, outbox = make(tmp_path, relay)
    status = sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert "injoignable" in status


def test_lost_ack_is_retried_until_delivered(tmp_path):
    relay = FakeRelay([])
    queue, outbox = make(tmp_path, relay)
    outbox.add(A, "shown")
    relay.fail = True
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert outbox.pending() == [{"id": A, "status": "shown"}]      # conservé
    relay.fail = False
    sync_cycle(relay.client(), queue, outbox, Settings(), now=2)
    assert relay.acks == [(A, "shown")] and outbox.pending() == []


def test_outbox_survives_restart(tmp_path):
    AckOutbox(tmp_path / "acks.json").add(C, "failed")
    assert AckOutbox(tmp_path / "acks.json").pending() == [{"id": C, "status": "failed"}]
```

```python
# tests/test_end_to_end.py
"""De l'envoi sur le relais jusqu'à l'écran, sans réseau ni matériel."""
import json

from fastapi.testclient import TestClient

from slowmovie_inkdrop.config import Settings
from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password
from slowmovie_inkdrop.relayclient import AckOutbox, RelayClient, sync_cycle

from conftest import png_bytes


def test_upload_to_screen_and_ack(make_player, tmp_path):
    config = RelayConfig(data_dir=tmp_path / "relay", password_hash=hash_password("pw pw pw"),
                         device_token="j" * 40)
    web = TestClient(create_app(config))
    sent = web.post("/api/send", data={"password": "pw pw pw", "duration": "120"},
                    files={"image": ("a.png", png_bytes(), "image/png")})
    image_id = sent.json()["id"]

    ctx = make_player()
    outbox = AckOutbox(tmp_path / "acks.json")
    ctx.player.on_done = lambda item, status: outbox.add(item.id, status)
    client = RelayClient("http://testserver", "j" * 40, client=web)

    sync_cycle(client, ctx.queue, outbox, Settings(), ctx.clock())
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE"

    sync_cycle(client, ctx.queue, outbox, Settings(), ctx.clock())      # envoie l'accusé
    assert web.get(f"/api/status/{image_id}").json()["status"] == "shown"
    assert json.loads(ctx.paths.state.read_text())["mode"] == "IMAGE"
```

- [ ] **Step 2: Lancer pour voir l'échec** — FAIL

- [ ] **Step 3: Implémenter `relayclient.py`**

```python
"""Le Pi interroge le relais : connexions sortantes uniquement."""
from __future__ import annotations

import io
import json
import logging
import os
import threading
from pathlib import Path
from typing import Callable

import httpx
from PIL import Image

from .config import Settings
from .image_queue import ImageQueue

log = logging.getLogger(__name__)


class RelayError(RuntimeError):
    pass


class RelayClient:
    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None,
                 timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._client.request(method, self.base_url + path, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise RelayError(f"relais injoignable : {exc}") from exc
        if response.status_code >= 400:
            raise RelayError(f"relais : HTTP {response.status_code}")
        return response

    def pending(self) -> list[dict]:
        return self._request("GET", "/api/device/pending").json()

    def download(self, image_id: str) -> bytes:
        return self._request("GET", f"/api/device/image/{image_id}").content

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
            client.ack(entry["id"], entry["status"])
            outbox.done(entry["id"])
        added = 0
        for item in client.pending():
            image_id = str(item.get("id", ""))
            if queue.known(image_id):
                continue
            if len(queue) >= settings.queue_max:
                break
            data = client.download(image_id)
            try:
                with Image.open(io.BytesIO(data)) as probe:
                    probe.verify()
            except Exception:
                client.ack(image_id, "failed")
                continue
            try:
                queue.add(image_id, data, int(item.get("duration", settings.min_image_seconds)), now)
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
```

- [ ] **Step 4: Simplifier `runner.py`** : remplacer le bloc `outbox_hook`/`try…except ImportError` par un import direct.

Dans `slowmovie_inkdrop/runner.py`, ajouter en tête `from .relayclient import AckOutbox, relay_loop`, puis remplacer :

```python
    outbox_hook = None
    outbox = None
    try:
        from .relayclient import AckOutbox
        outbox = AckOutbox(paths.acks)
        outbox_hook = lambda item, status: outbox.add(item.id, status)  # noqa: E731
    except ImportError:  # relayclient arrive à la tâche 10
        pass
    player = Player(paths=paths, settings=settings, display=display, frames=FfmpegFrameSource(),
                    image_queue=queue, library=library, on_done=outbox_hook)
```
par :
```python
    outbox = AckOutbox(paths.acks)
    player = Player(paths=paths, settings=settings, display=display, frames=FfmpegFrameSource(),
                    image_queue=queue, library=library,
                    on_done=lambda item, status: outbox.add(item.id, status))
```
et remplacer le démarrage conditionnel du thread du relais par :
```python
    threading.Thread(target=relay_loop, args=(player, queue, outbox, stop, time.time),
                     daemon=True).start()
```

- [ ] **Step 5: Lancer toute la suite**

Run: `.venv/bin/pytest -q`
Expected: tout PASS

- [ ] **Step 6: Commit**

```bash
git add slowmovie_inkdrop/relayclient.py slowmovie_inkdrop/runner.py tests/test_relayclient.py tests/test_end_to_end.py
git commit -m "feat(relayclient): synchronisation avec le relais, accusés persistants, test de bout en bout"
```

---

### Task 11: Ajout de films (`slowmovie add`) et CLI complète

**Files:**
- Modify: `slowmovie_inkdrop/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `send_command`, `ServiceNotRunning` (T8) ; `is_supported` (T5) ; `Paths`, `default_base` (T2).
- Produces:
  - `build_rsync_command(file: Path, host: str, user: str, remote_dir: str) -> list[str]`
  - `build_ssh_command(argv: list[str], host: str, user: str) -> list[str]` (`ssh -t user@host slowmovie …`)
  - `is_remote() -> bool` : vrai hors du Pi (pas de `/proc/device-tree/model`) sauf si `SLOWMOVIE_LOCAL=1`
  - `format_status(state: dict | None) -> str`
  - Sous-commandes : `run`, `status`, `pause`, `resume`, `skip`, `play <film>`, `add <fichier>`, `tui` (défaut ; branchée à la tâche 12)
  - Variables d'environnement client : `SLOWMOVIE_HOST` (défaut `slowmovie.local`), `SLOWMOVIE_USER` (`admin`), `SLOWMOVIE_REMOTE_PATH` (`/home/admin/SlowMovie`)

- [ ] **Step 1: Écrire les tests**

```python
# tests/test_cli.py
import json

import pytest

from slowmovie_inkdrop import cli


def test_rsync_command_targets_videos_dir():
    from pathlib import Path
    cmd = cli.build_rsync_command(Path("/tmp/mon film.mp4"), "slowmovie.local", "admin",
                                  "/home/admin/SlowMovie")
    assert cmd[0] == "rsync" and "--partial" in cmd
    assert cmd[-1] == "admin@slowmovie.local:/home/admin/SlowMovie/Videos/"
    assert "/tmp/mon film.mp4" in cmd           # un seul argument, pas d'interprétation par un shell


def test_ssh_command_wraps_arguments():
    cmd = cli.build_ssh_command(["play", "mon film.mp4"], "slowmovie.local", "admin")
    assert cmd[:3] == ["ssh", "-t", "admin@slowmovie.local"]
    assert cmd[3] == "slowmovie play 'mon film.mp4'"


def test_is_remote_respects_local_override(monkeypatch):
    monkeypatch.setenv("SLOWMOVIE_LOCAL", "1")
    assert cli.is_remote() is False


def test_add_rejects_missing_and_unsupported(tmp_path, capsys):
    assert cli.main(["add", str(tmp_path / "absent.mp4")]) == 1
    assert "introuvable" in capsys.readouterr().err
    notes = tmp_path / "notes.txt"
    notes.write_text("x")
    assert cli.main(["add", str(notes)]) == 1
    assert "format" in capsys.readouterr().err.lower()


def test_add_runs_rsync(tmp_path, monkeypatch):
    film = tmp_path / "film.mp4"
    film.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert cli.main(["add", str(film)]) == 0
    assert calls[0][0] == "rsync"


def test_format_status_for_each_mode():
    assert "arrêté" in cli.format_status(None)
    film = {"mode": "FILM", "paused": False, "video": "a.mp4", "percent": 12.5,
            "remaining_seconds": 7200, "queue": [], "error": "", "relay_status": "ok"}
    out = cli.format_status(film)
    assert "a.mp4" in out and "12.5" in out and "2 h" in out
    image = dict(film, mode="IMAGE", queue=[{"id": "x", "duration": 60}], image_until=2_000_000.0,
                 updated_at=1_999_940.0)
    assert "image" in cli.format_status(image).lower() and "1 en attente" in cli.format_status(image)
    assert "pause" in cli.format_status(dict(film, paused=True)).lower()


def test_status_command_reads_state_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLOWMOVIE_LOCAL", "1")
    monkeypatch.setenv("SLOWMOVIE_HOME", str(tmp_path))
    (tmp_path / "state.json").write_text(json.dumps(
        {"mode": "FILM", "paused": False, "video": "z.mp4", "percent": 1.0,
         "remaining_seconds": 60, "queue": [], "error": "", "relay_status": ""}))
    assert cli.main(["status"]) == 0
    assert "z.mp4" in capsys.readouterr().out


def test_control_command_reports_service_down(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLOWMOVIE_LOCAL", "1")
    monkeypatch.setenv("SLOWMOVIE_HOME", str(tmp_path))
    monkeypatch.setenv("SLOWMOVIE_SOCKET", str(tmp_path / "nope.sock"))
    assert cli.main(["pause"]) == 1
    assert "ne tourne pas" in capsys.readouterr().err


def test_remote_mode_delegates_to_ssh(monkeypatch):
    monkeypatch.delenv("SLOWMOVIE_LOCAL", raising=False)
    monkeypatch.setattr(cli, "is_remote", lambda: True)
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert cli.main(["status"]) == 0
    assert calls[0][:3] == ["ssh", "-t", "admin@slowmovie.local"]
```

- [ ] **Step 2: Lancer pour voir l'échec** — FAIL

- [ ] **Step 3: Réécrire `cli.py`**

```python
"""Ligne de commande : `slowmovie`."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .config import Paths, default_base
from .control import ServiceNotRunning, send_command
from .videos import FILE_TYPES, is_supported


def is_remote() -> bool:
    """Hors du Pi, la commande pilote le Pi par SSH."""
    if os.environ.get("SLOWMOVIE_LOCAL") == "1":
        return False
    return not Path("/proc/device-tree/model").exists()


def _client_target() -> tuple[str, str, str]:
    return (os.environ.get("SLOWMOVIE_HOST", "slowmovie.local"),
            os.environ.get("SLOWMOVIE_USER", "admin"),
            os.environ.get("SLOWMOVIE_REMOTE_PATH", "/home/admin/SlowMovie"))


def build_rsync_command(file: Path, host: str, user: str, remote_dir: str) -> list[str]:
    return ["rsync", "-a", "--partial", "--progress", str(file),
            f"{user}@{host}:{remote_dir.rstrip('/')}/Videos/"]


def build_ssh_command(argv: list[str], host: str, user: str) -> list[str]:
    return ["ssh", "-t", f"{user}@{host}", "slowmovie " + " ".join(shlex.quote(a) for a in argv)]


def _hours(seconds: int) -> str:
    if seconds >= 86400:
        return f"{seconds / 86400:.1f} j"
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    return f"{max(seconds // 60, 1)} min"


def format_status(state: dict | None) -> str:
    if not state:
        return "Service arrêté ou jamais démarré (aucun état disponible)."
    lines = []
    if state.get("paused"):
        lines.append("⏸  En pause")
    elif state.get("mode") == "IMAGE":
        left = max(int(state.get("image_until", 0) - state.get("updated_at", 0)), 0)
        lines.append(f"🖼  Image envoyée à l'écran (encore {_hours(left)})")
    else:
        lines.append("🎞  Lecture du film")
    if state.get("video"):
        lines.append(f"Film : {state['video']} — {state.get('percent')} %"
                     + (f", reste environ {_hours(state['remaining_seconds'])}"
                        if state.get("remaining_seconds") is not None else ""))
    queue = state.get("queue") or []
    lines.append(f"File : {len(queue)} en attente")
    if state.get("relay_status"):
        lines.append(f"Relais : {state['relay_status']}")
    if state.get("error"):
        lines.append(f"⚠ {state['error']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slowmovie",
                                     description="SlowMovie avec envoi d'images à distance")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="lance le service (utilisé par systemd)")
    sub.add_parser("tui", help="ouvre l'interface au clavier (défaut)")
    sub.add_parser("status", help="affiche l'état")
    for name, text in (("pause", "met en pause"), ("resume", "reprend"),
                       ("skip", "passe l'image envoyée ou l'image du film")):
        sub.add_parser(name, help=text)
    play = sub.add_parser("play", help="lance un film")
    play.add_argument("film")
    add = sub.add_parser("add", help="copie un film vers le Pi (rsync)")
    add.add_argument("file", type=Path)
    return parser


def _read_state(paths: Paths) -> dict | None:
    try:
        return json.loads(paths.state.read_text())
    except (OSError, ValueError):
        return None


def _add(file: Path) -> int:
    if not file.is_file():
        print(f"Fichier introuvable : {file}", file=sys.stderr)
        return 1
    if not is_supported(file.name):
        print(f"Format non pris en charge. Extensions acceptées : {', '.join(FILE_TYPES)}",
              file=sys.stderr)
        return 1
    host, user, remote = _client_target()
    return subprocess.call(build_rsync_command(file, host, user, remote))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    command = args.command or "tui"
    if command == "add":
        return _add(args.file)
    if command != "run" and is_remote():
        host, user, _ = _client_target()
        return subprocess.call(build_ssh_command(argv or ["tui"], host, user))
    paths = Paths(default_base())
    if command == "run":
        from .runner import run
        return run(default_base())
    if command == "status":
        print(format_status(_read_state(paths)))
        return 0
    if command == "tui":
        from .tui.app import SlowMovieApp
        SlowMovieApp(paths).run()
        return 0
    payload = {"cmd": command}
    if command == "play":
        payload["video"] = args.film
    try:
        reply = send_command(paths.socket, payload)
    except ServiceNotRunning as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not reply.get("ok"):
        print(reply.get("error", "échec"), file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Lancer les tests** — `.venv/bin/pytest tests/test_cli.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/cli.py tests/test_cli.py
git commit -m "feat(cli): status, pause, play, add (rsync) et délégation SSH depuis le Mac"
```

---

### Task 12: Interface TUI (`tui/`)

**Files:**
- Create: `slowmovie_inkdrop/tui/__init__.py` (vide), `tui/model.py`, `tui/theme.py`, `tui/app.py`
- Test: `tests/test_tui_model.py`, `tests/test_tui_app.py`

**Interfaces:**
- Consumes: `Paths`, `Settings` (T2) ; `send_command`, `ServiceNotRunning` (T8) ; `format_status` (T11) ; état `state.json` (T7) ; `library.json` (T5).
- Produces:
  - `tui.model.read_state(paths) -> dict | None`, `film_rows(paths) -> list[dict]` (clés `name, status, percent, error`), `SETTING_FIELDS` (liste de `(champ, libellé, type)`), `tail_log(paths, lines=200) -> str`
  - `tui.app.SlowMovieApp(paths)` : onglets `player` (touche 1), `films` (2), `queue` (3), `settings` (4), `log` (5) ; `?` aide ; `q` quitter
  - Touches : `Espace` pause/reprise, `s` passer ; Films : `Entrée` lancer, `z` remise à zéro, `d` supprimer (confirmation), `a` aide à l'ajout ; File : `x` retirer, `c` vider ; Réglages : `Entrée` modifier

- [ ] **Step 1: Récupérer le thème de cadav**

```bash
gh repo clone charlescoiffier/cadav /tmp/cadav-ref -- --depth 1
grep -rn -i "galaxy" /tmp/cadav-ref/cadav | head
```
Copier la définition du thème « galaxy » trouvée dans cadav vers `slowmovie_inkdrop/tui/theme.py`, sous la forme d'une variable `GALAXY: Theme` (classe `textual.theme.Theme`). Si la définition de cadav se limite à un nom de thème fourni par une dépendance, reproduire les couleurs de cadav telles quelles. Si rien d'exploitable n'est trouvé, utiliser ce thème de repli :

```python
from textual.theme import Theme

GALAXY = Theme(
    name="galaxy",
    primary="#c45aff",
    secondary="#a53bd6",
    accent="#ff7be5",
    foreground="#e2e2f0",
    background="#1a1126",
    surface="#241838",
    panel="#2d1f45",
    success="#6ee7a8",
    warning="#ffd166",
    error="#ff6b81",
    dark=True,
)
```

- [ ] **Step 2: Tests du modèle**

```python
# tests/test_tui_model.py
import json

from slowmovie_inkdrop.config import Paths, Settings
from slowmovie_inkdrop.tui import model


def test_read_state_missing(tmp_path):
    assert model.read_state(Paths(tmp_path)) is None


def test_film_rows_merge_library_and_progress(tmp_path):
    paths = Paths(tmp_path)
    paths.progress.mkdir()
    (paths.progress / "a.mp4.progress").write_text("250")
    paths.library.write_text(json.dumps({
        "a.mp4": {"size": 1, "status": "ready", "frames": 1000, "changed_at": 0},
        "b.mp4": {"size": 1, "status": "error", "error": "illisible", "changed_at": 0},
        "c.mp4": {"size": 1, "status": "pending", "changed_at": 0}}))
    rows = {r["name"]: r for r in model.film_rows(paths)}
    assert rows["a.mp4"]["percent"] == 25.0 and rows["a.mp4"]["status"] == "ready"
    assert rows["b.mp4"]["error"] == "illisible"
    assert rows["c.mp4"]["status"] == "pending" and rows["c.mp4"]["percent"] is None


def test_setting_fields_cover_every_user_setting():
    names = {name for name, _, _ in model.SETTING_FIELDS}
    for expected in ("delay", "increment", "random_file", "random_frames", "loop", "contrast",
                     "fullscreen", "subtitles", "timecode", "min_image_seconds",
                     "max_image_seconds", "queue_max", "active_hours", "relay_url"):
        assert expected in names
    assert "relay_token" not in names           # jamais affiché à l'écran
    assert names <= set(Settings.__dataclass_fields__)


def test_tail_log(tmp_path):
    paths = Paths(tmp_path)
    paths.log.write_text("\n".join(f"ligne {i}" for i in range(300)))
    out = model.tail_log(paths, lines=3)
    assert out.splitlines() == ["ligne 297", "ligne 298", "ligne 299"]
    assert model.tail_log(Paths(tmp_path / "vide"), 3) == ""
```

- [ ] **Step 3: Tests de l'application**

```python
# tests/test_tui_app.py
import json

import pytest

from slowmovie_inkdrop.config import Paths, Settings
from slowmovie_inkdrop.tui import app as tui_app
from slowmovie_inkdrop.tui.app import SlowMovieApp

STATE = {"mode": "FILM", "paused": False, "video": "metropolis.mp4", "percent": 42.0,
         "remaining_seconds": 7200, "queue": [{"id": "a" * 32, "duration": 120}], "error": "",
         "relay_status": "ok (0 nouvelle(s) image(s))", "frame": 10, "frame_count": 100,
         "updated_at": 1.0, "image_until": 0}


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path)
    p.state.write_text(json.dumps(STATE))
    p.library.write_text(json.dumps(
        {"metropolis.mp4": {"size": 1, "status": "ready", "frames": 100, "changed_at": 0}}))
    Settings().save(p.conf)
    return p


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(tui_app, "send_command", lambda path, cmd, timeout=3: calls.append(cmd) or {"ok": True})
    return calls


async def test_player_tab_shows_state(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.pause()
        text = pilot.app.player_text
        assert "metropolis.mp4" in text and "42" in text


async def test_number_keys_switch_tabs(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        assert pilot.app.query_one("TabbedContent").active == "films"
        await pilot.press("3")
        assert pilot.app.query_one("TabbedContent").active == "queue"


async def test_space_toggles_pause(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("space")
        assert sent[-1] == {"cmd": "pause"}


async def test_enter_on_film_plays_it(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("enter")
        assert sent[-1] == {"cmd": "play", "video": "metropolis.mp4"}


async def test_queue_clear(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("3")
        await pilot.press("c")
        assert sent[-1] == {"cmd": "queue_clear"}


async def test_service_down_shows_message_instead_of_crashing(paths, monkeypatch):
    from slowmovie_inkdrop.control import ServiceNotRunning

    def boom(*a, **k):
        raise ServiceNotRunning("Le service slowmovie ne tourne pas.")

    monkeypatch.setattr(tui_app, "send_command", boom)
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("space")
        await pilot.pause()
        assert pilot.app.is_running


async def test_missing_state_does_not_crash(tmp_path, sent):
    async with SlowMovieApp(Paths(tmp_path)).run_test() as pilot:
        await pilot.pause()
        assert "arrêté" in pilot.app.player_text
```

- [ ] **Step 4: Lancer pour voir l'échec** — FAIL

- [ ] **Step 5: Implémenter `tui/model.py`**

```python
"""Lecture de l'état et formatage pour la TUI (aucune dépendance à Textual)."""
from __future__ import annotations

import json
from collections import deque

from ..config import Paths
from ..videos import read_progress

# (champ de Settings, libellé affiché, type d'édition)
SETTING_FIELDS: list[tuple[str, str, str]] = [
    ("delay", "Délai entre deux images (s)", "int"),
    ("increment", "Images avancées à chaque rafraîchissement", "int"),
    ("random_file", "Film au hasard", "bool"),
    ("random_frames", "Images au hasard", "bool"),
    ("loop", "Boucler sur un seul film", "bool"),
    ("fullscreen", "Remplir l'écran (recadrer)", "bool"),
    ("subtitles", "Sous-titres", "bool"),
    ("timecode", "Afficher le timecode", "bool"),
    ("contrast", "Contraste (0.1 à 3.0)", "float"),
    ("min_image_seconds", "Durée minimale d'une image envoyée (s)", "int"),
    ("max_image_seconds", "Durée maximale d'une image envoyée (s)", "int"),
    ("queue_max", "Taille maximale de la file", "int"),
    ("queue_expiry_seconds", "Expiration d'une image en attente (s)", "int"),
    ("active_hours", "Plage horaire (HH:MM-HH:MM, vide = toujours)", "str"),
    ("relay_url", "Adresse du relais", "str"),
    ("relay_poll_seconds", "Interrogation du relais (s)", "int"),
]


def read_state(paths: Paths) -> dict | None:
    try:
        return json.loads(paths.state.read_text())
    except (OSError, ValueError):
        return None


def film_rows(paths: Paths) -> list[dict]:
    try:
        entries = json.loads(paths.library.read_text())
    except (OSError, ValueError):
        return []
    rows = []
    for name, entry in sorted(entries.items()):
        percent = None
        if entry.get("status") == "ready" and entry.get("frames"):
            done = read_progress(paths.progress, paths.base / name)
            percent = round(100 * done / entry["frames"], 1)
        rows.append({"name": name, "status": entry.get("status", "?"), "percent": percent,
                     "error": entry.get("error", "")})
    return rows


def tail_log(paths: Paths, lines: int = 200) -> str:
    try:
        with paths.log.open(encoding="utf-8", errors="replace") as f:
            return "\n".join(deque((line.rstrip("\n") for line in f), maxlen=lines))
    except OSError:
        return ""
```

- [ ] **Step 6: Implémenter `tui/app.py`**

```python
"""Application Textual : cinq onglets, tout au clavier."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane

from ..cli import format_status
from ..config import ConfigError, Paths, Settings
from ..control import ServiceNotRunning, send_command
from . import model
from .theme import GALAXY

STATUS_LABELS = {"ready": "prêt", "pending": "copie en cours…", "error": "illisible"}
HELP = ("1-5 onglets · Espace pause/reprise · s passer · Entrée valider · q quitter\n"
        "Films : Entrée lancer, z remettre à zéro, d supprimer, a ajouter\n"
        "File : x retirer, c vider · Réglages : Entrée modifier")


class Prompt(ModalScreen[str | None]):
    """Saisie d'une valeur ; Échap annule."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Annuler")]

    def __init__(self, question: str, value: str = ""):
        super().__init__()
        self.question, self.value = question, value

    def compose(self) -> ComposeResult:
        yield Label(self.question)
        yield Input(value=self.value, id="prompt-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class SlowMovieApp(App):
    TITLE = "SlowMovie-Inkdrop"
    CSS = "Prompt { align: center middle; } Prompt > * { width: 70%; } #player-info { padding: 1 2; }"
    BINDINGS = [
        Binding("1", "tab('player')", "Lecteur"), Binding("2", "tab('films')", "Films"),
        Binding("3", "tab('queue')", "File"), Binding("4", "tab('settings')", "Réglages"),
        Binding("5", "tab('log')", "Journal"),
        Binding("space", "toggle_pause", "Pause"), Binding("s", "send('skip')", "Passer"),
        Binding("z", "reset_film", "Remettre à zéro", show=False),
        Binding("d", "delete_film", "Supprimer", show=False),
        Binding("a", "how_to_add", "Ajouter", show=False),
        Binding("x", "queue_remove", "Retirer", show=False),
        Binding("c", "send('queue_clear')", "Vider", show=False),
        Binding("question_mark", "help", "Aide"), Binding("q", "quit", "Quitter"),
    ]

    def __init__(self, paths: Paths):
        super().__init__()
        self.paths = paths
        self.state: dict | None = None
        self.player_text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="player"):
            with TabPane("Lecteur", id="player"):
                yield Static("", id="player-info")
            with TabPane("Films", id="films"):
                yield DataTable(id="films-table", cursor_type="row")
            with TabPane("File", id="queue"):
                yield DataTable(id="queue-table", cursor_type="row")
            with TabPane("Réglages", id="settings"):
                yield DataTable(id="settings-table", cursor_type="row")
            with TabPane("Journal", id="log"):
                yield RichLog(id="log-view", wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(GALAXY)
        self.theme = "galaxy"
        self.query_one("#films-table", DataTable).add_columns("Film", "État", "Progression")
        self.query_one("#queue-table", DataTable).add_columns("Image", "Durée")
        self.query_one("#settings-table", DataTable).add_columns("Réglage", "Valeur")
        self.set_interval(2.0, self.refresh_data)
        self.refresh_data()

    # -- rafraîchissement ------------------------------------------------
    def refresh_data(self) -> None:
        self.state = model.read_state(self.paths)
        self.player_text = format_status(self.state)
        self.query_one("#player-info", Static).update(self.player_text)
        self._fill("#films-table", [
            (r["name"], (r["name"], STATUS_LABELS.get(r["status"], r["status"]),
                         f"{r['percent']} %" if r["percent"] is not None else r["error"]))
            for r in model.film_rows(self.paths)])
        self._fill("#queue-table", [
            (i["id"], (i["id"][:8], f"{i['duration'] // 60} min"))
            for i in (self.state or {}).get("queue", [])])
        settings = Settings.load(self.paths.conf)
        self._fill("#settings-table", [
            (name, (label, str(getattr(settings, name)))) for name, label, _ in model.SETTING_FIELDS])
        log = self.query_one("#log-view", RichLog)
        log.clear()
        log.write(model.tail_log(self.paths))

    def _fill(self, table_id: str, rows: list[tuple[str, tuple]]) -> None:
        """Remplit une table en gardant la ligne sélectionnée (rafraîchissement toutes les 2 s)."""
        table = self.query_one(table_id, DataTable)
        row = table.cursor_row
        table.clear()
        for key, cells in rows:
            table.add_row(*cells, key=key)
        if table.row_count:
            table.move_cursor(row=min(row, table.row_count - 1))

    # -- actions ---------------------------------------------------------
    def _send(self, **command) -> dict | None:
        try:
            reply = send_command(self.paths.socket, command, timeout=3)
        except ServiceNotRunning as exc:
            self.notify(str(exc), severity="error")
            return None
        except OSError as exc:
            self.notify(f"Communication impossible : {exc}", severity="error")
            return None
        if not reply.get("ok"):
            self.notify(reply.get("error", "échec"), severity="error")
        return reply

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab
        table = {"films": "#films-table", "queue": "#queue-table",
                 "settings": "#settings-table"}.get(tab)
        if table:
            self.query_one(table, DataTable).focus()      # Entrée agit sur la table de l'onglet

    def action_send(self, name: str) -> None:
        self._send(cmd=name)
        self.refresh_data()

    def action_toggle_pause(self) -> None:
        paused = bool((self.state or {}).get("paused"))
        self._send(cmd="resume" if paused else "pause")
        self.refresh_data()

    def action_help(self) -> None:
        self.notify(HELP, timeout=12)

    def action_how_to_add(self) -> None:
        self.notify("Depuis le Mac : slowmovie add film.mp4 — ou glissez le film dans le partage "
                    "réseau « SlowMovie » (dossier Videos).", timeout=12)

    def _selected_key(self, table_id: str) -> str | None:
        table = self.query_one(table_id, DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_queue_remove(self) -> None:
        key = self._selected_key("#queue-table")
        if key:
            self._send(cmd="queue_remove", id=key)
            self.refresh_data()

    def action_reset_film(self) -> None:
        key = self._selected_key("#films-table")
        if key:
            self._send(cmd="reset", video=key)
            self.refresh_data()

    def action_delete_film(self) -> None:
        key = self._selected_key("#films-table")
        if not key:
            return

        def confirmed(answer: str | None) -> None:
            if (answer or "").strip().lower() in ("o", "oui"):
                settings = Settings.load(self.paths.conf)
                target = self.paths.videos_dir(settings) / key
                try:
                    target.unlink()
                    self.notify(f"{key} supprimé.")
                except OSError as exc:
                    self.notify(f"Suppression impossible : {exc}", severity="error")
                self.refresh_data()

        self.push_screen(Prompt(f"Supprimer définitivement {key} ? (oui/non)"), confirmed)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        key = event.row_key.value
        if table_id == "films-table":
            self._send(cmd="play", video=key)
            self.refresh_data()
        elif table_id == "settings-table":
            self._edit_setting(key)

    def _edit_setting(self, name: str) -> None:
        settings = Settings.load(self.paths.conf)
        kind = next(k for n, _, k in model.SETTING_FIELDS if n == name)
        label = next(text for n, text, _ in model.SETTING_FIELDS if n == name)
        current = getattr(settings, name)
        if kind == "bool":
            self._apply_setting(settings, name, not current)
            return

        def entered(value: str | None) -> None:
            if value is None:
                return
            try:
                self._apply_setting(settings, name, {"int": int, "float": float, "str": str}[kind](value.strip()))
            except ValueError:
                self.notify("Valeur invalide.", severity="error")

        self.push_screen(Prompt(label, str(current)), entered)

    def _apply_setting(self, settings: Settings, name: str, value) -> None:
        setattr(settings, name, value)
        try:
            settings.save(self.paths.conf)
        except ConfigError as exc:
            self.notify(str(exc), severity="error")
            return
        self._send(cmd="reload")
        self.refresh_data()
```

- [ ] **Step 7: Lancer les tests**

Run: `.venv/bin/pytest tests/test_tui_model.py tests/test_tui_app.py -q`
Expected: PASS. Si `test_enter_on_film_plays_it` échoue parce que le curseur n'est pas sur la première ligne, ajouter `await pilot.press("down", "up")` avant `enter`.

- [ ] **Step 8: Essai visuel sur le Mac**

```bash
SLOWMOVIE_LOCAL=1 SLOWMOVIE_HOME=/tmp/sm-test .venv/bin/slowmovie
```
Expected: l'interface s'ouvre, thème galaxy, onglets 1 à 5 navigables au clavier, `q` quitte. Comparer visuellement avec cadav (`cadav play`).

- [ ] **Step 9: Commit**

```bash
git add slowmovie_inkdrop/tui tests/test_tui_model.py tests/test_tui_app.py
git commit -m "feat(tui): interface Textual au clavier, thème galaxy"
```

---

### Task 13: Déploiement et documentation

**Files:**
- Create: `deploy/slowmovie.service`, `deploy/smb.conf.snippet`, `deploy/install-pi.sh`, `deploy/relay/slowmovie-relay.service`, `deploy/relay/nginx.conf.snippet`, `deploy/relay/Caddyfile.snippet`, `deploy/relay/relay.env.example`, `docs/deploiement.md`, `README.md`
- Test: `tests/test_deploy_files.py`

**Interfaces:**
- Consumes: commandes `slowmovie run` (T8), `slowmovie-relay serve` (T9) ; variables d'environnement du relais (T9).
- Produces: unités systemd, script d'installation du Pi, guide de déploiement.

- [ ] **Step 1: Test de cohérence des fichiers de déploiement**

```python
# tests/test_deploy_files.py
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_service_runs_the_installed_command():
    unit = (ROOT / "deploy/slowmovie.service").read_text()
    assert "ExecStart=/home/admin/SlowMovie/.venv/bin/slowmovie run" in unit
    assert "Restart=always" in unit and "User=admin" in unit
    assert "SLOWMOVIE_HOME=/home/admin/SlowMovie" in unit


def test_relay_unit_binds_localhost_only():
    unit = (ROOT / "deploy/relay/slowmovie-relay.service").read_text()
    assert "serve --host 127.0.0.1" in unit and "EnvironmentFile=" in unit


def test_relay_env_example_lists_required_secrets():
    env = (ROOT / "deploy/relay/relay.env.example").read_text()
    for name in ("SLOWMOVIE_RELAY_PASSWORD_HASH", "SLOWMOVIE_RELAY_DEVICE_TOKEN",
                 "SLOWMOVIE_RELAY_DATA", "SLOWMOVIE_RELAY_TRUST_PROXY"):
        assert name in env


def test_proxy_snippets_limit_body_size():
    assert "client_max_body_size" in (ROOT / "deploy/relay/nginx.conf.snippet").read_text()
    assert "max_size" in (ROOT / "deploy/relay/Caddyfile.snippet").read_text()


def test_samba_share_points_to_videos():
    smb = (ROOT / "deploy/smb.conf.snippet").read_text()
    assert "[SlowMovie]" in smb and "/home/admin/SlowMovie/Videos" in smb
```

- [ ] **Step 2: Lancer pour voir l'échec** — FAIL (fichiers absents)

- [ ] **Step 3: Écrire les fichiers du Pi**

`deploy/slowmovie.service` :
```ini
[Unit]
Description=SlowMovie-Inkdrop
After=network-online.target
Wants=network-online.target

[Service]
User=admin
Environment=SLOWMOVIE_HOME=/home/admin/SlowMovie
WorkingDirectory=/home/admin/SlowMovie
ExecStart=/home/admin/SlowMovie/.venv/bin/slowmovie run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`deploy/smb.conf.snippet` :
```ini
[SlowMovie]
   comment = Films SlowMovie
   path = /home/admin/SlowMovie/Videos
   browseable = yes
   read only = no
   valid users = admin
   create mask = 0644
   directory mask = 0755
```

`deploy/install-pi.sh` :
```bash
#!/usr/bin/env bash
# À lancer sur le Pi, depuis une copie du dépôt : ./deploy/install-pi.sh
set -euo pipefail
TARGET=/home/admin/SlowMovie
REPO="$(cd "$(dirname "$0")/.." && pwd)"

sudo apt-get update
sudo apt-get install -y ffmpeg samba avahi-daemon rsync python3-venv python3-dev libopenjp2-7

mkdir -p "$TARGET/Videos"
python3 -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/pip" install --upgrade pip
"$TARGET/.venv/bin/pip" install "$REPO[video,tui,epd]"

if ! grep -q '^\[SlowMovie\]' /etc/samba/smb.conf; then
  sudo tee -a /etc/samba/smb.conf < "$REPO/deploy/smb.conf.snippet" >/dev/null
fi
echo "Définissez le mot de passe Samba de l'utilisateur admin :"
sudo smbpasswd -a admin
sudo systemctl restart smbd avahi-daemon

sudo cp "$REPO/deploy/slowmovie.service" /etc/systemd/system/slowmovie.service
sudo systemctl daemon-reload
sudo systemctl enable --now slowmovie.service

echo "Terminé. Nom d'hôte actuel : $(hostname). Pour joindre le Pi par slowmovie.local :"
echo "  sudo hostnamectl set-hostname slowmovie"
```

- [ ] **Step 4: Écrire les fichiers du relais**

`deploy/relay/slowmovie-relay.service` :
```ini
[Unit]
Description=SlowMovie-Inkdrop relais
After=network.target

[Service]
User=slowmovie
EnvironmentFile=/etc/slowmovie-relay.env
ExecStart=/opt/slowmovie-relay/.venv/bin/slowmovie-relay serve --host 127.0.0.1 --port 8077
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/slowmovie-relay

[Install]
WantedBy=multi-user.target
```

`deploy/relay/relay.env.example` :
```
# Copier en /etc/slowmovie-relay.env (chmod 600, propriétaire root)
SLOWMOVIE_RELAY_DATA=/var/lib/slowmovie-relay
# Résultat de : slowmovie-relay hash-password
SLOWMOVIE_RELAY_PASSWORD_HASH=scrypt$...
# Chaîne aléatoire longue : python3 -c "import secrets; print(secrets.token_urlsafe(32))"
SLOWMOVIE_RELAY_DEVICE_TOKEN=...
# 1 si le relais est derrière un reverse proxy qui ajoute X-Forwarded-For
SLOWMOVIE_RELAY_TRUST_PROXY=1
```

`deploy/relay/nginx.conf.snippet` :
```nginx
server {
    server_name slowmovie.charlescoiffier.fr;
    client_max_body_size 12m;
    location / {
        proxy_pass http://127.0.0.1:8077;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    # HTTPS : certificat géré comme pour cadav.charlescoiffier.fr
}
```

`deploy/relay/Caddyfile.snippet` :
```
slowmovie.charlescoiffier.fr {
    request_body {
        max_size 12MB
    }
    reverse_proxy 127.0.0.1:8077
}
```

- [ ] **Step 5: Écrire `docs/deploiement.md` et `README.md`**

`docs/deploiement.md` doit contenir, dans cet ordre, avec les commandes exactes :
1. **Choisir le proxy** : lire `~/dev/vps-infra` pour savoir si le VPS utilise nginx ou Caddy, puis employer le fichier `deploy/relay/nginx.conf.snippet` **ou** `deploy/relay/Caddyfile.snippet` en l'adaptant à la façon dont cadav est déjà publié (même gestion HTTPS).
2. **Relais** : créer l'utilisateur `slowmovie`, `python3 -m venv /opt/slowmovie-relay/.venv`, `pip install '/chemin/vers/le/dépôt[relay]'`, créer `/var/lib/slowmovie-relay`, générer le haché (`slowmovie-relay hash-password`) et le jeton, remplir `/etc/slowmovie-relay.env`, installer l'unité, `systemctl enable --now slowmovie-relay`, vérifier `curl -I https://slowmovie.charlescoiffier.fr/`.
3. **Pi** : copier le dépôt, `./deploy/install-pi.sh`, puis dans `slowmovie.conf` du Pi renseigner `relay-url` (l'adresse https du relais) et `relay-token` (le jeton) ; `sudo systemctl restart slowmovie`.
4. **Wi-Fi du Pi après changement de box** : option câble Ethernet, ou modification de la carte SD (`bootfs`) ; ne jamais écrire le mot de passe Wi-Fi dans le dépôt.
5. **Mac** : `pipx install .` (ou `pip install -e .`), puis `slowmovie` ouvre la TUI du Pi par SSH ; `slowmovie add film.mp4` copie un film ; variables `SLOWMOVIE_HOST`, `SLOWMOVIE_USER`, `SLOWMOVIE_REMOTE_PATH`.
6. **Sécurité** : droits `600` sur `slowmovie.conf` et `/etc/slowmovie-relay.env`, rotation du mot de passe (`hash-password` puis redémarrage du relais), rotation du jeton (modifier les deux côtés).

`README.md` : présentation en français (objectif, trois fonctions, schéma des trois blocs en texte, installation rapide, lien vers `docs/deploiement.md`, mention que `legacy/` contient les anciens scripts, mention de la licence à confirmer pour le code dérivé de SlowMovie de Tom Whitwell, avertissement « projet vibecodé avec l'aide de l'IA » comme cadav).

- [ ] **Step 6: Lancer toute la suite et le lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: tout PASS, aucune erreur de lint

- [ ] **Step 7: Commit**

```bash
git add deploy docs/deploiement.md README.md tests/test_deploy_files.py
git commit -m "docs(deploy): services systemd, Samba, relais derrière proxy, guide de déploiement"
```

---

### Task 14: Recette sur le matériel (manuelle)

**Files:** aucun (liste de vérification, résultats à noter dans `docs/deploiement.md`, section « Recette »)

**Interfaces:** consomme tout ce qui précède.

Cette tâche ne se lance que lorsque le Pi est de nouveau joignable (câble Ethernet ou Wi-Fi reconfiguré) et que le relais est déployé. Chaque ligne est à cocher à la main ; ne rien cocher sans l'avoir observé.

- [ ] **Step 1: Le service démarre sur le Pi**

Run: `ssh admin@slowmovie.local 'systemctl is-active slowmovie && slowmovie status'`
Expected: `active`, puis l'état du film en cours ; la progression de l'ancien SlowMovie est reprise (même film, même image à quelques pas près).

- [ ] **Step 2: Ajout d'un film depuis le Mac**

Run: `slowmovie add ~/Movies/un-film.mp4`
Expected: rsync termine ; en moins de 30 s, l'onglet Films de la TUI montre le film « prêt ».

- [ ] **Step 3: Ajout d'un film par le Finder**

Dans le Finder : « Aller → Se connecter au serveur » `smb://slowmovie.local/SlowMovie`, glisser un film. Expected : « copie en cours… » pendant le transfert, puis « prêt » ; le film n'est jamais joué à moitié.

- [ ] **Step 4: Envoi d'une image depuis un téléphone en 4G (hors Wi-Fi de la maison)**

Ouvrir `https://slowmovie.charlescoiffier.fr/`, envoyer une photo prise en portrait, durée 1 minute. Expected : message de position, l'écran affiche la photo (bien orientée) dans les 30 s après le prochain moment sûr, puis le film reprend à la même image ; la page indique « affichée ».

- [ ] **Step 5: File d'attente**

Envoyer deux images à la suite depuis deux appareils. Expected : affichées dans l'ordre, chacune pendant sa durée, le film reprend seulement après la seconde.

- [ ] **Step 6: Garde-fous**

Envoyer avec un mauvais mot de passe 5 fois, puis le bon. Expected : blocage avec message en français ; un fichier `.svg` et un GIF animé sont refusés avec un message clair.

- [ ] **Step 7: Coupure du relais, redémarrage du Pi**

Arrêter le relais 2 minutes : le film continue. Redémarrer le Pi pendant l'affichage d'une image : après le redémarrage l'image reste jusqu'à l'échéance (ou le film reprend si l'échéance est passée), sans boucle de redémarrage du service.

- [ ] **Step 8: Noter les résultats et committer**

```bash
git add docs/deploiement.md
git commit -m "docs: résultats de la recette sur le matériel"
```

---

## Self-Review

**Couverture de la spécification**
- §1 objectifs → images (T7, T9, T10), TUI (T12), ajout de films (T5, T8, T11, T13).
- §2 existant → T1 range les trois fichiers dans `legacy/` ; `slowmovie.py` est repris dans `framesource.py`, `videos.py`, `player.py`, avec les mêmes fichiers d'état ; le bug de `progress.json` de `slowmovie_plus.py` n'est pas repris.
- §3 décisions → relais VPS (T9, T10), mot de passe partagé (T9), file (T6, T7), Samba + rsync (T11, T13), Textual (T12).
- §4 architecture → trois blocs (T7–T10, T12) ; `ssh -t` depuis le Mac (T11) ; `state.json` + socket (T7, T8).
- §5 lecteur → états, reprise, horloge sans RTC, relais injoignable, image illisible, expiration 6 h, garde-fous min/max/10 (T6, T7, T10) ; plage horaire optionnelle (T2, T7).
- §6 relais → routes, sécurité, déploiement (T9, T13).
- §7 TUI/CLI → cinq écrans, commandes directes (T11, T12).
- §8 films → Samba, rsync, surveillance du dossier avec stabilité de la taille (T5, T11, T13).
- §9 tests → `FileDisplay`, horloge simulée, tests d'API, fichiers malformés, CI (T1, T3, T4, T7, T9).
- §10 organisation du dépôt → respectée (le paquet s'appelle `slowmovie_inkdrop`).
- §11 points ouverts → plage horaire en option (T2/T7), licence et proxy rappelés dans les contraintes et T13.

**Écarts assumés par rapport au texte de la spécification**
- La spec parlait d'un « socket Unix » et d'un `state.json` : réalisés tels quels. Les commandes `queue_remove` et `queue_clear` s'y ajoutent pour les touches `x` et `c` de l'écran File.
- Le rechargement de la configuration est une commande `reload` : la TUI écrit `slowmovie.conf` puis envoie `reload`.
- `file` dans `slowmovie.conf` ne sert qu'à choisir le film initial ; la TUI lance un film par la commande `play`.

**Cohérence des types** : `ImageQueue.add(id, data, duration, now)`, `peek(now)`, `discard(item)`, `remove(id)`, `clear()` ; `Player(... image_queue=, library=, on_done=)` ; `on_done(item, "shown"|"failed")` ; `AckOutbox.add(id, status)` ; `send_command(path, command, timeout)` ; `format_status(state)` ; `Settings` champs utilisés partout avec les mêmes noms (`min_image_seconds`, `max_image_seconds`, `queue_max`, `queue_expiry_seconds`, `relay_url`, `relay_token`, `relay_poll_seconds`, `active_hours`).

**Review Focus** : chacune des cinq lignes a ses tests (envois bizarres → T3 et T9 ; redémarrage/horloge → T7 ; dossier de films → T5 et T7 ; saisies du formulaire → T9 ; relais coupé / accusé perdu / doublon → T10).

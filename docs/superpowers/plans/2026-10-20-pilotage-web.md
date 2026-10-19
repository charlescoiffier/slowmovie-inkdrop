# Pilotage web, confirmation d'envoi, sous-titres intégrés, TUI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un onglet « Réglages » (mot de passe administrateur séparé) à la passerelle pour piloter le Pi, une page de confirmation d'envoi, les sous-titres intégrés, et deux améliorations de la TUI (navigation `↓`/`↑`, point de départ par film).

**Architecture:** Le Pi envoie un instantané de son état au relais à chaque tour (20 s) et récupère des commandes en attente qu'il revalide, applique via `Player.submit`, puis accuse. Le relais ne garde que le dernier instantané et les commandes. Un module partagé `remote.py` définit la liste fermée des commandes et réglages ; le relais valide la forme, le Pi revalide tout. Les pages sont du HTML/JS statique sans framework.

**Tech Stack:** Python ≥ 3.11, FastAPI, SQLite, httpx, ffmpeg-python, Textual, pytest, Pillow ; JS sans dépendance.

**Spec:** `docs/superpowers/specs/2026-10-20-pilotage-web-design.md` (complète `2026-10-14-slowmovie-inkdrop-design.md`)

## Global Constraints

- Python ≥ 3.11 ; paquet `slowmovie_inkdrop` ; lint `ruff check .` (règles E4,E7,E9,F,I) et `pytest -q` verts à chaque tâche.
- Le Pi n'ouvre aucun port ; il reste la seule source de vérité (`slowmovie.conf`). Le relais ne garde que l'instantané et les commandes.
- Toutes les routes `/api/admin/*` (hors `login`) exigent le cookie `sm_admin` valide **et** l'en-tête `X-Requested-With: slowmovie`. Cookie : `HttpOnly; Secure; SameSite=Strict; Path=/api/admin; Max-Age=28800`.
- Mot de passe administrateur : `SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH` (format `scrypt:…`) ; absent → administration désactivée (503). Blocage après 5 échecs / 900 s, limiteur distinct de celui des envois. Jamais de mot de passe dans les journaux.
- Réglages modifiables à distance (liste fermée) : `delay, increment, random_file, random_frames, loop, fullscreen, subtitles, timecode, subtitle_track, contrast, min_image_seconds, max_image_seconds, queue_max, queue_expiry_seconds, caption_max_chars, active_hours, relay_poll_seconds` (5–300). **Jamais** : `relay_url, relay_token, epd, directory, file, clear, loglevel, start`.
- Commandes : `pause, resume, skip, play, seek, set_start, reset, settings`. Expiration d'une commande non récupérée : 600 s. Instantané : 64 Ko au plus. Le Pi est « en ligne » si son dernier instantané a moins de 60 s.
- Tout texte venant du Pi (noms de films, légendes, messages) est inséré dans les pages avec `textContent`, jamais `innerHTML`.
- Point de départ : `films.json` dans le dossier de données ; il s'applique à la première lecture, à `reset` et au redémarrage en boucle ; `set_start` ne déplace pas un film déjà commencé ; `seek` déplace maintenant.
- Sous-titres intégrés : `subtitle_track` (≥ 0) = numéro parmi **tous** les flux de sous-titres du fichier ; seuls les codecs texte (`subrip, srt, ass, ssa, mov_text, webvtt, text`) sont rendus.
- Messages et interfaces en français. Ne jamais pousser sur GitHub sans demande explicite de l'utilisateur.

## Review Focus

1. **XSS via les chaînes du Pi** : un film nommé `<img src=x onerror=alert(1)>.mkv`, une légende ou un message d'erreur hostiles, affichés sur la page d'administration. Attendu : affichés comme du texte, jamais interprétés.
2. **Accès administrateur volé ou forgé** : cookie absent, expiré, falsifié ; requête inter-sites sans `X-Requested-With` ; administration activée sans haché ; mot de passe d'envoi utilisé comme mot de passe administrateur ; force brute. Attendu : 401/403/503 selon le cas, jamais d'accès.
3. **Commandes qui abîmeraient le Pi** : réglages hors bornes ou de mauvais type (`True` pour un entier, `"10"` pour un booléen), réglages interdits (`relay_url`), nom de film `../x` ou absolu, `seek` négatif ou énorme, commande rejouée ou périmée. Attendu : refus en bloc avec un message, rien d'écrit.
4. **Désynchronisation relais/Pi** : relais redéployé (instantané et commandes perdus), Pi hors ligne des heures puis de retour, accusé perdu. Attendu : l'état revient sous 20 s, les commandes périmées ne sont jamais appliquées, une commande n'est jamais appliquée deux fois.
5. **Sous-titres et noms de fichiers étranges** : nom avec parenthèses, virgules, crochets, apostrophes, point-virgule ; film sans sous-titres ; sous-titres image (PGS) ; numéro de piste hors limites. Attendu : jamais d'erreur qui bloque l'affichage.

---

## File Structure

```
slowmovie_inkdrop/
  remote.py              NOUVEAU  contrat des commandes et réglages distants (partagé relais/Pi)
  starts.py              NOUVEAU  StartPoints (films.json), timecodes, secondes<->images
  videos.py              VideoInfo.subtitle_codecs, parse_probe, has_progress
  library.py             fps et pistes de sous-titres, re-sondage des anciennes entrées
  config.py              Settings.subtitle_track ; Paths.starts, Paths.commands
  framesource.py         sous-titres intégrés, chemins sûrs pour ffmpeg
  player.py              points de départ, seek/set_start/reset, commande settings, remote_snapshot
  relayclient.py         put_state, commands, ack_command, CommandLog, sync_control, relay_loop
  runner.py              branche CommandLog
  relay/config.py        admin_password_hash, session_secret, command_ttl_seconds, state_max_bytes
  relay/security.py      jetons de session signés
  relay/store.py         tables device_state et commands
  relay/app.py           routes appareil (state, commands) et administrateur, /envoi/{id}
  relay/static/index.html  deux onglets, réglages
  relay/static/envoi.html  NOUVEAU  page de confirmation
  tui/model.py           film_rows (positions, départs), subtitle_track
  tui/app.py             NavTable, navigation, colonnes, touches t/g/z
deploy/update-pi.sh      NOUVEAU
docs/deploiement.md, README.md, deploy/relay/relay.env.example
tests/                   un fichier de test par module (voir chaque tâche)
```

---

### Task 1: Sondage des films (flux de sous-titres) et bibliothèque

**Files:**
- Modify: `slowmovie_inkdrop/videos.py`, `slowmovie_inkdrop/library.py`
- Test: `tests/test_videos.py`, `tests/test_library.py`

**Interfaces:**
- Produces:
  - `TEXT_SUBTITLE_CODECS: frozenset[str]`
  - `VideoInfo(fps, duration, frame_count, width, height, subtitle_codecs: tuple[str, ...] = ())` avec `.usable_subtitle_track(track: int) -> bool`
  - `parse_probe(probe)` : choisit le premier flux vidéo qui n'est pas une image jointe (`disposition.attached_pic`) et relève les codecs de tous les flux `codec_type == "subtitle"`
  - `probe_video(path)` sonde tous les flux
  - `has_progress(progress_dir, video) -> bool`
  - Entrées de `Library.rows()` : champs supplémentaires `fps: float`, `subtitle_codecs: list[str]`

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/test_videos.py` (ajouter `has_progress` à l'import existant depuis `slowmovie_inkdrop.videos`) :

```python
def test_parse_probe_collects_subtitle_streams_and_ignores_cover_art():
    probe = {"streams": [
        {"codec_type": "video", "avg_frame_rate": "0/0", "width": 600, "height": 900,
         "disposition": {"attached_pic": 1}},
        {"codec_type": "video", "avg_frame_rate": "24000/1001", "nb_frames": "2400",
         "width": 1920, "height": 1040, "disposition": {"attached_pic": 0}},
        {"codec_type": "audio", "codec_name": "ac3"},
        {"codec_type": "subtitle", "codec_name": "subrip"},
        {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"}],
        "format": {"duration": "100.1"}}
    info = parse_probe(probe)
    assert (info.width, info.height, info.frame_count) == (1920, 1040, 2400)
    assert info.subtitle_codecs == ("subrip", "hdmv_pgs_subtitle")
    assert info.usable_subtitle_track(0)
    assert not info.usable_subtitle_track(1)          # sous-titres image : non rendus
    assert not info.usable_subtitle_track(2) and not info.usable_subtitle_track(-1)


def test_a_film_without_subtitle_streams_has_no_usable_track():
    info = parse_probe(MP4)
    assert info.subtitle_codecs == () and not info.usable_subtitle_track(0)


def test_a_file_with_only_cover_art_is_rejected():
    probe = {"streams": [{"codec_type": "video", "avg_frame_rate": "0/0", "width": 1, "height": 1,
                          "disposition": {"attached_pic": 1}}], "format": {"duration": "1"}}
    with pytest.raises(ValueError):
        parse_probe(probe)


def test_has_progress(tmp_path):
    video = Path("/x/film.mp4")
    assert not has_progress(tmp_path, video)
    write_progress(tmp_path, video, 0)
    assert has_progress(tmp_path, video)
```

Ajouter à `tests/test_library.py` :

```python
def test_ready_entry_records_fps_and_subtitle_tracks(tmp_path):
    info = VideoInfo(24.0, 100.0, 2400, 1920, 1080, ("subrip",))
    vdir, lib = make(tmp_path, probe=lambda p: info)
    (vdir / "a.mp4").write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    row = lib.rows()[0]
    assert row["fps"] == 24.0 and row["subtitle_codecs"] == ["subrip"]


def test_ready_entry_saved_by_an_older_version_is_probed_again_without_waiting(tmp_path):
    vdir = tmp_path / "Videos"
    vdir.mkdir()
    f = vdir / "a.mp4"
    f.write_bytes(b"x")
    st = f.stat()
    (tmp_path / "library.json").write_text(json.dumps({"a.mp4": {
        "size": st.st_size, "mtime": st.st_mtime_ns, "status": "ready", "changed_at": 0,
        "frames": 5, "duration": 1.0}}))
    lib = Library(vdir, tmp_path / "library.json", lambda p: INFO, settle_seconds=10)
    lib.poll(1)                                   # pas d'attente de 10 s : le film était déjà prêt
    assert lib.rows()[0]["fps"] == 24.0 and lib.ready() == [f]
```

Ajouter aussi à `tests/test_videos.py` (un nom de film avec des crochets ou des étoiles ne doit pas casser la recherche) :

```python
def test_find_subtitles_survives_glob_characters_in_the_film_name(tmp_path):
    film = tmp_path / "Film [2013] * (v2).mkv"
    film.touch()
    (tmp_path / "Film [2013] * (v2).fr.srt").touch()
    (tmp_path / "Autre film.srt").touch()
    assert find_subtitles(film) == tmp_path / "Film [2013] * (v2).fr.srt"
    assert find_subtitles(tmp_path / "Inconnu.mkv") is None
```

- [ ] **Step 2: Lancer pour voir l'échec**

Run: `.venv/bin/pytest tests/test_videos.py tests/test_library.py -q`
Expected: FAIL (`has_progress` absent, `subtitle_codecs` absent, `find_subtitles` ne trouve pas le fichier)

- [ ] **Step 3: Implémenter `videos.py`**

Remplacer la classe `VideoInfo`, `parse_probe` et `probe_video`, et ajouter `TEXT_SUBTITLE_CODECS` et `has_progress` :

```python
TEXT_SUBTITLE_CODECS = frozenset({"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"})


@dataclass(frozen=True)
class VideoInfo:
    fps: float
    duration: float
    frame_count: int
    width: int
    height: int
    subtitle_codecs: tuple[str, ...] = ()

    @property
    def frame_time_ms(self) -> float:
        return 1000 / self.fps

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    def usable_subtitle_track(self, track: int) -> bool:
        """Vrai si la piste existe et contient des sous-titres texte (les sous-titres image ne sont pas rendus)."""
        return 0 <= track < len(self.subtitle_codecs) and self.subtitle_codecs[track] in TEXT_SUBTITLE_CODECS


def parse_probe(probe: dict) -> VideoInfo:
    try:
        streams = probe["streams"]
        videos = [s for s in streams if s.get("codec_type", "video") == "video"]
        real = [s for s in videos if not (s.get("disposition") or {}).get("attached_pic")]
        candidates = real or videos
        if not candidates:
            raise ValueError("aucun flux vidéo")
        stream = candidates[0]
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
        subtitles = tuple(str(s.get("codec_name", "")) for s in streams if s.get("codec_type") == "subtitle")
        return VideoInfo(fps, duration, frames, int(stream["width"]), int(stream["height"]), subtitles)
    except (KeyError, TypeError, ZeroDivisionError) as exc:
        raise ValueError(f"métadonnées vidéo inexploitables ({exc!r})") from exc


def probe_video(path: Path) -> VideoInfo:
    import ffmpeg  # import paresseux

    return parse_probe(ffmpeg.probe(str(path)))


def has_progress(progress_dir: Path, video: Path) -> bool:
    return _progress_file(progress_dir, video).is_file()


def find_subtitles(video: Path) -> Path | None:
    """Fichier de sous-titres voisin (`film.srt`, `film.fr.ass`…) ; sans glob : `[`, `*`, `?` sont courants dans les noms."""
    try:
        siblings = sorted(video.parent.iterdir())
    except OSError:
        return None
    for candidate in siblings:
        if candidate.name.startswith(video.stem + ".") and candidate.suffix.lower() in SUBTITLE_TYPES:
            return candidate
    return None
```

(`_progress_file` est défini plus bas dans le fichier : placer `has_progress` après lui. Remplacer l'ancienne `find_subtitles` par celle-ci.)

- [ ] **Step 4: Implémenter `library.py`**

Dans `Library.poll`, remplacer le bloc de sondage par :

```python
                due = entry["status"] == "pending" and now - entry["changed_at"] >= self.settle
                legacy = entry["status"] == "ready" and "fps" not in entry    # entrée d'une ancienne version
                if due or legacy:
                    try:
                        info = self.probe(self.videos_dir / name)
                    except Exception as exc:  # ffprobe : tout échec = film inutilisable
                        entry.update(status="error", error=str(exc)[:200])
                    else:
                        entry.update(status="ready", frames=info.frame_count, duration=info.duration,
                                     fps=info.fps, subtitle_codecs=list(info.subtitle_codecs))
```

- [ ] **Step 5: Lancer les tests**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: tout PASS (les anciens tests de `test_videos.py` et `test_library.py` restent verts)

- [ ] **Step 6: Commit**

```bash
git add slowmovie_inkdrop/videos.py slowmovie_inkdrop/library.py tests/test_videos.py tests/test_library.py
git commit -m "feat(videos): flux de sous-titres, fps en bibliothèque, ignorer les images jointes"
```

---

### Task 2: Points de départ et timecodes (`starts.py`)

**Files:**
- Create: `slowmovie_inkdrop/starts.py`
- Modify: `slowmovie_inkdrop/config.py` (`Paths.starts`, `Paths.commands`)
- Test: `tests/test_starts.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `parse_timecode(text: str) -> float` (accepte `ss`, `mm:ss`, `hh:mm:ss`, décimales ; lève `ValueError` en français)
  - `format_timecode(seconds: float) -> str` → `"hh:mm:ss"`
  - `seconds_to_frame(seconds, fps, frame_count) -> int` (borné à `[0, frame_count]`)
  - `frame_to_seconds(frame, fps) -> float`
  - `StartPoints(path: Path)` : `.get(name) -> float`, `.set(name, seconds)` (0 supprime l'entrée), `.all() -> dict[str, float]`
  - `Paths.starts -> base/"films.json"`, `Paths.commands -> base/"commands.json"`

- [ ] **Step 1: Écrire les tests**

`tests/test_starts.py` :

```python
import pytest

from slowmovie_inkdrop.starts import (
    StartPoints, format_timecode, frame_to_seconds, parse_timecode, seconds_to_frame,
)


@pytest.mark.parametrize("text, expected", [
    ("90", 90.0), ("1:30", 90.0), ("01:02:03", 3723.0), ("0:0:0", 0.0), ("  00:00:10.5 ", 10.5),
    ("2:00:00", 7200.0),
])
def test_parse_timecode(text, expected):
    assert parse_timecode(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "abc", "1:2:3:4", "-5", "1:60", "1:99:00", "nan", "inf", ":", "1::2"])
def test_parse_timecode_rejects_garbage_with_a_message(text):
    with pytest.raises(ValueError) as exc:
        parse_timecode(text)
    assert str(exc.value)


def test_format_timecode():
    assert format_timecode(0) == "00:00:00"
    assert format_timecode(3723.9) == "01:02:03"
    assert format_timecode(8233) == "02:17:13"


def test_seconds_and_frames_convert_and_clamp():
    assert seconds_to_frame(10, 24.0, 1000) == 240
    assert seconds_to_frame(5000, 24.0, 1000) == 1000
    assert seconds_to_frame(-3, 24.0, 1000) == 0
    assert frame_to_seconds(240, 24.0) == 10.0
    assert frame_to_seconds(5, 0) == 0.0


def test_start_points_roundtrip_and_remove_on_zero(tmp_path):
    sp = StartPoints(tmp_path / "films.json")
    assert sp.get("a.mkv") == 0.0 and sp.all() == {}
    sp.set("a.mkv", 12.5)
    assert StartPoints(tmp_path / "films.json").get("a.mkv") == 12.5       # persisté
    sp.set("a.mkv", 0)
    assert sp.all() == {}


def test_start_points_ignore_a_corrupt_file_and_negative_values(tmp_path):
    path = tmp_path / "films.json"
    path.write_text("{pas du json")
    sp = StartPoints(path)
    assert sp.all() == {}
    sp.set("a.mkv", -4)
    assert sp.get("a.mkv") == 0.0
```

Ajouter à `tests/test_config.py` :

```python
def test_paths_for_start_points_and_command_log(tmp_path):
    paths = Paths(tmp_path)
    assert paths.starts == tmp_path / "films.json" and paths.commands == tmp_path / "commands.json"
```

- [ ] **Step 2: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_starts.py tests/test_config.py -q` → FAIL

- [ ] **Step 3: Implémenter `starts.py`**

```python
"""Points de départ par film et conversions de timecodes."""
from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path


def parse_timecode(text: str) -> float:
    raw = text.strip()
    if not raw:
        raise ValueError("timecode vide : utilisez hh:mm:ss")
    parts = raw.split(":")
    if len(parts) > 3:
        raise ValueError("timecode invalide : utilisez hh:mm:ss")
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        raise ValueError("timecode invalide : utilisez hh:mm:ss") from None
    if any(not math.isfinite(n) or n < 0 for n in numbers):
        raise ValueError("timecode invalide : utilisez hh:mm:ss")
    if len(numbers) > 1 and any(n >= 60 for n in numbers[1:]):
        raise ValueError("les minutes et les secondes doivent être inférieures à 60")
    seconds = 0.0
    for n in numbers:
        seconds = seconds * 60 + n
    return seconds


def format_timecode(seconds: float) -> str:
    total = max(int(seconds), 0)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def seconds_to_frame(seconds: float, fps: float, frame_count: int) -> int:
    return int(min(max(seconds * fps, 0), frame_count))


def frame_to_seconds(frame: int, fps: float) -> float:
    return frame / fps if fps > 0 else 0.0


class StartPoints:
    """Point de départ (en secondes) de chaque film, dans `films.json`."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def all(self) -> dict[str, float]:
        with self._lock:
            return self._read()

    def _read(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text())
            return {str(k): float(v["start_seconds"]) for k, v in data.items() if float(v["start_seconds"]) > 0}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    def get(self, name: str) -> float:
        return self.all().get(name, 0.0)

    def set(self, name: str, seconds: float) -> None:
        with self._lock:
            current = self._read()
            if seconds > 0:
                current[name] = float(seconds)
            else:
                current.pop(name, None)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({k: {"start_seconds": v} for k, v in current.items()}))
            os.replace(tmp, self.path)
```

Dans `config.py`, ajouter à `Paths` :

```python
    @property
    def starts(self) -> Path:
        return self.base / "films.json"

    @property
    def commands(self) -> Path:
        return self.base / "commands.json"
```

- [ ] **Step 4: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .` → PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/starts.py slowmovie_inkdrop/config.py tests/test_starts.py tests/test_config.py
git commit -m "feat(starts): points de départ par film et conversions de timecodes"
```

---

### Task 3: Sous-titres intégrés (`subtitle_track`, `framesource.py`)

**Files:**
- Modify: `slowmovie_inkdrop/config.py` (`Settings.subtitle_track`), `slowmovie_inkdrop/framesource.py`
- Test: `tests/test_config.py`, `tests/test_framesource.py`

**Interfaces:**
- Consumes: `VideoInfo.usable_subtitle_track` (T1).
- Produces: `Settings.subtitle_track: int = 0` (validé : 0 à 50) ; `FfmpegFrameSource.frame(...)` rend, avec `settings.subtitles`, le fichier `.srt/.ass/.ssa` voisin s'il existe, **sinon** la piste intégrée `subtitle_track` si elle est de type texte ; sinon aucun sous-titre, sans erreur. Les chemins passés au filtre `subtitles` sont des liens symboliques sûrs dans le dossier de travail.

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/test_config.py` :

```python
@pytest.mark.parametrize("value", [-1, 51])
def test_subtitle_track_out_of_range_is_refused(tmp_path, value):
    with pytest.raises(ConfigError):
        Settings(subtitle_track=value).save(tmp_path / "x.conf")


def test_subtitle_track_defaults_to_the_first_one():
    assert Settings().subtitle_track == 0
```

Ajouter à `tests/test_framesource.py` (après les tests existants ; ajouter `from slowmovie_inkdrop.videos import VideoInfo` à l'import) :

```python
def _has_filter(name: str) -> bool:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return f" {name} " in out


needs_subtitles_filter = pytest.mark.skipif(not _has_filter("subtitles"), reason="filtre subtitles absent")
SRT = "1\n00:00:00,000 --> 00:00:04,000\nBONJOUR LE MONDE\n"


def _black_film(path, *, srt=None):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=24:d=4"]
    if srt is not None:
        srt_dir = path.parent / "montage"                 # pas à côté du film : il serait pris pour un fichier externe
        srt_dir.mkdir(exist_ok=True)
        srt_file = srt_dir / "s.srt"
        srt_file.write_text(srt, encoding="utf-8")
        cmd += ["-i", str(srt_file), "-map", "0:v", "-map", "1:s", "-c:s", "srt"]
    subprocess.run(cmd + ["-c:v", "mpeg4", str(path)], check=True)


def _brightest(img):
    return max(img.convert("L").getdata())


@needs_subtitles_filter
def test_embedded_text_subtitles_are_drawn_even_with_a_hostile_file_name(tmp_path):
    film = tmp_path / "Film (2013), v2 [x]; a'b.mkv"
    _black_film(film, srt=SRT)
    info = probe_video(film)
    assert info.subtitle_codecs == ("subrip",)
    src = FfmpegFrameSource(scratch=tmp_path)
    with_subs = src.frame(film, info, 24, Settings(subtitles=True), (800, 480))
    without = src.frame(film, info, 24, Settings(subtitles=False), (800, 480))
    assert _brightest(with_subs) > 200 and _brightest(without) < 30


@needs_subtitles_filter
def test_an_external_subtitle_file_wins_over_the_embedded_track(tmp_path):
    film = tmp_path / "film.mkv"
    _black_film(film, srt=SRT)
    film.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:04,000\nEXTERNE\n", encoding="utf-8")
    info = probe_video(film)
    img = FfmpegFrameSource(scratch=tmp_path).frame(film, info, 24, Settings(subtitles=True), (800, 480))
    assert _brightest(img) > 200


@needs_subtitles_filter
def test_a_film_without_any_subtitles_just_shows_the_picture(tmp_path):
    film = tmp_path / "plain.mkv"
    _black_film(film)
    info = probe_video(film)
    img = FfmpegFrameSource(scratch=tmp_path).frame(film, info, 24, Settings(subtitles=True), (800, 480))
    assert img.size == (800, 480) and _brightest(img) < 30


@needs_subtitles_filter
def test_image_subtitles_and_out_of_range_tracks_are_ignored_without_error(tmp_path):
    film = tmp_path / "film.mkv"
    _black_film(film, srt=SRT)
    real = probe_video(film)
    pgs = VideoInfo(real.fps, real.duration, real.frame_count, real.width, real.height,
                    ("hdmv_pgs_subtitle",))
    src = FfmpegFrameSource(scratch=tmp_path)
    assert _brightest(src.frame(film, pgs, 24, Settings(subtitles=True), (800, 480))) < 30
    assert _brightest(src.frame(film, real, 24, Settings(subtitles=True, subtitle_track=7), (800, 480))) < 30
```

- [ ] **Step 2: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_config.py tests/test_framesource.py -q` → FAIL

- [ ] **Step 3: Implémenter**

Dans `config.py`, ajouter le champ à `Settings` (juste après `timecode: bool = False`) :

```python
    subtitle_track: int = 0     # piste de sous-titres intégrée (parmi tous les flux de sous-titres)
```

et dans `problems()` :

```python
        check(0 <= self.subtitle_track <= 50, "subtitle_track", "doit être entre 0 et 50")
```

Remplacer `slowmovie_inkdrop/framesource.py` par :

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
        self.scratch = scratch or (shm if shm.is_dir() else Path(tempfile.gettempdir()))
        self.target = self.scratch / "slowmovie-frame.png"

    def _safe_path(self, path: Path, tag: str) -> str:
        """Lien au nom neutre : le filtre `subtitles` s'étrangle sur les parenthèses, virgules, crochets, apostrophes…"""
        link = self.scratch / f"slowmovie-{tag}{path.suffix.lower()}"
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(path.resolve())
        except OSError:
            return str(path)
        return str(link)

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
        subtitle_file = find_subtitles(video) if settings.subtitles else None
        embedded = (settings.subtitles and subtitle_file is None
                    and info.usable_subtitle_track(settings.subtitle_track))
        if subtitle_file:
            stream = stream.filter("subtitles", self._safe_path(subtitle_file, "external"))
        elif embedded:
            stream = stream.filter("subtitles", self._safe_path(video, "embedded"), si=settings.subtitle_track)
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

- [ ] **Step 4: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: PASS (les tests ffmpeg sont ignorés si le filtre `subtitles` manque ; le dire dans le ledger le cas échéant)

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/config.py slowmovie_inkdrop/framesource.py tests/test_config.py tests/test_framesource.py
git commit -m "feat(subtitles): sous-titres intégrés au film, chemins sûrs pour ffmpeg"
```

---

### Task 4: Contrat des commandes (`remote.py`) et lecteur (points de départ, seek, réglages)

**Files:**
- Create: `slowmovie_inkdrop/remote.py`
- Modify: `slowmovie_inkdrop/player.py`, `tests/conftest.py`
- Test: `tests/test_remote.py`, `tests/test_player.py`

**Interfaces:**
- Consumes: `StartPoints`, `seconds_to_frame` (T2) ; `has_progress`, `VideoInfo` (T1) ; `Settings` (T2/T3).
- Produces:
  - `remote.REMOTE_SETTINGS: tuple[str, ...]`, `remote.SETTING_TYPES`, `remote.COMMAND_KINDS`, `remote.FILM_COMMANDS`
  - `remote.check_setting_value(name, value)` → valeur typée ou `ValueError`
  - `remote.validate_command(payload: dict) -> dict` → `{"kind": ..., ...}` normalisé ou `ValueError`
  - `Player(..., starts: StartPoints | None = None)` ; attribut `.starts`
  - Commandes du lecteur : `seek {video, seconds}`, `set_start {video, seconds}`, `reset {video}` (retour au point de départ), `settings {changes}` ; réponses `{"ok": True}` ou `{"ok": False, "error": "..."}`
  - `FakeLibrary.rows()` dans `conftest.py`

- [ ] **Step 1: Écrire `tests/test_remote.py`**

```python
import pytest

from slowmovie_inkdrop.remote import (
    COMMAND_KINDS, REMOTE_SETTINGS, check_setting_value, validate_command,
)


def test_the_remote_setting_list_is_closed_and_excludes_dangerous_ones():
    for forbidden in ("relay_url", "relay_token", "epd", "directory", "file", "clear", "loglevel", "start"):
        assert forbidden not in REMOTE_SETTINGS
    for needed in ("delay", "increment", "subtitles", "subtitle_track", "contrast", "active_hours"):
        assert needed in REMOTE_SETTINGS


@pytest.mark.parametrize("name, value, expected", [
    ("delay", 40, 40), ("fullscreen", True, True), ("contrast", 1, 1.0), ("contrast", 1.5, 1.5),
    ("active_hours", " 08:00-22:00 ", "08:00-22:00"), ("relay_poll_seconds", 20, 20),
])
def test_valid_setting_values(name, value, expected):
    assert check_setting_value(name, value) == expected


@pytest.mark.parametrize("name, value", [
    ("delay", "40"), ("delay", True), ("delay", 1.5), ("delay", 10**9), ("fullscreen", 1), ("fullscreen", "true"),
    ("contrast", "1"), ("contrast", float("nan")), ("contrast", True), ("active_hours", 5),
    ("active_hours", "x" * 51), ("relay_poll_seconds", 4), ("relay_poll_seconds", 301),
    ("relay_url", "https://evil"), ("relay_token", "x"), ("epd", "x"), ("nimporte", 1),
])
def test_invalid_setting_values_are_refused(name, value):
    with pytest.raises(ValueError):
        check_setting_value(name, value)


def test_simple_commands():
    for kind in ("pause", "resume", "skip"):
        assert validate_command({"kind": kind, "ignored": 1}) == {"kind": kind}


def test_film_commands_are_normalised():
    assert validate_command({"kind": "play", "video": "a.mkv"}) == {"kind": "play", "video": "a.mkv"}
    assert validate_command({"kind": "seek", "video": "a.mkv", "seconds": 90}) == {
        "kind": "seek", "video": "a.mkv", "seconds": 90.0}
    assert validate_command({"kind": "set_start", "video": "Le film (2013).mkv", "seconds": 0}) == {
        "kind": "set_start", "video": "Le film (2013).mkv", "seconds": 0.0}
    assert validate_command({"kind": "reset", "video": "a.mkv"}) == {"kind": "reset", "video": "a.mkv"}


@pytest.mark.parametrize("payload", [
    None, [], "play", {}, {"kind": "dance"}, {"kind": "play"}, {"kind": "play", "video": ""},
    {"kind": "play", "video": "../x.mkv"}, {"kind": "play", "video": "/etc/passwd"},
    {"kind": "play", "video": "a\\b.mkv"}, {"kind": "play", "video": ".cache.mkv"},
    {"kind": "play", "video": "a\x00b.mkv"}, {"kind": "play", "video": "x" * 300}, {"kind": "play", "video": 5},
    {"kind": "seek", "video": "a.mkv"}, {"kind": "seek", "video": "a.mkv", "seconds": -1},
    {"kind": "seek", "video": "a.mkv", "seconds": "10"}, {"kind": "seek", "video": "a.mkv", "seconds": True},
    {"kind": "seek", "video": "a.mkv", "seconds": 10**9}, {"kind": "set_start", "video": "a.mkv", "seconds": float("nan")},
    {"kind": "settings"}, {"kind": "settings", "changes": {}}, {"kind": "settings", "changes": []},
    {"kind": "settings", "changes": {"relay_url": "https://evil"}},
    {"kind": "settings", "changes": {"delay": "40"}},
    {"kind": "settings", "changes": {f"delay{i}": 1 for i in range(30)}},
])
def test_invalid_commands_are_refused_with_a_message(payload):
    with pytest.raises(ValueError) as exc:
        validate_command(payload)
    assert str(exc.value)


def test_settings_command_is_normalised():
    cmd = validate_command({"kind": "settings", "changes": {"delay": 10, "contrast": 1}})
    assert cmd == {"kind": "settings", "changes": {"delay": 10, "contrast": 1.0}}


def test_command_kinds_cover_the_spec():
    assert set(COMMAND_KINDS) == {"pause", "resume", "skip", "play", "seek", "set_start", "reset", "settings"}
```

- [ ] **Step 2: Écrire les tests du lecteur**

Dans `tests/conftest.py`, ajouter à `FakeLibrary` :

```python
    def rows(self):
        return [{"name": v.name, "status": "ready", "frames": INFO.frame_count, "duration": INFO.duration,
                 "fps": INFO.fps, "subtitle_codecs": [], "error": ""} for v in self.videos]
```

Ajouter à `tests/test_player.py` (ajouter `import pytest`, `from slowmovie_inkdrop.config import Settings` et `has_progress` aux imports) :

```python
def test_first_play_starts_at_the_film_start_point(make_player):
    ctx = make_player()
    ctx.player.starts.set("a.mp4", 10)            # 10 s à 24 i/s = image 240
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 240)]


def test_saved_progress_wins_over_the_start_point(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 500)
    ctx = make_player()
    ctx.player.starts.set("a.mp4", 10)
    ctx.player.tick()
    assert ctx.frames.calls == [("a.mp4", 500)]


def test_set_start_does_not_move_a_film_already_started(make_player):
    ctx = make_player()
    ctx.player.tick()                                           # image 0 affichée, progression = 4
    fut = ctx.player.submit({"cmd": "set_start", "video": "a.mp4", "seconds": 30})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.player.starts.get("a.mp4") == 30
    assert ctx.player.frame == 4 and read_progress(ctx.paths.progress, ctx.library.videos[0]) == 4


def test_set_start_beyond_the_film_is_refused(make_player):
    ctx = make_player()
    fut = ctx.player.submit({"cmd": "set_start", "video": "a.mp4", "seconds": 5000})   # durée 100 s
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False and ctx.player.starts.get("a.mp4") == 0.0


def test_seek_jumps_now_and_is_saved(make_player):
    ctx = make_player()
    ctx.player.tick()
    fut = ctx.player.submit({"cmd": "seek", "video": "a.mp4", "seconds": 20})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.frames.calls[-1] == ("a.mp4", 480)
    assert read_progress(ctx.paths.progress, ctx.library.videos[0]) == 484


def test_seek_past_the_end_is_clamped(make_player):
    ctx = make_player()
    ctx.player.tick()
    ctx.player.submit({"cmd": "seek", "video": "a.mp4", "seconds": 5000})
    ctx.player.tick()
    assert ctx.frames.calls[-1] == ("a.mp4", 1000)


def test_seek_on_another_film_only_moves_its_saved_position(make_player):
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.tick()
    ctx.player.submit({"cmd": "seek", "video": "b.mp4", "seconds": 10})
    ctx.player.tick()
    assert read_progress(ctx.paths.progress, ctx.library.videos[1]) == 240
    assert ctx.player.video.name == "a.mp4" and ctx.frames.calls[-1][0] == "a.mp4"


def test_seek_unknown_film_is_refused(make_player):
    ctx = make_player()
    fut = ctx.player.submit({"cmd": "seek", "video": "inconnu.mp4", "seconds": 10})
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False


def test_reset_goes_back_to_the_start_point(make_player):
    ctx = make_player()
    ctx.player.starts.set("a.mp4", 10)
    ctx.player.tick()
    ctx.player.submit({"cmd": "seek", "video": "a.mp4", "seconds": 20})
    ctx.player.tick()
    assert ctx.frames.calls[-1] == ("a.mp4", 480)
    fut = ctx.player.submit({"cmd": "reset", "video": "a.mp4"})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    assert ctx.frames.calls[-1] == ("a.mp4", 240)


def test_loop_restart_uses_the_start_point(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 998)
    ctx = make_player(loop=True)
    ctx.player.starts.set("a.mp4", 10)
    ctx.player.tick()                                            # affiche 998, dépasse la fin, repart au départ
    assert read_progress(ctx.paths.progress, ctx.library.videos[0]) == 240


def test_next_film_starts_at_its_own_start_point(make_player, tmp_path):
    (tmp_path / "progress").mkdir(exist_ok=True)
    write_progress(tmp_path / "progress", tmp_path / "Videos" / "a.mp4", 998)
    ctx = make_player(videos=("a.mp4", "b.mp4"))
    ctx.player.starts.set("b.mp4", 5)
    ctx.player.tick()
    assert ctx.paths.now_playing.read_text().endswith("b.mp4")
    assert read_progress(ctx.paths.progress, tmp_path / "Videos" / "b.mp4") == 120


def test_remote_settings_change_is_validated_saved_and_applied(make_player):
    ctx = make_player()
    ctx.paths.conf.write_text("relay-token = secret\ndelay = 40\n")
    fut = ctx.player.submit({"cmd": "settings", "changes": {
        "delay": 10, "fullscreen": True, "contrast": 1.5, "active_hours": "08:00-22:00", "queue_max": 3}})
    ctx.player.tick()
    assert fut.result(timeout=1) == {"ok": True}
    saved = Settings.load(ctx.paths.conf)
    assert (saved.delay, saved.fullscreen, saved.contrast, saved.active_hours) == (10, True, 1.5, "08:00-22:00")
    assert saved.relay_token == "secret"                          # les réglages non touchés sont conservés
    assert ctx.player.settings.delay == 10 and ctx.queue.max_items == 3


@pytest.mark.parametrize("changes", [
    {"relay_url": "https://evil"}, {"relay_token": "x"}, {"epd": "x"}, {"directory": "/"},
    {"delay": "10"}, {"delay": True}, {"delay": 0}, {"delay": 1.5}, {"fullscreen": 1}, {"contrast": 9},
    {"active_hours": "n'importe quoi"}, {"relay_poll_seconds": 1000}, {}, {"subtitle_track": -1},
    {"min_image_seconds": 500, "max_image_seconds": 100},
])
def test_invalid_remote_settings_are_refused_and_nothing_is_written(make_player, changes):
    ctx = make_player()
    ctx.paths.conf.write_text("delay = 40\n")
    before = ctx.paths.conf.read_text()
    fut = ctx.player.submit({"cmd": "settings", "changes": changes})
    ctx.player.tick()
    assert fut.result(timeout=1)["ok"] is False
    assert ctx.paths.conf.read_text() == before and ctx.player.settings.delay == 120
```

- [ ] **Step 3: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_remote.py tests/test_player.py -q` → FAIL

- [ ] **Step 4: Implémenter `remote.py`**

```python
"""Contrat des commandes envoyées du relais au Pi : le relais valide la forme, le Pi revalide tout."""
from __future__ import annotations

import math

REMOTE_SETTINGS = (
    "delay", "increment", "random_file", "random_frames", "loop", "fullscreen", "subtitles", "timecode",
    "subtitle_track", "contrast", "min_image_seconds", "max_image_seconds", "queue_max",
    "queue_expiry_seconds", "caption_max_chars", "active_hours", "relay_poll_seconds",
)
SETTING_TYPES: dict[str, type] = {
    "delay": int, "increment": int, "random_file": bool, "random_frames": bool, "loop": bool,
    "fullscreen": bool, "subtitles": bool, "timecode": bool, "subtitle_track": int, "contrast": float,
    "min_image_seconds": int, "max_image_seconds": int, "queue_max": int, "queue_expiry_seconds": int,
    "caption_max_chars": int, "active_hours": str, "relay_poll_seconds": int,
}
SIMPLE_COMMANDS = ("pause", "resume", "skip")
FILM_COMMANDS = ("play", "seek", "set_start", "reset")
COMMAND_KINDS = SIMPLE_COMMANDS + FILM_COMMANDS + ("settings",)
POLL_RANGE = (5, 300)
MAX_INT = 10_000_000
MAX_SECONDS = 2 * 24 * 3600
MAX_CHANGES = 20


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def check_setting_value(name: str, value):
    """Valeur typée pour un réglage modifiable à distance, ou ValueError (message en français)."""
    if name not in SETTING_TYPES:
        raise ValueError(f"réglage non modifiable à distance : {name}")
    kind = SETTING_TYPES[name]
    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{name} : vrai ou faux attendu")
        return value
    if kind is str:
        if not isinstance(value, str) or len(value) > 50:
            raise ValueError(f"{name} : texte de 50 caractères au plus attendu")
        return value.strip()
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_INT:
            raise ValueError(f"{name} : nombre entier raisonnable attendu")
        number = value
    else:
        if not _is_number(value) or abs(value) > MAX_INT:
            raise ValueError(f"{name} : nombre attendu")
        number = float(value)
    if name == "relay_poll_seconds" and not POLL_RANGE[0] <= number <= POLL_RANGE[1]:
        raise ValueError("relay_poll_seconds : entre 5 et 300")
    return number


def validate_command(payload) -> dict:
    """Commande normalisée `{"kind": ..., ...}` ou ValueError."""
    if not isinstance(payload, dict):
        raise ValueError("commande invalide")
    kind = payload.get("kind")
    if kind not in COMMAND_KINDS:
        raise ValueError(f"commande inconnue : {kind!r}")
    if kind in SIMPLE_COMMANDS:
        return {"kind": kind}
    if kind == "settings":
        changes = payload.get("changes")
        if not isinstance(changes, dict) or not changes or len(changes) > MAX_CHANGES:
            raise ValueError("aucun réglage à modifier")
        return {"kind": kind, "changes": {n: check_setting_value(n, v) for n, v in changes.items()}}
    video = payload.get("video")
    if (not isinstance(video, str) or not video or len(video) > 255 or video.startswith(".")
            or any(c in video for c in "/\\\x00")):
        raise ValueError("nom de film invalide")
    out = {"kind": kind, "video": video}
    if kind in ("seek", "set_start"):
        seconds = payload.get("seconds")
        if not _is_number(seconds) or not 0 <= seconds <= MAX_SECONDS:
            raise ValueError("position invalide")
        out["seconds"] = float(seconds)
    return out
```

- [ ] **Step 5: Implémenter dans `player.py`**

Imports à ajouter : `from .remote import check_setting_value` et `from .starts import StartPoints, seconds_to_frame`, et `has_progress` dans l'import de `.videos`.

Dans `__init__`, avant `self._restore()` : `self.starts = starts or StartPoints(paths.starts)` ; ajouter `starts: StartPoints | None = None` à la signature (après `probe=probe_video`).

Ajouter les méthodes :

```python
    def _info(self, video: Path) -> VideoInfo:
        info = self._infos.get(video) or self.probe(video)
        self._infos[video] = info
        return info

    def _start_frame(self, video: Path) -> int:
        info = self._info(video)
        return seconds_to_frame(self.starts.get(video.name), info.fps, info.frame_count)

    def _ready_video(self, name) -> Path | None:
        return next((v for v in self.library.ready() if v.name == name), None)
```

Dans `_load_video`, remplacer la lecture de la progression par :

```python
        info = self._info(video)
        self.video, self.info = video, info
        if self.settings.random_frames:
            self.frame = 0
        else:
            if has_progress(self.paths.progress, video):
                frame = read_progress(self.paths.progress, video)
            else:
                frame = seconds_to_frame(self.starts.get(video.name), info.fps, info.frame_count)
            if self._start_pending:
                frame, self._start_pending = self.settings.start, False
            self.frame = int(_clamp(frame, 0, info.frame_count))
```

Dans `_advance`, remplacer les deux `self.frame = 0` du dépassement de fin par :

```python
            if self.frame > self.info.frame_count:
                if not s.loop:
                    following = (self.rng.choice(videos) if s.random_file
                                 else next_video(videos, self.video))
                    self._load_video(following)
                self.frame = self._start_frame(self.video)
```

Dans `_handle`, remplacer la branche `reset` et ajouter `seek`, `set_start`, `settings` :

```python
        elif name in ("seek", "set_start", "reset"):
            target = self._ready_video(cmd.get("video"))
            if target is None:
                return {"ok": False, "error": "film introuvable ou pas encore prêt"}
            info = self._info(target)
            if name == "set_start":
                seconds = float(cmd.get("seconds", 0))
                if not 0 <= seconds <= info.duration:
                    return {"ok": False, "error": "le point de départ dépasse la durée du film"}
                self.starts.set(target.name, seconds)
            else:
                if name == "seek":
                    frame = seconds_to_frame(float(cmd.get("seconds", 0)), info.fps, info.frame_count)
                else:
                    frame = self._start_frame(target)
                write_progress(self.paths.progress, target, frame)
                if target == self.video:
                    self.frame = frame
                    self.next_frame_at = 0.0
        elif name == "settings":
            return self._apply_remote_settings(cmd.get("changes"))
```

et la méthode :

```python
    def _apply_remote_settings(self, changes) -> dict:
        if not isinstance(changes, dict) or not changes:
            return {"ok": False, "error": "aucun réglage à modifier"}
        try:
            checked = {n: check_setting_value(n, v) for n, v in changes.items()}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        new = Settings.load(self.paths.conf)
        for name, value in checked.items():
            setattr(new, name, value)
        problems = new.problems()
        if problems:
            return {"ok": False, "error": f"{problems[0][0]} {problems[0][1]}"}
        new.save(self.paths.conf)
        self.settings = new
        self.queue.max_items = new.queue_max
        self.queue.expiry_seconds = new.queue_expiry_seconds
        return {"ok": True}
```

(L'ancienne branche `reset` de `_handle`, qui écrivait 0, est supprimée : remplacée par la branche commune ci-dessus.)

- [ ] **Step 6: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .` → PASS

- [ ] **Step 7: Commit**

```bash
git add slowmovie_inkdrop/remote.py slowmovie_inkdrop/player.py tests/conftest.py tests/test_remote.py tests/test_player.py
git commit -m "feat(player): points de départ, seek, set_start, réglages à distance validés"
```

---

### Task 5: Instantané du Pi (`Player.remote_snapshot`)

**Files:**
- Modify: `slowmovie_inkdrop/player.py`
- Test: `tests/test_player.py`

**Interfaces:**
- Consumes: `Library.rows()` avec `fps`, `subtitle_codecs` (T1) ; `StartPoints.all()` (T2) ; `REMOTE_SETTINGS` (T4).
- Produces: `Player.remote_snapshot(now: float) -> dict` :

```
{ "sent_at", "mode", "paused", "video", "position_seconds", "duration_seconds", "error",
  "queue": [{id, duration, caption}], "settings": {<REMOTE_SETTINGS>},
  "films": [{name, status, duration_seconds, fps, position_seconds, start_seconds, error, subtitle_codecs}] }
```

- [ ] **Step 1: Écrire les tests**

```python
def test_remote_snapshot_describes_the_player_without_secrets(make_player):
    import json

    ctx = make_player(videos=("a.mp4", "b.mp4"), relay_token="jeton-secret", relay_url="https://x.test")
    ctx.player.starts.set("b.mp4", 12)
    ctx.queue.add(A, png_bytes(), 90, ctx.clock(), caption="Bonjour")
    ctx.player.tick()                       # affiche d'abord l'image envoyée
    snap = ctx.player.remote_snapshot(ctx.clock())
    assert snap["mode"] == "IMAGE" and snap["paused"] is False
    assert snap["queue"] == [] and snap["video"] is None
    ctx.clock.advance(120)
    ctx.player.tick()                       # puis le film
    snap = ctx.player.remote_snapshot(ctx.clock())
    assert snap["video"] == "a.mp4" and snap["duration_seconds"] == 100.0 and snap["position_seconds"] > 0
    films = {f["name"]: f for f in snap["films"]}
    assert films["b.mp4"]["start_seconds"] == 12 and films["a.mp4"]["fps"] == 24.0
    assert films["a.mp4"]["status"] == "ready" and films["a.mp4"]["subtitle_codecs"] == []
    text = json.dumps(snap)
    assert "jeton-secret" not in text and "relay_url" not in snap["settings"] and "x.test" not in text
    assert snap["settings"]["delay"] == 120 and "subtitle_track" in snap["settings"]


def test_remote_snapshot_lists_the_waiting_images_with_their_captions(make_player):
    ctx = make_player(videos=())
    ctx.queue.add(A, png_bytes(), 90, ctx.clock(), caption="Texte")
    snap = ctx.player.remote_snapshot(ctx.clock())
    assert snap["queue"] == [{"id": A, "duration": 90, "caption": "Texte"}]


def test_remote_snapshot_stays_small_with_many_films(make_player):
    import json
    names = tuple(f"film numéro {i} avec un nom assez long pour faire du volume.mkv" for i in range(200))
    ctx = make_player(videos=names)
    assert len(json.dumps(ctx.player.remote_snapshot(ctx.clock()))) < 64 * 1024
```

- [ ] **Step 2: Lancer pour voir l'échec** — FAIL (`remote_snapshot` absent)

- [ ] **Step 3: Implémenter**

Import : `from .remote import REMOTE_SETTINGS, check_setting_value`. Ajouter à `Player` :

```python
    def remote_snapshot(self, now: float) -> dict:
        """Instantané envoyé au relais : ni jeton, ni adresse du relais."""
        s, info = self.settings, self.info
        starts = self.starts.all()
        films = []
        for row in self.library.rows():
            fps = row.get("fps") or 0.0
            frame = read_progress(self.paths.progress, self.paths.videos_dir(s) / row["name"])
            films.append({
                "name": row["name"], "status": row["status"], "duration_seconds": row.get("duration"),
                "fps": fps, "position_seconds": round(frame / fps, 1) if fps else 0.0,
                "start_seconds": starts.get(row["name"], 0.0), "error": row.get("error", ""),
                "subtitle_codecs": row.get("subtitle_codecs", []),
            })
        return {
            "sent_at": now, "mode": self.mode, "paused": self.paused,
            "video": self.video.name if self.video else None,
            "position_seconds": round(self.frame / info.fps, 1) if info else 0.0,
            "duration_seconds": info.duration if info else None,
            "error": self.error,
            "queue": [{"id": i.id, "duration": i.duration, "caption": i.caption} for i in self.queue.items()],
            "settings": {name: getattr(s, name) for name in REMOTE_SETTINGS},
            "films": films,
        }
```

- [ ] **Step 4: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .` → PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/player.py tests/test_player.py
git commit -m "feat(player): instantané distant sans secrets"
```

---

### Task 6: Relais — configuration, sessions signées, stockage

**Files:**
- Modify: `slowmovie_inkdrop/relay/config.py`, `slowmovie_inkdrop/relay/security.py`, `slowmovie_inkdrop/relay/store.py`
- Test: `tests/test_relay_security.py`, `tests/test_relay_store.py` (nouveau)

**Interfaces:**
- Produces:
  - `RelayConfig(..., admin_password_hash: str = "", session_secret: str = "", command_ttl_seconds: int = 600, state_max_bytes: int = 65536, session_seconds: int = 28800)` ; variables `SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH`, `SLOWMOVIE_RELAY_SESSION_SECRET`
  - `make_session_token(secret: bytes, now: float, ttl: float) -> str` ; `verify_session_token(secret: bytes, token: str, now: float) -> bool`
  - `Store.put_state(body: str, now: float)`, `Store.get_state() -> tuple[dict, float] | None`
  - `Store.add_command(command_id, kind, payload: dict, now)`, `Store.pending_commands(now, ttl) -> list[dict]` (`id, kind, payload`), `Store.expire_commands(now, ttl)`, `Store.ack_command(command_id, status, message, now) -> bool`, `Store.get_command(command_id) -> dict | None` (`id, kind, status, message, created_at`), `Store.recent_commands(limit) -> list[dict]`, `Store.pending_count() -> int`

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/test_relay_security.py` :

```python
def test_session_token_roundtrip_expiry_and_tampering():
    from slowmovie_inkdrop.relay.security import make_session_token, verify_session_token
    secret = b"s" * 32
    token = make_session_token(secret, now=1000.0, ttl=100.0)
    assert verify_session_token(secret, token, now=1050.0)
    assert not verify_session_token(secret, token, now=1101.0)               # expiré
    assert not verify_session_token(b"autre" * 8, token, now=1050.0)         # autre clé
    expiry, nonce, sig = token.split(".")
    assert not verify_session_token(secret, f"{int(expiry) + 999}.{nonce}.{sig}", now=1050.0)   # falsifié
    for bad in ("", "x", "a.b", "a.b.c.d", "abc.def.ghi", ".."):
        assert not verify_session_token(secret, bad, now=1050.0)
    assert make_session_token(secret, 1000.0, 100.0) != token                  # aléa différent
```

`tests/test_relay_store.py` :

```python
import json

from slowmovie_inkdrop.relay.store import Store


def test_state_is_replaced_and_timestamped(tmp_path):
    store = Store(tmp_path)
    assert store.get_state() is None
    store.put_state(json.dumps({"video": "a.mkv"}), now=10.0)
    store.put_state(json.dumps({"video": "b.mkv"}), now=20.0)
    assert store.get_state() == ({"video": "b.mkv"}, 20.0)


def test_command_lifecycle(tmp_path):
    store = Store(tmp_path)
    store.add_command("c1", "play", {"kind": "play", "video": "a.mkv"}, now=100.0)
    store.add_command("c2", "pause", {"kind": "pause"}, now=101.0)
    assert [c["id"] for c in store.pending_commands(now=110.0, ttl=600)] == ["c1", "c2"]
    assert store.pending_commands(110.0, 600)[0]["payload"] == {"kind": "play", "video": "a.mkv"}
    assert store.ack_command("c1", "applied", "", now=111.0) is True
    assert store.ack_command("c1", "failed", "", now=112.0) is False          # un accusé ne compte qu'une fois
    assert store.get_command("c1")["status"] == "applied"
    assert [c["id"] for c in store.pending_commands(115.0, 600)] == ["c2"]
    assert store.pending_count() == 1


def test_commands_expire_and_are_never_offered_late(tmp_path):
    store = Store(tmp_path)
    store.add_command("c1", "pause", {"kind": "pause"}, now=0.0)
    assert store.pending_commands(now=601.0, ttl=600) == []
    store.expire_commands(now=601.0, ttl=600)
    assert store.get_command("c1")["status"] == "expired"
    assert store.ack_command("c1", "applied", "", now=602.0) is False


def test_unknown_command_and_recent_list(tmp_path):
    store = Store(tmp_path)
    assert store.get_command("nope") is None and store.ack_command("nope", "applied", "", 1.0) is False
    for i in range(15):
        store.add_command(f"c{i:02d}", "pause", {"kind": "pause"}, now=float(i))
    recent = store.recent_commands(10)
    assert len(recent) == 10 and recent[0]["id"] == "c14"                       # plus récente d'abord


def test_message_is_truncated(tmp_path):
    store = Store(tmp_path)
    store.add_command("c1", "pause", {"kind": "pause"}, now=0.0)
    store.ack_command("c1", "failed", "x" * 1000, now=1.0)
    assert len(store.get_command("c1")["message"]) <= 300
```

- [ ] **Step 2: Lancer pour voir l'échec** — FAIL

- [ ] **Step 3: Implémenter**

`relay/config.py` : ajouter les champs (après `lease_seconds`) :

```python
    admin_password_hash: str = ""   # vide : l'administration est désactivée
    session_secret: str = ""        # vide : clé aléatoire à chaque démarrage
    command_ttl_seconds: int = 600
    state_max_bytes: int = 65536
    session_seconds: int = 28800
```

et dans `from_env` (arguments du constructeur) :

```python
            admin_password_hash=env.get("SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH", ""),
            session_secret=env.get("SLOWMOVIE_RELAY_SESSION_SECRET", ""),
```

`relay/security.py` : ajouter (imports `secrets`, `time` non requis) :

```python
def make_session_token(secret: bytes, now: float, ttl: float) -> str:
    body = f"{int(now + ttl)}.{secrets.token_hex(8)}"
    signature = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_session_token(secret: bytes, token: str, now: float) -> bool:
    try:
        expiry, nonce, signature = token.split(".")
        expected = hmac.new(secret, f"{expiry}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and int(expiry) > now
    except (ValueError, AttributeError):
        return False
```

(ajouter `import secrets` en tête du fichier).

`relay/store.py` : dans `__init__`, après la création de `items`, créer les deux tables :

```python
            self._db.execute("CREATE TABLE IF NOT EXISTS device_state (id INTEGER PRIMARY KEY CHECK (id = 1), "
                             "body TEXT NOT NULL, received_at REAL NOT NULL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                             "payload TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', "
                             "created_at REAL NOT NULL, done_at REAL)")
```

et les méthodes (ajouter `import json` en tête) :

```python
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
            self._db.commit()

    def ack_command(self, command_id: str, status: str, message: str, now: float) -> bool:
        with self._lock:
            cursor = self._db.execute("UPDATE commands SET status = ?, message = ?, done_at = ? "
                                      "WHERE id = ? AND status = 'pending'",
                                      (status, message[:300], now, command_id))
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
```

- [ ] **Step 4: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .` → PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/relay tests/test_relay_security.py tests/test_relay_store.py
git commit -m "feat(relay): configuration administrateur, sessions signées, stockage de l'état et des commandes"
```

---

### Task 7: Relais — routes de l'appareil et de l'administrateur

**Files:**
- Modify: `slowmovie_inkdrop/relay/app.py`
- Test: `tests/test_relay_admin.py` (nouveau), `tests/test_relay_api.py` (la clé `admin` de `/api/config`)

**Interfaces:**
- Consumes: `validate_command`, `FILM_COMMANDS` (T4) ; `Store.*` et `make_session_token`/`verify_session_token` (T6).
- Produces (JSON) :
  - `GET /api/config` → ajoute `"admin": bool`
  - Appareil (`Authorization: Bearer <jeton>`) : `PUT /api/device/state` (corps JSON objet ≤ `state_max_bytes`) → `{"ok": true}` ; `GET /api/device/commands` → `[{"id", "command": {...}}]` ; `POST /api/device/commands/{id}/ack` `{"status": "applied"|"failed", "message": ""}` → `{"ok": true}` (404 si inconnu ou déjà traité)
  - Administrateur : `POST /api/admin/login` `{"password"}` → `{"ok": true}` + cookie ; `POST /api/admin/logout` ; `GET /api/admin/state` → `{"online", "last_seen_seconds", "state", "commands"}` ; `POST /api/admin/commands` → 202 `{"id", "online"}` ; `GET /api/admin/commands/{id}` → `{"status", "message"}`
  - Erreurs : 503 administration désactivée ; 403 en-tête `X-Requested-With: slowmovie` manquant ; 401 cookie absent/invalide/expiré ou mot de passe faux ; 429 blocage ; 422 commande invalide ; 413 corps trop gros

- [ ] **Step 1: Écrire `tests/test_relay_admin.py`**

```python
import json

import pytest
from fastapi.testclient import TestClient

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password

ADMIN_PASSWORD = "phrase administrateur solide"
UPLOAD_PASSWORD = "phrase d'envoi"
TOKEN = "t" * 40
DEVICE = {"Authorization": f"Bearer {TOKEN}"}
XRW = {"X-Requested-With": "slowmovie"}
STATE = {"mode": "FILM", "video": "a.mkv", "settings": {"delay": 40},
         "films": [{"name": "a.mkv", "status": "ready"}, {"name": "<img src=x onerror=alert(1)>.mkv", "status": "ready"}]}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def make(tmp_path, **overrides):
    clock = Clock()
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password(UPLOAD_PASSWORD), device_token=TOKEN,
                         admin_password_hash=hash_password(ADMIN_PASSWORD), session_secret="k" * 32, **overrides)
    return TestClient(create_app(config, clock=clock), base_url="https://testserver"), clock, config


@pytest.fixture
def env(tmp_path):
    return make(tmp_path)


def login(client, password=ADMIN_PASSWORD):
    return client.post("/api/admin/login", json={"password": password}, headers=XRW)


def logged_in(env):
    client, clock, config = env
    assert login(client).status_code == 200
    return client, clock, config


def put_state(client, state=STATE):
    return client.put("/api/device/state", content=json.dumps(state), headers=DEVICE)


def test_admin_is_disabled_without_a_password_hash(tmp_path):
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password("x"), device_token=TOKEN)
    client = TestClient(create_app(config), base_url="https://testserver")
    assert client.get("/api/config").json()["admin"] is False
    assert client.post("/api/admin/login", json={"password": "x"}, headers=XRW).status_code == 503
    assert client.get("/api/admin/state", headers=XRW).status_code == 503


def test_config_announces_the_admin_tab(env):
    client, *_ = env
    assert client.get("/api/config").json()["admin"] is True


def test_login_sets_a_hardened_cookie(env):
    client, *_ = env
    r = login(client)
    assert r.status_code == 200 and r.json() == {"ok": True}
    cookie = r.headers["set-cookie"].lower()
    for attribute in ("sm_admin=", "httponly", "secure", "samesite=strict", "path=/api/admin", "max-age=28800"):
        assert attribute in cookie


def test_wrong_password_and_the_upload_password_are_refused(env):
    client, *_ = env
    assert login(client, "faux").status_code == 401
    assert login(client, UPLOAD_PASSWORD).status_code == 401           # le mot de passe d'envoi n'ouvre rien
    assert login(client, "").status_code == 401


def test_login_requires_the_custom_header(env):
    client, *_ = env
    assert client.post("/api/admin/login", json={"password": ADMIN_PASSWORD}).status_code == 403


def test_lockout_after_five_failures_is_separate_from_uploads(env):
    client, clock, _ = env
    for _ in range(5):
        login(client, "faux")
    r = login(client)
    assert r.status_code == 429 and "minute" in r.json()["detail"]
    clock.t += 901
    assert login(client).status_code == 200


def test_upload_failures_do_not_lock_the_admin_out(env):
    client, *_ = env
    for _ in range(5):
        client.post("/api/send", data={"password": "faux", "duration": "60"},
                    files={"image": ("a.png", b"x", "image/png")})
    assert login(client).status_code == 200


def test_password_is_never_logged(env, caplog):
    import logging
    client, *_ = env
    with caplog.at_level(logging.DEBUG):
        login(client, "Secret-administrateur-à-ne-pas-logger")
        login(client)
    assert "Secret-administrateur" not in caplog.text and ADMIN_PASSWORD not in caplog.text


def test_admin_routes_need_the_cookie_and_the_header(env):
    client, *_ = env
    assert client.get("/api/admin/state", headers=XRW).status_code == 401          # pas de cookie
    login(client)
    assert client.get("/api/admin/state").status_code == 403                        # cookie sans en-tête (CSRF)
    assert client.get("/api/admin/state", headers=XRW).status_code == 200


def test_tampered_or_expired_cookies_are_refused(env):
    client, clock, config = env
    login(client)
    good = client.cookies.get("sm_admin")
    client.cookies.set("sm_admin", good[:-3] + "abc", domain="testserver", path="/api/admin")
    assert client.get("/api/admin/state", headers=XRW).status_code == 401
    client.cookies.clear()
    login(client)
    clock.t += config.session_seconds + 1
    assert client.get("/api/admin/state", headers=XRW).status_code == 401


def test_logout_clears_the_session(env):
    client, *_ = logged_in(env)
    assert client.post("/api/admin/logout", headers=XRW).status_code == 200
    assert client.get("/api/admin/state", headers=XRW).status_code == 401


def test_device_endpoints_still_need_the_device_token(env):
    client, *_ = env
    assert client.put("/api/device/state", content="{}").status_code == 401
    assert client.get("/api/device/commands").status_code == 401
    assert client.post("/api/device/commands/" + "a" * 32 + "/ack", json={"status": "applied"}).status_code == 401
    login(client)
    assert client.get("/api/device/commands").status_code == 401          # le cookie administrateur n'ouvre pas l'appareil


def test_state_is_stored_and_shown_to_the_admin_with_the_online_flag(env):
    client, clock, _ = logged_in(env)
    assert client.get("/api/admin/state", headers=XRW).json()["online"] is False
    assert put_state(client).status_code == 200
    body = client.get("/api/admin/state", headers=XRW).json()
    assert body["online"] is True and body["state"]["video"] == "a.mkv" and body["last_seen_seconds"] == 0
    clock.t += 61
    assert client.get("/api/admin/state", headers=XRW).json()["online"] is False


def test_hostile_strings_are_returned_verbatim_as_json_data(env):
    client, *_ = logged_in(env)
    put_state(client)
    r = client.get("/api/admin/state", headers=XRW)
    assert r.headers["content-type"].startswith("application/json")
    assert any(f["name"].startswith("<img") for f in r.json()["state"]["films"])


@pytest.mark.parametrize("content", ["pas du json", "[1, 2]", "42", '"texte"'])
def test_invalid_state_is_refused(env, content):
    client, *_ = env
    assert client.put("/api/device/state", content=content, headers=DEVICE).status_code == 422


def test_oversized_state_is_refused_even_when_chunked(env):
    client, *_ = env
    big = json.dumps({"x": "y" * 70000})
    assert client.put("/api/device/state", content=big, headers=DEVICE).status_code == 413
    chunked = client.put("/api/device/state", content=iter([big.encode()[:30000], big.encode()[30000:]]),
                         headers=DEVICE)
    assert chunked.status_code == 413


def command(client, payload):
    return client.post("/api/admin/commands", json=payload, headers=XRW)


def test_a_valid_command_is_queued_and_offered_to_the_device(env):
    client, *_ = logged_in(env)
    put_state(client)
    r = command(client, {"kind": "play", "video": "a.mkv"})
    assert r.status_code == 202 and len(r.json()["id"]) == 32 and r.json()["online"] is True
    offered = client.get("/api/device/commands", headers=DEVICE).json()
    assert offered == [{"id": r.json()["id"], "command": {"kind": "play", "video": "a.mkv"}}]


@pytest.mark.parametrize("payload", [
    {"kind": "dance"}, {"kind": "play", "video": "inconnu.mkv"}, {"kind": "play", "video": "../a.mkv"},
    {"kind": "seek", "video": "a.mkv", "seconds": -1}, {"kind": "settings", "changes": {"relay_url": "https://evil"}},
    {"kind": "settings", "changes": {"delay": "10"}}, {"kind": "settings", "changes": {}}, "n'importe quoi", None,
])
def test_invalid_commands_are_refused_before_being_queued(env, payload):
    client, *_ = logged_in(env)
    put_state(client)
    r = command(client, payload)
    assert r.status_code == 422 and r.json()["detail"]
    assert client.get("/api/device/commands", headers=DEVICE).json() == []


def test_film_commands_need_a_known_state(env):
    client, *_ = logged_in(env)
    assert command(client, {"kind": "play", "video": "a.mkv"}).status_code == 422          # pas encore d'état du Pi
    assert command(client, {"kind": "pause"}).status_code == 202


def test_commands_need_the_cookie_and_the_header(env):
    client, *_ = env
    assert client.post("/api/admin/commands", json={"kind": "pause"}, headers=XRW).status_code == 401
    login(client)
    assert client.post("/api/admin/commands", json={"kind": "pause"}).status_code == 403


def test_too_many_pending_commands_are_refused(env):
    client, *_ = logged_in(env)
    for _ in range(20):
        assert command(client, {"kind": "pause"}).status_code == 202
    assert command(client, {"kind": "pause"}).status_code == 429


def test_ack_updates_the_status_seen_by_the_admin(env):
    client, *_ = logged_in(env)
    put_state(client)
    cid = command(client, {"kind": "play", "video": "a.mkv"}).json()["id"]
    assert client.get(f"/api/admin/commands/{cid}", headers=XRW).json()["status"] == "pending"
    r = client.post(f"/api/device/commands/{cid}/ack", json={"status": "applied", "message": ""}, headers=DEVICE)
    assert r.status_code == 200
    assert client.get(f"/api/admin/commands/{cid}", headers=XRW).json() == {"status": "applied", "message": ""}
    assert client.get("/api/device/commands", headers=DEVICE).json() == []
    again = client.post(f"/api/device/commands/{cid}/ack", json={"status": "failed", "message": "x"}, headers=DEVICE)
    assert again.status_code == 404                                                   # jamais deux fois


def test_failed_ack_carries_its_message(env):
    client, *_ = logged_in(env)
    cid = command(client, {"kind": "pause"}).json()["id"]
    client.post(f"/api/device/commands/{cid}/ack", json={"status": "failed", "message": "film introuvable"},
                headers=DEVICE)
    assert client.get(f"/api/admin/commands/{cid}", headers=XRW).json()["message"] == "film introuvable"


@pytest.mark.parametrize("body", [{"status": "nimporte"}, {"status": "pending"}, {}])
def test_invalid_ack_status_is_refused(env, body):
    client, *_ = logged_in(env)
    cid = command(client, {"kind": "pause"}).json()["id"]
    assert client.post(f"/api/device/commands/{cid}/ack", json=body, headers=DEVICE).status_code == 422


def test_unknown_or_malformed_command_ids(env):
    client, *_ = logged_in(env)
    assert client.get("/api/admin/commands/" + "0" * 32, headers=XRW).status_code == 404
    assert client.get("/api/admin/commands/..%2Fx", headers=XRW).status_code in (404, 422)
    assert client.post("/api/device/commands/" + "0" * 32 + "/ack", json={"status": "applied"},
                       headers=DEVICE).status_code == 404


def test_commands_expire_and_are_never_offered_late(env):
    client, clock, config = logged_in(env)
    cid = command(client, {"kind": "pause"}).json()["id"]
    clock.t += config.command_ttl_seconds + 1
    assert client.get("/api/device/commands", headers=DEVICE).json() == []
    assert client.get(f"/api/admin/commands/{cid}", headers=XRW).json()["status"] == "expired"


def test_the_admin_state_lists_recent_commands(env):
    client, *_ = logged_in(env)
    for _ in range(3):
        command(client, {"kind": "pause"})
    assert len(client.get("/api/admin/state", headers=XRW).json()["commands"]) == 3
```

Ajouter à `tests/test_relay_api.py` l'assertion de la clé `admin` : remplacer, dans `test_page_and_config`, la comparaison par :

```python
    assert client.get("/api/config").json() == {"min_duration": 60, "max_duration": 3600, "max_mb": 10,
                                                "caption_max": 128, "admin": False}
```

- [ ] **Step 2: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_relay_admin.py -q` → FAIL

- [ ] **Step 3: Implémenter dans `relay/app.py`**

Imports à ajouter : `import json`, `import secrets`, `from ..remote import FILM_COMMANDS, validate_command`, et `make_session_token, verify_session_token` dans l'import depuis `.security`.

Modèles (avec `Ack`) :

```python
class Login(BaseModel):
    password: str = ""


class CommandAck(BaseModel):
    status: str
    message: str = ""
```

Dans `create_app`, après `limiter = RateLimiter(clock)` :

```python
    admin_limiter = RateLimiter(clock)
    admin_enabled = bool(config.admin_password_hash)
    session_key = (config.session_secret or secrets.token_hex(32)).encode()
    COOKIE = "sm_admin"
    CSRF_HEADER = "x-requested-with"
```

Helpers (après `check_id`) :

```python
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
        return state, received, clock() - received < 60
```

`/api/config` : ajouter `"admin": admin_enabled`.

Routes à ajouter avant `return app` :

```python
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
    def admin_login(request: Request, body: Login) -> JSONResponse:
        if not admin_enabled:
            raise HTTPException(503, "Administration désactivée.")
        if request.headers.get(CSRF_HEADER) != "slowmovie":
            raise HTTPException(403, "En-tête de requête manquant.")
        ip = client_ip(request)
        wait = admin_limiter.blocked(ip)
        if wait > 0:
            raise HTTPException(429, f"Trop d'essais. Réessayez dans {int(wait // 60) + 1} minute(s).")
        if not verify_password(body.password, config.admin_password_hash):
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
```

`read_json` doit aussi lire un corps vide : `b"null"` → `None` → `validate_command(None)` lève `ValueError` ✓ (422).

Attention : `read_json` est `async` ; `put_state` et `admin_command` aussi. Les autres routes restent synchrones.

- [ ] **Step 4: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: PASS. Si `test_tampered_or_expired_cookies_are_refused` échoue sur la façon de poser le cookie dans le client de test, poser la valeur falsifiée avec `client.cookies.set("sm_admin", valeur, domain="testserver", path="/api/admin")` après avoir vidé le jar, et le noter dans le journal.

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/relay/app.py tests/test_relay_admin.py tests/test_relay_api.py
git commit -m "feat(relay): routes de l'appareil (état, commandes) et de l'administrateur"
```

---

### Task 8: Pi — synchronisation du pilotage (`relayclient`, `runner`) et bout en bout

**Files:**
- Modify: `slowmovie_inkdrop/relayclient.py`, `slowmovie_inkdrop/runner.py`
- Test: `tests/test_relayclient.py`, `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `validate_command` (T4) ; `Player.submit`, `Player.remote_snapshot` (T4/T5) ; routes de l'appareil (T7) ; `Paths.commands` (T2).
- Produces:
  - `RelayClient.put_state(snapshot)`, `.commands() -> list[dict]`, `.ack_command(command_id, status, message="")`
  - `CommandLog(path)` : `.get(id)`, `.put(id, status, message)`, `.done(id)`, `.pending() -> dict`
  - `apply_remote_command(player, command: dict) -> {"status": "applied"|"failed", "message": str}`
  - `sync_control(client, player, log, now) -> str` (ne lève jamais)
  - `relay_loop(player, queue, outbox, stop, clock, command_log=None)`

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/test_relayclient.py` (les imports `json`, `httpx` existent déjà ; ajouter `from concurrent.futures import Future` et `from slowmovie_inkdrop.relayclient import CommandLog, apply_remote_command, sync_control`) :

```python
class FakePlayer:
    """Lecteur factice : enregistre les commandes reçues et répond comme le vrai."""

    def __init__(self, replies=None):
        self.received = []
        self.replies = replies or {}

    def remote_snapshot(self, now):
        return {"sent_at": now, "mode": "FILM", "films": [], "settings": {"delay": 40}}

    def submit(self, cmd):
        self.received.append(cmd)
        future = Future()
        future.set_result(self.replies.get(cmd["cmd"], {"ok": True}))
        return future


class ControlRelay:
    """Relais factice côté pilotage."""

    def __init__(self, commands=None):
        self.commands = commands or []
        self.states = []
        self.acks = []
        self.ack_codes = {}
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("relais éteint")
        assert request.headers["authorization"] == "Bearer jeton"
        path = request.url.path
        if path == "/api/device/state" and request.method == "PUT":
            self.states.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        if path == "/api/device/commands":
            return httpx.Response(200, json=self.commands)
        if path.startswith("/api/device/commands/") and path.endswith("/ack"):
            cid = path.split("/")[-2]
            if cid in self.ack_codes:
                return httpx.Response(self.ack_codes[cid], json={"detail": "x"})
            self.acks.append((cid, json.loads(request.content)))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    def client(self) -> RelayClient:
        return RelayClient("https://relais.test", "jeton",
                           client=httpx.Client(transport=httpx.MockTransport(self.handler)))


def test_apply_remote_command_translates_and_reports(tmp_path):
    player = FakePlayer()
    assert apply_remote_command(player, {"kind": "play", "video": "a.mkv"}) == {"status": "applied", "message": ""}
    assert player.received == [{"cmd": "play", "video": "a.mkv"}]


def test_apply_remote_command_revalidates_everything(tmp_path):
    player = FakePlayer()
    for bad in ({"kind": "settings", "changes": {"relay_url": "https://evil"}}, {"kind": "play", "video": "../x"},
                {"kind": "dance"}, "n'importe quoi", None):
        result = apply_remote_command(player, bad)
        assert result["status"] == "failed" and result["message"]
    assert player.received == []                                       # rien n'a atteint le lecteur


def test_apply_remote_command_reports_a_refusal_from_the_player():
    player = FakePlayer({"play": {"ok": False, "error": "film introuvable ou pas encore prêt"}})
    assert apply_remote_command(player, {"kind": "play", "video": "a.mkv"}) == {
        "status": "failed", "message": "film introuvable ou pas encore prêt"}


def test_sync_control_applies_new_commands_acks_them_and_sends_the_state(tmp_path):
    relay = ControlRelay([{"id": A, "command": {"kind": "pause"}}, {"id": B, "command": {"kind": "skip"}}])
    player, log = FakePlayer(), CommandLog(tmp_path / "commands.json")
    status = sync_control(relay.client(), player, log, now=5.0)
    assert [c["cmd"] for c in player.received] == ["pause", "skip"]
    assert [a[0] for a in relay.acks] == [A, B] and relay.acks[0][1]["status"] == "applied"
    assert relay.states and relay.states[0]["settings"] == {"delay": 40}
    assert log.pending() == {} and "2" in status


def test_a_command_offered_again_is_never_applied_twice(tmp_path):
    relay = ControlRelay([{"id": A, "command": {"kind": "pause"}}])
    relay.ack_codes[A] = 500                                           # l'accusé n'arrive pas
    player, log = FakePlayer(), CommandLog(tmp_path / "commands.json")
    sync_control(relay.client(), player, log, now=1.0)
    sync_control(relay.client(), player, log, now=2.0)
    assert len(player.received) == 1                                    # appliquée une seule fois
    relay.ack_codes.clear()
    sync_control(relay.client(), player, log, now=3.0)
    assert relay.acks[-1][0] == A and log.pending() == {} and len(player.received) == 1


def test_an_ack_for_a_command_the_relay_forgot_is_dropped(tmp_path):
    relay = ControlRelay([])
    relay.ack_codes[A] = 404
    log = CommandLog(tmp_path / "commands.json")
    log.put(A, "applied", "")
    sync_control(relay.client(), FakePlayer(), log, now=1.0)
    assert log.pending() == {}


def test_a_failing_ack_does_not_stop_the_other_commands_or_the_state(tmp_path):
    relay = ControlRelay([{"id": A, "command": {"kind": "pause"}}, {"id": B, "command": {"kind": "skip"}}])
    relay.ack_codes[A] = 500
    player, log = FakePlayer(), CommandLog(tmp_path / "commands.json")
    sync_control(relay.client(), player, log, now=1.0)
    assert [c["cmd"] for c in player.received] == ["pause", "skip"] and relay.states
    assert [a[0] for a in relay.acks] == [B] and list(log.pending()) == [A]


def test_sync_control_never_raises_when_the_relay_is_down(tmp_path):
    relay = ControlRelay()
    relay.down = True
    status = sync_control(relay.client(), FakePlayer(), CommandLog(tmp_path / "c.json"), now=1.0)
    assert "injoignable" in status


def test_command_log_survives_restart(tmp_path):
    CommandLog(tmp_path / "c.json").put(C, "failed", "raison")
    assert CommandLog(tmp_path / "c.json").pending() == {C: {"status": "failed", "message": "raison"}}
```

Ajouter à `tests/test_end_to_end.py` :

```python
def test_admin_command_travels_from_the_web_to_the_player(make_player, tmp_path):
    from slowmovie_inkdrop.relayclient import CommandLog, sync_control

    config = RelayConfig(data_dir=tmp_path / "relay3", password_hash=hash_password("pw pw pw"),
                         device_token="j" * 40, admin_password_hash=hash_password("admin pw pw"),
                         session_secret="k" * 32)
    web = TestClient(create_app(config), base_url="https://testserver")
    xrw = {"X-Requested-With": "slowmovie"}
    assert web.post("/api/admin/login", json={"password": "admin pw pw"}, headers=xrw).status_code == 200

    ctx = make_player(videos=("a.mp4", "b.mp4"))
    client = RelayClient("https://testserver", "j" * 40, client=web)
    log = CommandLog(tmp_path / "commands3.json")
    ctx.player.tick()
    sync_control(client, ctx.player, log, ctx.clock())                      # 1er tour : envoie l'état du Pi
    state = web.get("/api/admin/state", headers=xrw).json()
    assert state["online"] and {f["name"] for f in state["state"]["films"]} == {"a.mp4", "b.mp4"}

    first = web.post("/api/admin/commands", json={"kind": "play", "video": "b.mp4"}, headers=xrw).json()["id"]
    second = web.post("/api/admin/commands", json={"kind": "settings", "changes": {"delay": 15}}, headers=xrw).json()["id"]
    assert web.post("/api/admin/commands", json={"kind": "settings", "changes": {"relay_url": "x"}},
                    headers=xrw).status_code == 422

    import threading
    runner = threading.Thread(target=sync_control, args=(client, ctx.player, log, ctx.clock()))
    runner.start()
    for _ in range(200):                                                    # le lecteur traite les commandes dans tick()
        ctx.player.tick()
        if not runner.is_alive():
            break
        runner.join(timeout=0.02)
    runner.join(timeout=5)
    assert web.get(f"/api/admin/commands/{first}", headers=xrw).json()["status"] == "applied"
    assert web.get(f"/api/admin/commands/{second}", headers=xrw).json()["status"] == "applied"
    assert ctx.player.video.name == "b.mp4" and ctx.player.settings.delay == 15
    from slowmovie_inkdrop.config import Settings
    assert Settings.load(ctx.paths.conf).delay == 15
```

- [ ] **Step 2: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_relayclient.py tests/test_end_to_end.py -q` → FAIL

- [ ] **Step 3: Implémenter dans `relayclient.py`**

Ajouter `from .remote import validate_command` aux imports, puis :

```python
class RelayClient:
    ...
    def put_state(self, snapshot: dict) -> None:
        self._request("PUT", "/api/device/state", json=snapshot)

    def commands(self) -> list[dict]:
        return self._request("GET", "/api/device/commands").json()

    def ack_command(self, command_id: str, status: str, message: str = "") -> None:
        self._request("POST", f"/api/device/commands/{command_id}/ack",
                      json={"status": status, "message": message[:300]})
```

(les trois méthodes sont ajoutées à la classe `RelayClient` existante) et :

```python
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


def apply_remote_command(player, command) -> dict:
    """Revalide la commande puis la fait appliquer par le lecteur. Ne lève jamais."""
    try:
        cmd = validate_command(command)
    except ValueError as exc:
        return {"status": "failed", "message": str(exc)}
    payload = {"cmd": cmd.pop("kind"), **cmd}
    try:
        reply = player.submit(payload).result(timeout=60)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "message": f"le lecteur n'a pas répondu : {exc}"}
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
            result = apply_remote_command(player, item.get("command"))
            log.put(command_id, result["status"], result["message"])
            _ack(client, log, command_id, result["status"], result["message"])
            applied += 1
        client.put_state(player.remote_snapshot(now))
        return f"ok ({applied} commande(s))"
    except RelayError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("pilotage du relais")
        return f"erreur : {exc}"
```

Dans `_ack`, le cas « accusé oublié par le relais » (404/410/422) supprime l'entrée ; tout autre échec la conserve.

`relay_loop` : ajouter le paramètre `command_log: CommandLog | None = None` et, après `player.relay_status = sync_cycle(...)` :

```python
            if command_log is not None:
                control = sync_control(cached[1], player, command_log, clock())
                if not control.startswith("ok"):
                    player.relay_status = f"{player.relay_status} | {control}"
```

`runner.py` : importer `CommandLog`, créer `command_log = CommandLog(paths.commands)` et le passer : `args=(player, queue, outbox, stop, time.time, command_log)`.

- [ ] **Step 4: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .` → PASS

- [ ] **Step 5: Commit**

```bash
git add slowmovie_inkdrop/relayclient.py slowmovie_inkdrop/runner.py tests/test_relayclient.py tests/test_end_to_end.py
git commit -m "feat(relayclient): synchronisation du pilotage, commandes jamais rejouées, test de bout en bout"
```

---

### Task 9: Pages — onglets, réglages, confirmation d'envoi

**Files:**
- Modify: `slowmovie_inkdrop/relay/static/index.html` (remplacé en entier), `slowmovie_inkdrop/relay/app.py` (route `/envoi/{id}`)
- Create: `slowmovie_inkdrop/relay/static/envoi.html`
- Test: `tests/test_relay_pages.py`

**Interfaces:**
- Consumes: routes de T7 (`/api/config`, `/api/admin/*`, `/api/status/{id}`).
- Produces: `GET /envoi/{identifiant}` → page de confirmation ; le formulaire redirige avec `location.assign("/envoi/" + id)` ; onglet « Réglages » affiché seulement si `/api/config` renvoie `admin: true`.

- [ ] **Step 1: Écrire les tests (`tests/test_relay_pages.py`)**

```python
import re

import pytest
from fastapi.testclient import TestClient

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password


@pytest.fixture
def client(tmp_path):
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password("x"), device_token="t" * 40,
                         admin_password_hash=hash_password("y"))
    return TestClient(create_app(config), base_url="https://testserver")


def test_the_main_page_has_two_tabs_and_the_admin_hooks(client):
    html = client.get("/").text
    for expected in ('role="tablist"', 'id="tab-send"', 'id="tab-admin"', 'role="tabpanel"',
                     "/api/admin/login", "/api/admin/state", "/api/admin/commands", "X-Requested-With",
                     'name="caption"', 'location.assign("/envoi/"'):
        assert expected in html, expected


@pytest.mark.parametrize("path", ["/", "/envoi/" + "a" * 32, "/envoi/nimporte-quoi"])
def test_pages_never_inject_data_as_html(client, path):
    html = client.get(path).text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in scripts, forbidden


def test_the_confirmation_page_follows_the_upload(client):
    for ident in ("a" * 32, "0" * 32, "pas-un-identifiant"):
        r = client.get("/envoi/" + ident)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = client.get("/envoi/" + "a" * 32).text
    for expected in ("/api/status/", "location.pathname", "Envoyer une autre image", "introuvable"):
        assert expected in html, expected


def test_the_admin_tab_is_hidden_until_the_relay_says_it_is_enabled(client):
    html = client.get("/").text
    assert re.search(r'id="tab-admin"[^>]*hidden', html)
    assert "c.admin" in html
```

- [ ] **Step 2: Lancer pour voir l'échec** — FAIL

- [ ] **Step 3: Route et page de confirmation**

Dans `relay/app.py` (après la lecture de `page`) : `envoi_page = (Path(__file__).parent / "static" / "envoi.html").read_text(encoding="utf-8")` et la route :

```python
    @app.get("/envoi/{image_id}", response_class=HTMLResponse)
    def envoi_page_route(image_id: str) -> str:
        return envoi_page
```

`slowmovie_inkdrop/relay/static/envoi.html` :

```html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Envoi en cours</title>
<style>
  :root { --bg:#fafaf7; --fg:#1b1b1b; --muted:#6b6b66; --line:#d9d8d2; --accent:#1b1b1b; --err:#a3281f; --ok:#256b3a; }
  @media (prefers-color-scheme: dark) { :root { --bg:#161616; --fg:#eeeeea; --muted:#9a9a94; --line:#333; --accent:#eeeeea; --err:#ff8a80; --ok:#7fd49a; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:17px/1.45 system-ui, sans-serif; }
  main { max-width:30rem; margin:0 auto; padding:2rem 16px 3rem; }
  h1 { font-size:1.4rem; margin:0 0 .5rem; } p { margin:.4rem 0; } .muted { color:var(--muted); }
  #state { font-size:1.15rem; margin:1.2rem 0 .3rem; } .ok { color:var(--ok); } .err { color:var(--err); }
  a.button { display:block; text-align:center; margin-top:1.8rem; padding:.7rem .8rem; border-radius:8px; background:var(--accent); color:var(--bg); font-weight:600; text-decoration:none; }
</style>
</head>
<body>
<main>
  <h1>Votre image</h1>
  <p id="state" role="status" aria-live="polite">Vérification…</p>
  <p id="detail" class="muted"></p>
  <a class="button" href="/">Envoyer une autre image</a>
</main>
<script>
const $ = (id) => document.getElementById(id);
const id = location.pathname.split("/").filter(Boolean).pop() || "";
function show(text, cls, detail) { $("state").textContent = text; $("state").className = cls || ""; $("detail").textContent = detail || ""; }
function hhmm(seconds) {
  return new Date(Date.now() + seconds * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
async function poll() {
  let r;
  try { r = await fetch("/api/status/" + encodeURIComponent(id)); } catch (e) { setTimeout(poll, 5000); return; }
  if (r.status === 404) return show("Envoi introuvable.", "err", "Le lien est incorrect, ou l'envoi est trop ancien.");
  const s = await r.json().catch(() => ({}));
  if (s.status === "shown") return show("Votre image a été affichée.", "ok", "Merci !");
  if (s.status === "failed" || s.status === "expired") return show("L'image n'a pas pu être affichée.", "err", "Vous pouvez en envoyer une autre.");
  show("Image reçue.", "ok", `Position ${s.position} dans la file — affichage vers ${hhmm(s.eta_seconds)}.`);
  setTimeout(poll, 5000);
}
poll();
</script>
</body>
</html>
```

- [ ] **Step 4: Page principale (`index.html`) — remplacer le fichier**

```html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inkdrop</title>
<style>
  :root { --bg:#fafaf7; --fg:#1b1b1b; --muted:#6b6b66; --line:#d9d8d2; --accent:#1b1b1b; --err:#a3281f; --ok:#256b3a; --card:#f1f0ea; }
  @media (prefers-color-scheme: dark) { :root { --bg:#161616; --fg:#eeeeea; --muted:#9a9a94; --line:#333; --accent:#eeeeea; --err:#ff8a80; --ok:#7fd49a; --card:#202020; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:17px/1.45 system-ui, sans-serif; }
  main { max-width:38rem; margin:0 auto; padding:1.2rem 16px 3rem; }
  h1 { font-size:1.4rem; margin:0 0 .75rem; } h2 { font-size:1.05rem; margin:1.4rem 0 .5rem; }
  [role=tablist] { display:flex; gap:.4rem; border-bottom:1px solid var(--line); margin-bottom:1rem; }
  [role=tab] { width:auto; margin:0; padding:.55rem .9rem; border:1px solid transparent; border-bottom:none; border-radius:8px 8px 0 0; background:transparent; color:var(--muted); font-weight:600; cursor:pointer; }
  [role=tab][aria-selected=true] { color:var(--fg); border-color:var(--line); background:var(--card); }
  [hidden] { display:none !important; }
  p.lead { color:var(--muted); margin:0 0 1rem; }
  label { display:block; font-weight:600; margin:1.1rem 0 .35rem; }
  input, select, button { font:inherit; color:inherit; background:transparent; border:1px solid var(--line); border-radius:8px; padding:.6rem .75rem; }
  input:not([type=checkbox]), select { width:100%; }
  button { background:var(--accent); color:var(--bg); border-color:var(--accent); font-weight:600; cursor:pointer; }
  button.plain { background:transparent; color:var(--fg); border-color:var(--line); font-weight:500; }
  button:disabled { opacity:.5; cursor:default; }
  form button[type=submit] { width:100%; margin-top:1.4rem; }
  #preview { display:none; width:100%; max-height:14rem; object-fit:contain; margin-top:.8rem; border:1px solid var(--line); border-radius:8px; }
  .msg { margin-top:1rem; min-height:1.4rem; } .err { color:var(--err); } .ok { color:var(--ok); }
  .hint { color:var(--muted); font-weight:400; font-size:.85rem; }
  .remember { display:flex; gap:.5rem; align-items:center; margin-top:.7rem; font-weight:400; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:.8rem 1rem; margin:.8rem 0; }
  .row { display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; margin:.4rem 0; }
  .row > .grow { flex:1 1 10rem; min-width:0; overflow-wrap:anywhere; }
  .row input { width:7.5rem !important; }
  .film { border-top:1px solid var(--line); padding:.6rem 0; } .film:first-of-type { border-top:none; }
  .badge { display:inline-block; padding:.05rem .5rem; border-radius:999px; border:1px solid var(--line); font-size:.8rem; color:var(--muted); }
  .dot { display:inline-block; width:.6rem; height:.6rem; border-radius:50%; margin-right:.4rem; background:var(--err); } .dot.on { background:var(--ok); }
  .setting { display:grid; grid-template-columns:1fr auto; gap:.5rem; align-items:center; margin:.35rem 0; }
  .setting input:not([type=checkbox]) { width:8rem; }
</style>
</head>
<body>
<main>
  <h1>Inkdrop</h1>
  <div role="tablist" aria-label="Sections">
    <button type="button" role="tab" id="tab-send" aria-controls="panel-send" aria-selected="true">Envoyer une image</button>
    <button type="button" role="tab" id="tab-admin" aria-controls="panel-admin" aria-selected="false" tabindex="-1" hidden>Réglages</button>
  </div>

  <section id="panel-send" role="tabpanel" aria-labelledby="tab-send">
    <p class="lead">Elle s'affichera sur l'écran à son tour, pendant la durée choisie.</p>
    <form id="form">
      <label for="image">Image (JPEG, PNG ou WebP)</label>
      <input id="image" name="image" type="file" accept="image/jpeg,image/png,image/webp" required>
      <img id="preview" alt="Aperçu">
      <label for="duration">Durée d'affichage</label>
      <select id="duration" name="duration"></select>
      <label for="caption">Texte sous l'image <span class="hint">(facultatif)</span></label>
      <input id="caption" name="caption" type="text" maxlength="128" autocomplete="off">
      <div id="count" class="hint" aria-live="polite"></div>
      <label for="password">Mot de passe</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <label class="remember"><input id="remember" type="checkbox"> Se souvenir sur cet appareil</label>
      <button id="submit" type="submit">Envoyer</button>
    </form>
    <div id="msg" class="msg" role="status" aria-live="polite"></div>
  </section>

  <section id="panel-admin" role="tabpanel" aria-labelledby="tab-admin" hidden>
    <div id="admin-login">
      <p class="lead">Réglages du lecteur, réservés à l'administrateur.</p>
      <label for="admin-password">Mot de passe administrateur</label>
      <input id="admin-password" type="password" autocomplete="current-password">
      <div class="row"><button id="admin-login-button" type="button">Se connecter</button></div>
    </div>
    <div id="admin-main" hidden></div>
    <div id="admin-msg" class="msg" role="status" aria-live="polite"></div>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const PRESETS = [[60, "1 minute"], [300, "5 minutes"], [900, "15 minutes"], [1800, "30 minutes"], [3600, "1 heure"]];
function stored(key, value) {
  try { if (value === undefined) return localStorage.getItem(key); if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch (e) { return null; }
}
function say(id, text, cls) { $(id).textContent = text; $(id).className = "msg " + (cls || ""); }

/* ---- onglets ---- */
const tabs = [$("tab-send"), $("tab-admin")];
function selectTab(tab) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute("aria-selected", on ? "true" : "false");
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute("aria-controls")).hidden = !on;
  }
  tab.focus();
  if (tab === $("tab-admin")) adminStart(); else adminStop();
}
for (const t of tabs) {
  t.addEventListener("click", () => selectTab(t));
  t.addEventListener("keydown", (e) => {
    const visible = tabs.filter((x) => !x.hidden);
    const i = visible.indexOf(t);
    if (e.key === "ArrowRight") { e.preventDefault(); selectTab(visible[(i + 1) % visible.length]); }
    if (e.key === "ArrowLeft") { e.preventDefault(); selectTab(visible[(i - 1 + visible.length) % visible.length]); }
  });
}

/* ---- envoi d'une image ---- */
function hhmm(seconds) {
  return new Date(Date.now() + seconds * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
fetch("/api/config").then((r) => r.json()).then((c) => {
  for (const [s, label] of PRESETS) if (s >= c.min_duration && s <= c.max_duration) $("duration").add(new Option(label, s));
  if (c.caption_max) { $("caption").maxLength = c.caption_max; countCaption(); }
  if (c.admin) $("tab-admin").hidden = false;
}).catch(() => PRESETS.forEach(([s, l]) => $("duration").add(new Option(l, s))));
const saved = stored("sm-password");
if (saved) { $("password").value = saved; $("remember").checked = true; }
function countCaption() {
  const max = $("caption").maxLength, n = [...$("caption").value].length;
  $("count").textContent = n ? `${n} / ${max}` : "";
}
$("caption").addEventListener("input", countCaption);
$("image").addEventListener("change", () => {
  const f = $("image").files[0];
  if (f) { $("preview").src = URL.createObjectURL(f); $("preview").style.display = "block"; }
});
$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("submit").disabled = true; say("msg", "Envoi en cours…");
  try {
    const r = await fetch("/api/send", { method: "POST", body: new FormData($("form")) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { say("msg", body.detail || "Erreur " + r.status, "err"); return; }
    stored("sm-password", $("remember").checked ? $("password").value : null);
    location.assign("/envoi/" + body.id);
  } catch (err) {
    say("msg", "Impossible de joindre le serveur.", "err");
  } finally { $("submit").disabled = false; }
});

/* ---- administration ---- */
const SETTINGS = [
  ["delay", "Délai entre deux images (s)", "int"], ["increment", "Images avancées à chaque rafraîchissement", "int"],
  ["random_file", "Film au hasard", "bool"], ["random_frames", "Images au hasard", "bool"],
  ["loop", "Boucler sur un seul film", "bool"], ["fullscreen", "Remplir l'écran (recadrer)", "bool"],
  ["subtitles", "Sous-titres", "bool"], ["subtitle_track", "Piste de sous-titres intégrée (0 = première)", "int"],
  ["timecode", "Afficher le timecode", "bool"], ["contrast", "Contraste (0,1 à 3)", "float"],
  ["min_image_seconds", "Durée minimale d'une image envoyée (s)", "int"], ["max_image_seconds", "Durée maximale d'une image envoyée (s)", "int"],
  ["queue_max", "Taille maximale de la file", "int"], ["queue_expiry_seconds", "Expiration d'une image en attente (s)", "int"],
  ["caption_max_chars", "Longueur maximale du texte sous l'image", "int"],
  ["active_hours", "Plage horaire (HH:MM-HH:MM, vide = toujours)", "str"], ["relay_poll_seconds", "Interrogation du relais (s, 5 à 300)", "int"],
];
const XRW = { "X-Requested-With": "slowmovie" };
let timer = null, lastState = null;

async function api(path, options = {}) {
  const headers = { ...XRW, ...(options.body ? { "Content-Type": "application/json" } : {}) };
  const r = await fetch(path, { credentials: "same-origin", ...options, headers });
  const body = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, body };
}
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "className") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}
function fmt(seconds) {
  const t = Math.max(0, Math.floor(seconds || 0));
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(t / 3600))}:${p(Math.floor(t % 3600 / 60))}:${p(t % 60)}`;
}
function parseTimecode(text) {
  const parts = text.trim().split(":");
  if (!text.trim() || parts.length > 3 || parts.some((p) => p === "" || isNaN(Number(p)) || Number(p) < 0)) return null;
  const nums = parts.map(Number);
  if (nums.length > 1 && nums.slice(1).some((n) => n >= 60)) return null;
  return nums.reduce((acc, n) => acc * 60 + n, 0);
}

async function send(command, label) {
  const r = await api("/api/admin/commands", { method: "POST", body: JSON.stringify(command) });
  if (!r.ok) return say("admin-msg", r.body.detail || "Erreur " + r.status, "err");
  say("admin-msg", `${label} : en attente du Pi…`);
  for (let i = 0; i < 100; i++) {
    await new Promise((res) => setTimeout(res, 3000));
    const s = await api("/api/admin/commands/" + r.body.id);
    if (s.body.status === "applied") { say("admin-msg", `${label} : appliqué.`, "ok"); return refresh(); }
    if (s.body.status === "failed") return say("admin-msg", `${label} : refusé par le Pi — ${s.body.message || "échec"}`, "err");
    if (s.body.status === "expired") return say("admin-msg", `${label} : le Pi n'a pas répondu à temps.`, "err");
  }
}

function filmRow(film, state) {
  const startInput = el("input", { type: "text", value: fmt(film.start_seconds), "aria-label": "Point de départ" });
  const seekInput = el("input", { type: "text", value: fmt(film.position_seconds), "aria-label": "Aller à" });
  const timecode = (input, kind, label) => () => {
    const seconds = parseTimecode(input.value);
    if (seconds === null) return say("admin-msg", "Timecode invalide : utilisez hh:mm:ss.", "err");
    send({ kind, video: film.name, seconds }, `${label} ${film.name}`);
  };
  const subs = film.subtitle_codecs && film.subtitle_codecs.length ? ` · sous-titres : ${film.subtitle_codecs.join(", ")}` : "";
  const ready = film.status === "ready";
  return el("div", { className: "film" },
    el("div", { className: "row" },
      el("div", { className: "grow", text: film.name }),
      el("span", { className: "badge", text: film.name === state.video ? "en cours" : film.status })),
    el("div", { className: "muted hint", text: `Position ${fmt(film.position_seconds)} sur ${fmt(film.duration_seconds)} · départ ${fmt(film.start_seconds)}${subs}` }),
    ready ? el("div", { className: "row" },
      el("button", { type: "button", onclick: () => send({ kind: "play", video: film.name }, "Lancer " + film.name), text: "Lancer" }),
      el("button", { type: "button", className: "plain", onclick: () => send({ kind: "reset", video: film.name }, "Retour au départ de " + film.name), text: "Revenir au départ" })) : el("span"),
    ready ? el("div", { className: "row" }, seekInput,
      el("button", { type: "button", className: "plain", onclick: timecode(seekInput, "seek", "Aller dans"), text: "Aller à" }),
      startInput,
      el("button", { type: "button", className: "plain", onclick: timecode(startInput, "set_start", "Départ de"), text: "Régler le départ" })) : el("span"));
}

function settingsForm(values) {
  const inputs = {};
  const form = el("div", { className: "card" }, el("strong", { text: "Réglages" }));
  for (const [name, label, kind] of SETTINGS) {
    let input;
    if (kind === "bool") { input = el("input", { type: "checkbox", id: "set-" + name }); input.checked = !!values[name]; }
    else { input = el("input", { type: "text", id: "set-" + name, value: String(values[name] ?? "") }); }
    inputs[name] = [input, kind];
    form.append(el("div", { className: "setting" }, el("label", { for: "set-" + name, text: label }), input));
  }
  form.append(el("div", { className: "row" }, el("button", { type: "button", text: "Appliquer", onclick: () => {
    const changes = {};
    for (const [name, [input, kind]] of Object.entries(inputs)) {
      let value;
      if (kind === "bool") value = input.checked;
      else if (kind === "str") value = input.value.trim();
      else { value = Number(input.value); if (input.value.trim() === "" || isNaN(value) || (kind === "int" && !Number.isInteger(value))) return say("admin-msg", `Valeur invalide pour « ${name} ».`, "err"); }
      if (value !== values[name]) changes[name] = value;
    }
    if (!Object.keys(changes).length) return say("admin-msg", "Aucun changement.");
    send({ kind: "settings", changes }, "Réglages");
  } })));
  return form;
}

function render(data) {
  const root = $("admin-main");
  const state = data.state;
  const children = [];
  const ago = data.last_seen_seconds === null ? "jamais" : `il y a ${data.last_seen_seconds} s`;
  children.push(el("div", { className: "card" },
    el("div", { className: "row" }, el("span", { className: "dot" + (data.online ? " on" : "") }),
      el("strong", { text: data.online ? "Pi en ligne" : "Pi hors ligne" }), el("span", { className: "muted hint", text: `dernier contact : ${ago}` })),
    state ? el("div", { text: `${state.paused ? "En pause" : state.mode === "IMAGE" ? "Image envoyée affichée" : "Lecture"} · ${state.video || "aucun film"} · ${fmt(state.position_seconds)} / ${fmt(state.duration_seconds)}` }) : el("div", { className: "muted", text: "Aucun état reçu pour l'instant." }),
    state && state.error ? el("div", { className: "err", text: state.error }) : el("span"),
    el("div", { className: "row" },
      el("button", { type: "button", className: "plain", onclick: () => send({ kind: state && state.paused ? "resume" : "pause" }, state && state.paused ? "Reprise" : "Pause"), text: state && state.paused ? "Reprendre" : "Pause" }),
      el("button", { type: "button", className: "plain", onclick: () => send({ kind: "skip" }, "Passer"), text: "Passer" }),
      el("button", { type: "button", className: "plain", onclick: logout, text: "Se déconnecter" }))));
  if (state) {
    children.push(el("h2", { text: "Films" }));
    const list = el("div", { className: "card" });
    if (!state.films.length) list.append(el("div", { className: "muted", text: "Aucun film sur le Pi." }));
    for (const film of state.films) list.append(filmRow(film, state));
    children.push(list);
    children.push(settingsForm(state.settings || {}));
    if (state.queue && state.queue.length) {
      const q = el("div", { className: "card" }, el("strong", { text: "File d'images" }));
      for (const item of state.queue) q.append(el("div", { text: `${Math.round(item.duration / 60)} min${item.caption ? " — " + item.caption : ""}` }));
      children.push(q);
    }
  }
  root.replaceChildren(...children);
}

async function refresh() {
  const r = await api("/api/admin/state");
  if (r.status === 401 || r.status === 403) return showLogin();
  if (!r.ok) return say("admin-msg", r.body.detail || "Erreur " + r.status, "err");
  lastState = r.body;
  $("admin-login").hidden = true; $("admin-main").hidden = false;
  render(r.body);
}
function showLogin() { $("admin-login").hidden = false; $("admin-main").hidden = true; lastState = null; }
function adminStart() { refresh(); adminStop(); timer = setInterval(() => { if (!document.hidden && !$("panel-admin").hidden) refresh(); }, 5000); }
function adminStop() { if (timer) clearInterval(timer); timer = null; }
async function logout() { await api("/api/admin/logout", { method: "POST" }); showLogin(); say("admin-msg", "Déconnecté.", "ok"); }
$("admin-login-button").addEventListener("click", async () => {
  const r = await api("/api/admin/login", { method: "POST", body: JSON.stringify({ password: $("admin-password").value }) });
  $("admin-password").value = "";
  if (!r.ok) return say("admin-msg", r.body.detail || "Erreur " + r.status, "err");
  say("admin-msg", "");
  refresh();
});
$("admin-password").addEventListener("keydown", (e) => { if (e.key === "Enter") $("admin-login-button").click(); });
</script>
</body>
</html>
```

- [ ] **Step 5: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .` → PASS

- [ ] **Step 6: Vérification dans un vrai navigateur**

Lancer un relais local avec un faux état de Pi, puis ouvrir la page dans le navigateur intégré (outils `mcp__Claude_Browser__*`) et vérifier, en photographiant l'écran : onglets au clavier (`←`/`→`), connexion administrateur, état du Pi, liste des films dont l'un nommé `<img src=x onerror=alert(1)>.mkv` (affiché comme texte, aucune alerte), envoi d'une commande `play` puis son statut « en attente du Pi… », et la page `/envoi/<id>` après un vrai envoi.

```bash
cd /Users/charlescoiffier/dev/slowmovie-inkdrop
export SLOWMOVIE_RELAY_DATA=/tmp/relay-dev SLOWMOVIE_RELAY_DEVICE_TOKEN=$(python3 -c "print('t'*40)")
export SLOWMOVIE_RELAY_PASSWORD_HASH=$(.venv/bin/python -c "from slowmovie_inkdrop.relay.security import hash_password as h; print(h('envoi-test'))")
export SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH=$(.venv/bin/python -c "from slowmovie_inkdrop.relay.security import hash_password as h; print(h('admin-test'))")
rm -rf /tmp/relay-dev && .venv/bin/slowmovie-relay serve --port 8099 &
sleep 2
curl -s -X PUT localhost:8099/api/device/state -H "Authorization: Bearer $SLOWMOVIE_RELAY_DEVICE_TOKEN" -d '{"mode":"FILM","paused":false,"video":"a.mkv","position_seconds":125.0,"duration_seconds":8233.0,"error":"","queue":[],"settings":{"delay":40,"increment":4,"fullscreen":true,"subtitles":false,"subtitle_track":0,"contrast":1.0,"random_file":false,"random_frames":false,"loop":false,"timecode":false,"min_image_seconds":60,"max_image_seconds":3600,"queue_max":10,"queue_expiry_seconds":21600,"caption_max_chars":128,"active_hours":"","relay_poll_seconds":20},"films":[{"name":"a.mkv","status":"ready","duration_seconds":8233.0,"fps":23.976,"position_seconds":125.0,"start_seconds":0,"error":"","subtitle_codecs":["subrip","hdmv_pgs_subtitle"]},{"name":"<img src=x onerror=alert(1)>.mkv","status":"ready","duration_seconds":100.0,"fps":24.0,"position_seconds":0,"start_seconds":10,"error":"","subtitle_codecs":[]}]}'
```

Ouvrir `http://localhost:8099/` (les cookies `Secure` sont acceptés sur `localhost`). Arrêter le relais ensuite (`kill %1`).

- [ ] **Step 7: Commit**

```bash
git add slowmovie_inkdrop/relay tests/test_relay_pages.py
git commit -m "feat(relay): onglet Réglages, page de confirmation d'envoi"
```

---

### Task 10: TUI — navigation et points de départ

**Files:**
- Modify: `slowmovie_inkdrop/tui/model.py`, `slowmovie_inkdrop/tui/app.py`
- Test: `tests/test_tui_model.py`, `tests/test_tui_app.py`

**Interfaces:**
- Consumes: `StartPoints`, `parse_timecode`, `format_timecode` (T2) ; commandes `set_start`, `seek`, `reset` du lecteur (T4).
- Produces: `film_rows(paths)` renvoie aussi `position_seconds`, `start_seconds`, `duration_seconds` (`None` si le film n'a pas de `fps` en bibliothèque) ; `NavTable` ; touches `t` (point de départ), `g` (aller à), `z` (revenir au départ) ; `Échap` rend le focus à la barre d'onglets ; `SETTING_FIELDS` contient `subtitle_track`.

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/test_tui_model.py` :

```python
def test_film_rows_report_positions_and_start_points_in_seconds(tmp_path):
    from slowmovie_inkdrop.starts import StartPoints
    paths = Paths(tmp_path)
    paths.progress.mkdir()
    (paths.progress / "a.mp4.progress").write_text("480")
    paths.library.write_text(json.dumps({
        "a.mp4": {"size": 1, "status": "ready", "frames": 2400, "fps": 24.0, "duration": 100.0, "changed_at": 0},
        "old.mp4": {"size": 1, "status": "ready", "frames": 100, "changed_at": 0}}))
    StartPoints(paths.starts).set("a.mp4", 30)
    rows = {r["name"]: r for r in model.film_rows(paths)}
    assert rows["a.mp4"]["position_seconds"] == 20.0 and rows["a.mp4"]["start_seconds"] == 30.0
    assert rows["a.mp4"]["duration_seconds"] == 100.0
    assert rows["old.mp4"]["position_seconds"] is None            # pas de fps connu : on n'invente rien


def test_subtitle_track_is_a_setting():
    assert "subtitle_track" in {n for n, _, _ in model.SETTING_FIELDS}
```

Ajouter à `tests/test_tui_app.py` (ajouter `from textual.widgets import Tabs` aux imports ; la fixture `paths` écrit déjà `library.json` : y ajouter `"fps": 24.0, "duration": 100.0` pour `metropolis.mp4`) :

```python
async def test_down_from_the_tab_bar_enters_the_content_of_the_tab(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        pilot.app.focus_tabs()
        await pilot.pause()
        assert isinstance(pilot.app.focused, Tabs)
        await pilot.press("down")
        await pilot.pause()
        assert pilot.app.focused.id == "films-table"


async def test_down_from_the_tab_bar_works_on_every_table_tab(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        for key, table in (("3", "queue-table"), ("4", "settings-table"), ("5", "log-view")):
            await pilot.press(key)
            pilot.app.focus_tabs()
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            assert pilot.app.focused.id == table


async def test_up_on_the_first_row_returns_to_the_tab_bar_and_up_elsewhere_moves_the_cursor(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("down", "down")
        await pilot.press("up")
        assert pilot.app.query_one("#settings-table").cursor_row == 1
        await pilot.press("up")
        await pilot.press("up")
        await pilot.pause()
        assert isinstance(pilot.app.focused, Tabs)


async def test_escape_returns_to_the_tab_bar(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(pilot.app.focused, Tabs)


async def test_films_table_shows_position_and_start_columns(paths, sent):
    from slowmovie_inkdrop.starts import StartPoints
    StartPoints(paths.starts).set("metropolis.mp4", 90)
    paths.progress.mkdir(exist_ok=True)
    (paths.progress / "metropolis.mp4.progress").write_text("240")
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        row = pilot.app.query_one("#films-table").get_row_at(0)
        assert "00:00:10" in row and "00:01:30" in row


async def _prompt(pilot, key, text):
    await pilot.press("2")
    await pilot.pause()
    await pilot.press(key)
    await pilot.pause()
    pilot.app.screen.query_one("Input").value = text
    await pilot.press("enter")
    await pilot.pause()


async def test_t_sets_the_start_point(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await _prompt(pilot, "t", "00:01:30")
        assert sent[-1] == {"cmd": "set_start", "video": "metropolis.mp4", "seconds": 90.0}


async def test_g_seeks_to_a_timecode(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await _prompt(pilot, "g", "1:05")
        assert sent[-1] == {"cmd": "seek", "video": "metropolis.mp4", "seconds": 65.0}


async def test_an_invalid_timecode_sends_nothing(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        before = len(sent)
        await _prompt(pilot, "t", "n'importe quoi")
        assert len(sent) == before and pilot.app.is_running


async def test_z_goes_back_to_the_start_point(paths, sent):
    async with SlowMovieApp(paths).run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("z")
        assert sent[-1] == {"cmd": "reset", "video": "metropolis.mp4"}
```

- [ ] **Step 2: Lancer pour voir l'échec** — `.venv/bin/pytest tests/test_tui_model.py tests/test_tui_app.py -q` → FAIL

- [ ] **Step 3: Implémenter `tui/model.py`**

Imports : `from ..starts import StartPoints, frame_to_seconds`. Ajouter à `SETTING_FIELDS` après `("timecode", ...)` : `("subtitle_track", "Piste de sous-titres intégrée (0 = première)", "int"),`. Remplacer `film_rows` :

```python
def film_rows(paths: Paths) -> list[dict]:
    try:
        entries = json.loads(paths.library.read_text())
    except (OSError, ValueError):
        return []
    starts = StartPoints(paths.starts).all()
    rows = []
    for name, entry in sorted(entries.items()):
        percent = position = None
        fps = entry.get("fps")
        if entry.get("status") == "ready" and entry.get("frames"):
            done = read_progress(paths.progress, paths.base / name)
            percent = round(100 * done / entry["frames"], 1)
            position = round(frame_to_seconds(done, fps), 1) if fps else None
        rows.append({"name": name, "status": entry.get("status", "?"), "percent": percent,
                     "error": entry.get("error", ""), "position_seconds": position,
                     "start_seconds": starts.get(name, 0.0), "duration_seconds": entry.get("duration")})
    return rows
```

- [ ] **Step 4: Implémenter `tui/app.py`**

Imports : `from textual import events`, `Tabs` dans l'import de `textual.widgets`, `from ..starts import format_timecode, parse_timecode`.

Classe (avant `SlowMovieApp`) :

```python
class NavTable(DataTable):
    """Table dont la flèche haut, sur la première ligne, rend le focus à la barre d'onglets."""

    def action_cursor_up(self) -> None:
        if self.cursor_row == 0:
            self.app.focus_tabs()
        else:
            super().action_cursor_up()
```

Remplacer les trois `DataTable(...)` du `compose` par `NavTable(...)` (mêmes arguments). Ajouter aux `BINDINGS` :

```python
        Binding("t", "set_start", "Point de départ", show=False),
        Binding("g", "seek", "Aller à", show=False),
        Binding("escape", "focus_tabs", "Onglets", show=False),
```

Méthodes de `SlowMovieApp` :

```python
    def focus_tabs(self) -> None:
        self.query_one(TabbedContent).query_one(Tabs).focus()

    def action_focus_tabs(self) -> None:
        self.focus_tabs()

    def focus_content(self) -> None:
        target = {"films": "#films-table", "queue": "#queue-table", "settings": "#settings-table",
                  "log": "#log-view"}.get(self.query_one(TabbedContent).active)
        if target:
            self.query_one(target).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "down" and isinstance(self.focused, Tabs):
            self.focus_content()
            event.stop()
            event.prevent_default()

    def _ask_timecode(self, title: str, command: str, current: float) -> None:
        key = self._selected_key("#films-table")
        if not key:
            return

        def entered(value: str | None) -> None:
            if value is None:
                return
            try:
                seconds = parse_timecode(value)
            except ValueError as exc:
                self.notify(str(exc), severity="error")
                return
            self._send(cmd=command, video=key, seconds=seconds)
            self.refresh_data()

        self.push_screen(Prompt(f"{title} — {key} (hh:mm:ss)", format_timecode(current)), entered)

    def _selected_film(self) -> dict | None:
        key = self._selected_key("#films-table")
        return next((r for r in model.film_rows(self.paths) if r["name"] == key), None)

    def action_set_start(self) -> None:
        film = self._selected_film()
        if film:
            self._ask_timecode("Point de départ", "set_start", film["start_seconds"])

    def action_seek(self) -> None:
        film = self._selected_film()
        if film:
            self._ask_timecode("Aller à", "seek", film["position_seconds"] or 0.0)
```

Colonnes de la table des films : `add_columns("Film", "État", "Avancement", "Position", "Départ")` et, dans `refresh_data`, la ligne d'un film devient :

```python
        def tc(value):
            return format_timecode(value) if value is not None else "—"

        self._fill("#films-table", [
            (r["name"], (r["name"], STATUS_LABELS.get(r["status"], r["status"]),
                         f"{r['percent']} %" if r["percent"] is not None else r["error"],
                         tc(r["position_seconds"]), tc(r["start_seconds"])))
            for r in model.film_rows(self.paths)])
```

Mettre à jour `HELP` : `Films : Entrée lancer, t point de départ, g aller à, z revenir au départ, d supprimer, a ajouter` et `Flèche bas depuis les onglets pour entrer dans la liste, Échap pour revenir aux onglets`.

Modifier la fixture `paths` de `tests/test_tui_app.py` : l'entrée de `metropolis.mp4` devient `{"size": 1, "status": "ready", "frames": 100, "fps": 24.0, "duration": 100.0, "changed_at": 0}`.

- [ ] **Step 5: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: PASS. Si `test_up_on_the_first_row_...` échoue parce que `up` est consommé avant `NavTable`, vérifier que `NavTable.action_cursor_up` est bien celle de la liaison de la touche `up` du `DataTable` de la version de Textual installée, et le noter dans le journal.

- [ ] **Step 6: Commit**

```bash
git add slowmovie_inkdrop/tui tests/test_tui_model.py tests/test_tui_app.py
git commit -m "feat(tui): navigation flèches entre onglets et listes, points de départ par film"
```

---

### Task 11: Mise à jour du Pi, documentation et déploiement

**Files:**
- Create: `deploy/update-pi.sh`
- Modify: `deploy/relay/relay.env.example`, `docs/deploiement.md`, `README.md`
- Test: `tests/test_deploy_files.py`

**Interfaces:**
- Produces: `deploy/update-pi.sh` (mise à jour du code sur le Pi sans tout réinstaller) ; variables `SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH` et `SLOWMOVIE_RELAY_SESSION_SECRET` documentées.

- [ ] **Step 1: Écrire les tests**

Ajouter à `tests/test_deploy_files.py` :

```python
def test_relay_env_example_documents_the_admin_variables():
    env = (ROOT / "deploy/relay/relay.env.example").read_text()
    assert "SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH" in env and "SLOWMOVIE_RELAY_SESSION_SECRET" in env


def test_update_script_upgrades_the_package_and_restarts_the_service():
    import subprocess
    path = ROOT / "deploy/update-pi.sh"
    assert path.stat().st_mode & 0o111
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0
    script = path.read_text()
    assert "set -euo pipefail" in script and "git pull --ff-only" in script
    assert "install --upgrade" in script and "systemctl restart slowmovie" in script
    assert "/home/admin/.SlowMovie" in script


def test_deployment_guide_covers_the_admin_tab_and_the_update():
    guide = (ROOT / "docs/deploiement.md").read_text()
    for expected in ("SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH", "update-pi.sh", "Réglages", "/envoi/"):
        assert expected in guide, expected
```

- [ ] **Step 2: Lancer pour voir l'échec** — FAIL

- [ ] **Step 3: Écrire les fichiers**

`deploy/update-pi.sh` (puis `chmod +x`) :

```bash
#!/usr/bin/env bash
# Met à jour SlowMovie-Inkdrop sur le Pi sans tout réinstaller.
# À lancer depuis le dossier cloné :  cd ~/slowmovie-inkdrop && ./deploy/update-pi.sh
set -euo pipefail

VENV=/home/admin/.SlowMovie
REPO="$(cd "$(dirname "$0")/.." && pwd)"

cd "$REPO"
git pull --ff-only
"$VENV/bin/pip" install --upgrade "$REPO[video,tui]"
sudo ln -sf "$VENV/bin/slowmovie" /usr/local/bin/slowmovie
sudo systemctl restart slowmovie
sleep 5
systemctl --no-pager status slowmovie | head -8
slowmovie status
```

`deploy/relay/relay.env.example` : ajouter à la fin :

```
# Administration de la passerelle (onglet « Réglages »). Sans haché, l'onglet est désactivé.
# Résultat de : slowmovie-relay hash-password   (mot de passe DIFFÉRENT de celui des envois)
SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH=scrypt:...
# Facultatif : clé de signature des sessions ; sans elle, les sessions sautent à chaque redéploiement.
# SLOWMOVIE_RELAY_SESSION_SECRET=<chaîne aléatoire longue>
```

`docs/deploiement.md` : ajouter une section « 2 bis. Mettre à jour le Pi » (le Pi doit avoir un vrai clone : `rm -rf ~/slowmovie-inkdrop && git clone https://github.com/charlescoiffier/slowmovie-inkdrop && cd slowmovie-inkdrop && ./deploy/install-pi.sh` la première fois, puis `./deploy/update-pi.sh`), et une section « Pilotage depuis la passerelle » qui décrit : l'onglet « Réglages » (connexion administrateur, état du Pi, films, point de départ, sous-titres, réglages), le délai de ≈20 s, la liste des réglages jamais modifiables à distance, la création du haché administrateur et son ajout au `.env` du VPS suivi de `cd ~/srv/inkdrop && docker compose up -d --force-recreate`, la page de confirmation `/envoi/<identifiant>`, les sous-titres intégrés (texte seulement) et la recette (cases à cocher : changer de film depuis le téléphone, régler un point de départ, activer les sous-titres du film Kaguya, envoyer une image et voir la page de confirmation, vérifier qu'une session expirée redemande le mot de passe).

`README.md` : ajouter en tête de la liste des fonctions : « 4. Pilotage à distance (onglet Réglages de la passerelle, mot de passe administrateur séparé) : film, position de départ, sous-titres, réglages. »

- [ ] **Step 4: Lancer les tests** — `.venv/bin/pytest -q && .venv/bin/ruff check .` → PASS

- [ ] **Step 5: Commit**

```bash
chmod +x deploy/update-pi.sh
git add deploy docs/deploiement.md README.md tests/test_deploy_files.py
git commit -m "docs(deploy): mise à jour du Pi, administration de la passerelle, recette"
```

---

### Task 12: Recette en conditions réelles (manuelle, avec l'utilisateur)

**Files:** aucun (journal de la recette dans `docs/deploiement.md`, section « Recette »)

**Interfaces:** consomme tout ce qui précède. Nécessite l'accord de l'utilisateur pour chaque effet extérieur (push, redéploiement, mot de passe administrateur).

- [ ] **Step 1: Haché du mot de passe administrateur**

L'utilisateur lance `cd ~/dev/slowmovie-inkdrop && .venv/bin/slowmovie-relay hash-password` (mot de passe **différent** du mot de passe d'envoi) et fournit la ligne `scrypt:…`. L'ajouter à `~/srv/inkdrop/.env` sur le VPS (`SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH=…`), sans afficher d'autre secret.

- [ ] **Step 2: Pousser et déployer (avec l'accord explicite de l'utilisateur)**

`git push origin main`, attendre que « Build & Deploy » réussisse, puis `docker compose up -d --force-recreate` dans `~/srv/inkdrop` si le conteneur n'a pas relu le `.env`. Vérifier `https://inkdrop.charlescoiffier.fr/api/config` → `"admin": true`.

- [ ] **Step 3: Mettre le Pi à jour**

Copier la nouvelle version sur le Pi (`tar` depuis le Mac, ou `git clone` + `./deploy/install-pi.sh` puis `./deploy/update-pi.sh`), relancer le service, vérifier `slowmovie status` (« Relais : ok ») et que la passerelle montre « Pi en ligne » en moins de 40 s.

- [ ] **Step 4: Parcours complet** (cocher uniquement ce qui a été observé)

- [ ] Onglet « Réglages » visible, connexion avec le mauvais mot de passe refusée, avec le bon acceptée.
- [ ] Changer de film depuis le téléphone : le Pi bascule en moins de 40 s, le statut passe à « appliqué ».
- [ ] Régler un point de départ et « Aller à » un timecode : l'écran affiche l'image attendue.
- [ ] Activer les sous-titres : ils apparaissent sur le film Kaguya (ou le message « sous-titres image non pris en charge » si ce sont des PGS).
- [ ] Modifier le délai : il est appliqué, visible dans la TUI.
- [ ] Un réglage interdit (via `curl`, `relay_url`) est refusé.
- [ ] Après un envoi d'image, la page `/envoi/<id>` suit l'image jusqu'à « affichée ».
- [ ] Après expiration de la session (ou « Se déconnecter »), la page redemande le mot de passe.
- [ ] La TUI : `↓` depuis les onglets entre dans la liste, `↑` sur la première ligne revient aux onglets, `t`/`g`/`z` fonctionnent sur le Pi.

- [ ] **Step 5: Noter les résultats et committer**

```bash
git add docs/deploiement.md
git commit -m "docs: résultats de la recette du pilotage web"
```

---

## Self-Review

**Couverture de la spécification**
- §1.1 onglet Réglages → T6, T7, T9 ; §1.2 page de confirmation → T9 ; §1.3 sous-titres intégrés → T1, T3 ; §1.4 TUI → T10.
- §3 architecture (instantané, commandes, expiration 10 min, revalidation Pi, jamais rejoué) → T4, T5, T6, T7, T8.
- §4 authentification (haché distinct, désactivable, cookie, CSRF, blocage séparé, pas de mot de passe dans les journaux) → T6, T7.
- §5 liste fermée de réglages et exclusions → `remote.py` (T4) et tests T4/T7/T8.
- §6 sous-titres (`subtitle_track`, texte seulement, `VideoInfo.subtitle_codecs`, nombre de pistes dans l'instantané) → T1, T3, T5.
- §7 points de départ (`films.json`, première lecture, `reset`, boucle, `set_start` sans déplacement, `seek`) → T2, T4.
- §8 page de confirmation → T9 ; §9 page d'envoi et de réglages → T9 ; §10 TUI → T10 ; §11 robustesse → T6/T7/T8 (expiration, relais redéployé, accusés) ; §12 tests → chaque tâche ; §14 déploiement → T11, T12.

**Écarts assumés** : l'instantané donne la liste complète des codecs de sous-titres par film (et non seulement le nombre de pistes texte) pour que la page indique le type de chaque piste ; `subtitle_track` désigne le numéro parmi **tous** les flux de sous-titres, comme le filtre ffmpeg `si`.

**Cohérence des types** : `Player.submit` reçoit `{"cmd": kind, ...}` (T4) et le relais/`validate_command` utilisent `kind` (T4, T7, T8) ; `apply_remote_command` fait la traduction. `Library.rows()` expose `fps`, `subtitle_codecs`, `duration` (T1) consommés par `remote_snapshot` (T5) et `film_rows` (T10). `StartPoints.all()` (T2) consommé par T5 et T10.

**Review Focus** : (1) XSS → `test_pages_never_inject_data_as_html`, `test_hostile_strings_are_returned_verbatim_as_json_data`, vérification navigateur T9 ; (2) accès administrateur → tests T7 ; (3) commandes dangereuses → `test_remote.py`, tests T4, T7, T8 ; (4) désynchronisation → T6 (expiration), T8 (CommandLog, accusés), T7 ; (5) sous-titres et noms étranges → T1, T3.

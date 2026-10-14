"""Machine à états du lecteur : FILM <-> IMAGE."""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Protocol

from PIL import Image

from .config import Paths, Settings, in_active_hours
from .display import Display
from .image_queue import ImageQueue, QueuedImage
from .imaging import clean_caption, draw_caption, to_panel
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
            panel = draw_caption(panel, clean_caption(item.caption, s.caption_max_chars))
        except Exception as exc:  # noqa: BLE001 - bombe de décompression, mémoire... : l'image est écartée
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
            self.next_frame_at = now + TICK_MAX      # un film en cours de copie sera repris vite
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
            "queue": [{"id": i.id, "duration": i.duration, "caption": i.caption}
                      for i in self.queue.items()],
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

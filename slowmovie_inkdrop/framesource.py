"""Extraction d'une image de film avec ffmpeg (adapté de l'amont SlowMovie)."""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from PIL import Image

from .config import Settings
from .videos import VideoInfo, find_subtitles

log = logging.getLogger(__name__)


class FfmpegFrameSource:
    def __init__(self, scratch: Path | None = None, cache_dir: Path | None = None):
        shm = Path("/dev/shm")
        self.scratch = scratch or (shm if shm.is_dir() else Path(tempfile.gettempdir()))
        self.target = self.scratch / "slowmovie-frame.png"
        # Les sous-titres intégrés sont extraits UNE fois (le filtre `subtitles` relit sinon tout le fichier à
        # chaque image : 22 s -> près de 2 min par image sur un Pi Zero 2 W, mesuré) ; le cache survit au redémarrage.
        self.cache_dir = Path(cache_dir) if cache_dir else self.scratch / "slowmovie-subs"
        self.started = 0
        self._lock = threading.Lock()
        self._running: dict[str, threading.Thread] = {}
        self._failed: set[str] = set()

    # -- sous-titres intégrés ---------------------------------------------
    def _cache_path(self, video: Path, info: VideoInfo, track: int) -> Path:
        stat = video.stat()
        key = hashlib.sha1(f"{video.name}|{stat.st_size}|{stat.st_mtime_ns}|{track}".encode()).hexdigest()[:16]
        ext = "ass" if info.subtitle_codecs[track] in ("ass", "ssa") else "srt"
        return self.cache_dir / f"{key}.{ext}"

    def _embedded_file(self, video: Path, info: VideoInfo, track: int) -> Path | None:
        """Fichier de la piste si elle est déjà extraite ; sinon lance l'extraction en arrière-plan et renvoie None."""
        if not info.usable_subtitle_track(track):
            return None
        try:
            path = self._cache_path(video, info, track)
        except OSError:
            return None
        if path.exists():
            return path
        with self._lock:
            if path.name in self._failed or path.name in self._running:
                return None
            thread = threading.Thread(target=self._extract, args=(video, track, path), daemon=True)
            self._running[path.name] = thread
            self.started += 1
            thread.start()
        return None

    def _extract(self, video: Path, track: int, path: Path) -> None:
        tmp = path.with_name(path.stem + ".part" + path.suffix)
        try:
            log.info("extraction de la piste de sous-titres %s de %s…", track, video.name)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            codec = "ass" if path.suffix == ".ass" else "srt"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video), "-map", f"0:s:{track}",
                            "-c:s", codec, str(tmp)], check=True, capture_output=True, timeout=3600)
            os.replace(tmp, path)
            log.info("sous-titres de %s prêts", video.name)
        except Exception:  # noqa: BLE001 - une extraction ratée ne doit jamais gêner l'affichage
            log.warning("extraction des sous-titres de %s impossible", video.name)
            with self._lock:
                self._failed.add(path.name)
            tmp.unlink(missing_ok=True)
        finally:
            with self._lock:
                self._running.pop(path.name, None)

    def wait_for_extraction(self, timeout: float = 60) -> bool:
        """Attend la fin des extractions en cours (utile aux tests) ; vrai si plus rien ne tourne."""
        with self._lock:
            threads = list(self._running.values())
        for thread in threads:
            thread.join(timeout)
        with self._lock:
            return not self._running

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
        cached = self._embedded_file(video, info, settings.subtitle_track) if embedded else None
        if subtitle_file:
            stream = stream.filter("subtitles", self._safe_path(subtitle_file, "external"))
        elif embedded:
            if cached is not None:         # pas encore extraite : cette image part sans sous-titres
                stream = stream.filter("subtitles", self._safe_path(cached, "embedded"))
        elif settings.timecode:
            stream = stream.drawtext(escape_text=False, text="%{pts:hms}", fontcolor="white",
                                     fontsize=24, x="(w-text_w)/2", y="h-(text_h*2)",
                                     bordercolor="black", borderw=1)
        (stream.output(str(self.target), vframes=1, copyts=None)
         .overwrite_output().run(capture_stdout=True, capture_stderr=True))
        image = Image.open(self.target)
        image.load()
        return image

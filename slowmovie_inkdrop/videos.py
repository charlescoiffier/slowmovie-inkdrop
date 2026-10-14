"""Films : extensions, métadonnées ffprobe, progression, film suivant."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

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

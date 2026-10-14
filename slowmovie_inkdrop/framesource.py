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

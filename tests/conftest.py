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

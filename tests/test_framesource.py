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

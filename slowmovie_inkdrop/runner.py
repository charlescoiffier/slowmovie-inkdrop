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
from .library import Library
from .player import Player
from .relayclient import AckOutbox, CommandLog, relay_loop
from .slides import RelaySlideSource
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
    outbox = AckOutbox(paths.acks)
    command_log = CommandLog(paths.commands)
    player = Player(paths=paths, settings=settings, display=display, frames=FfmpegFrameSource(cache_dir=paths.base / "subtitles"),
                    image_queue=queue, library=library, slides=RelaySlideSource(),
                    on_done=lambda item, status: outbox.add(item.id, status))
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
    threading.Thread(target=relay_loop, args=(player, queue, outbox, stop, time.time, command_log),
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

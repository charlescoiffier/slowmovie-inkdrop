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

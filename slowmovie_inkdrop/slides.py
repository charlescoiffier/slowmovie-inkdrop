"""Diapositives des modes π-piquant et photos : le Pi les demande à la passerelle."""
from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from .config import Settings
from .imaging import caption_size_or_zero, clean_caption
from .relayclient import MAX_PIXELS, RelayClient, RelayError


class SlideError(RuntimeError):
    """Diapositive indisponible ; le message est affichable et ne contient jamais le lien de l'album."""


@dataclass
class Slide:
    image: Image.Image
    caption: str = ""
    caption_size: int = 0


class SlideSource(Protocol):
    def fetch(self, kind: str, settings: Settings) -> Slide: ...


class RelaySlideSource:
    def __init__(self, client_factory: Callable[[str, str], RelayClient] = RelayClient):
        self._factory = client_factory
        self._cached: tuple[tuple[str, str], RelayClient] | None = None

    def _client(self, settings: Settings) -> RelayClient:
        key = (settings.relay_url, settings.relay_token)
        if self._cached is None or self._cached[0] != key:
            self._cached = (key, self._factory(*key))
        return self._cached[1]

    def fetch(self, kind: str, settings: Settings) -> Slide:
        if not (settings.relay_url and settings.relay_token):
            raise SlideError("Relais non configuré.")
        if kind == "photos":
            if not settings.photos_album_url:
                raise SlideError("Aucun album n'est configuré.")
            body = {"source": "photos", "album_url": settings.photos_album_url}
        else:
            body = {"source": "pipiquant", "title": settings.pipiquant_title}
        try:
            data, caption, size = self._client(settings).slide(body)
        except RelayError as exc:
            raise SlideError(str(exc)) from None
        try:
            with Image.open(io.BytesIO(data)) as probe:
                if probe.width * probe.height > MAX_PIXELS:
                    raise ValueError("trop de pixels")
                image = probe.convert("RGB")
        except Exception:  # noqa: BLE001 - toute image douteuse est refusée
            raise SlideError("Image de diaporama illisible.") from None
        return Slide(image, clean_caption(caption, settings.caption_max_chars), caption_size_or_zero(size))

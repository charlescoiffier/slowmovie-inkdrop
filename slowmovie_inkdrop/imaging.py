"""Traitement des images : validation côté relais, mise en forme côté écran."""
from __future__ import annotations

import io

from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}   # MPO : JPEG multi-images des téléphones


class InvalidImage(ValueError):
    """Image refusée. `message` peut être montré tel quel à l'expéditeur."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code  # "too_large" | "format" | "corrupt"


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return img.convert("RGB")


def sanitize_upload(data: bytes, *, max_bytes: int = 10 * 1024 * 1024,
                    max_pixels: int = 40_000_000, max_side: int = 2000) -> bytes:
    if len(data) > max_bytes:
        raise InvalidImage(f"Image trop lourde ({max_bytes // (1024 * 1024)} Mo maximum).", "too_large")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise InvalidImage("Format non pris en charge : utilisez JPEG, PNG ou WebP.", "format")
            animated = getattr(probe, "n_frames", 1) > 1 or getattr(probe, "is_animated", False)
            if animated and probe.format != "MPO":      # un MPO n'est pas une animation : on garde l'image 1
                raise InvalidImage("Les images animées ne sont pas acceptées.", "format")
            if probe.width * probe.height > max_pixels:
                raise InvalidImage("Image trop grande (trop de pixels).", "too_large")
            probe.load()
            img = ImageOps.exif_transpose(probe)
            img = _flatten(img)
    except InvalidImage:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise InvalidImage("Fichier illisible ou corrompu.", "corrupt") from None
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def to_panel(img: Image.Image, size: tuple[int, int], *, contrast: float = 1.0) -> Image.Image:
    img = _flatten(img)
    img = ImageOps.pad(img, size, method=Image.LANCZOS, color=(255, 255, 255))
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    return img

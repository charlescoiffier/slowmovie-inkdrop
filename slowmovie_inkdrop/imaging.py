"""Traitement des images : validation côté relais, mise en forme côté écran."""
from __future__ import annotations

import functools
import io
import os
import unicodedata

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, UnidentifiedImageError

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


def clean_caption(text: str, max_chars: int) -> str:
    """Texte affichable : une ligne, sans caractère de contrôle ni invisible, tronqué à `max_chars`."""
    kept = []
    for char in text:
        if char in "\r\n\t":
            kept.append(" ")
        elif unicodedata.category(char).startswith(("C", "Z")) and char != " ":
            continue                    # contrôle, invisible, inversion de sens d'écriture, séparateurs
        else:
            kept.append(char)
    return " ".join("".join(kept).split())[:max_chars]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = word if not current else f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while draw.textlength(word, font=font) > max_width:     # mot plus large que l'écran
            cut = len(word)
            while cut > 1 and draw.textlength(word[:cut], font=font) > max_width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines


# Polices grasses avec accents, par ordre de préférence. `fonts-dejavu-core` est installé par deploy/install-pi.sh.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # Mac, pour le développement
    "/Library/Fonts/Arial Bold.ttf",
)


@functools.lru_cache(maxsize=None)
def find_caption_font(size: int):
    """Police du texte : `SLOWMOVIE_FONT`, sinon une police système avec accents, sinon celle de Pillow."""
    forced = os.environ.get("SLOWMOVIE_FONT")
    for path in ([forced] if forced else []) + list(FONT_CANDIDATES):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _has_glyph(font, char: str) -> bool:
    def render(c: str) -> bytes:
        im = Image.new("L", (80, 80))
        ImageDraw.Draw(im).text((10, 10), c, font=font, fill=255)
        return im.tobytes()

    return render(char) != render("\U0010FFFF")             # différent du carré « glyphe absent »


def _drawable(text: str, font) -> str:
    """Remplace ce que la police ne sait pas dessiner : sans accent, sinon « ? » (jamais de carré)."""
    out = []
    for char in text:
        if ord(char) < 128 or _has_glyph(font, char):
            out.append(char)
            continue
        base = unicodedata.normalize("NFKD", char).encode("ascii", "ignore").decode()
        out.append(base or "?")
    return "".join(out)


CAPTION_MIN_SIZE = 10      # bornes du corps demandé par le relais
CAPTION_MAX_SIZE = 20      # corps de départ (px) : discret sur l'écran e-paper 7,5"


def caption_size_or_zero(value) -> int:
    """Corps de légende demandé par le relais s'il est entier et dans les bornes, sinon 0 (corps par défaut)."""
    if isinstance(value, bool) or not isinstance(value, int) or not CAPTION_MIN_SIZE <= value <= CAPTION_MAX_SIZE:
        return 0
    return value


def draw_caption(img: Image.Image, text: str, *, margin: int = 12, bottom_margin: int = 40,
                 max_lines: int = 3, max_size: int = CAPTION_MAX_SIZE) -> Image.Image:
    """Dessine `text` en blanc cerclé de noir, centré en bas de l'image (copie ; l'original est intact).

    `margin` est la marge latérale ; `bottom_margin` laisse du vide sous le texte (cadre de l'écran)."""
    text = text.strip()
    if not text:
        return img
    out = img.convert("RGB").copy()
    width, height = out.size
    draw = ImageDraw.Draw(out)
    for size in range(max_size, 13, -2):                     # plus petit si le texte ne tient pas
        font = find_caption_font(size)
        lines = _wrap(draw, _drawable(text, font), font, width - 2 * margin)
        if len(lines) <= max_lines:
            break
    lines = lines[:max_lines]
    stroke = max(1, size // 10)
    ascent, descent = font.getmetrics()
    pitch = ascent + descent + stroke
    y = height - bottom_margin - pitch * len(lines)
    for line in lines:
        x = (width - draw.textlength(line, font=font)) / 2
        draw.text((x, y), line, font=font, fill="white", stroke_width=stroke, stroke_fill="black")
        y += pitch
    return out

import io

import pytest
from PIL import Image

from slowmovie_inkdrop.imaging import (
    InvalidImage,
    clean_caption,
    draw_caption,
    sanitize_upload,
    to_panel,
)


def _encode(img, fmt, **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def test_jpeg_exif_rotation_is_applied():
    img = Image.new("RGB", (40, 20), (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # tourner de 90°
    out = Image.open(io.BytesIO(sanitize_upload(_encode(img, "JPEG", exif=exif))))
    assert out.size == (20, 40)
    assert out.format == "PNG"


def test_output_has_no_metadata_and_is_downscaled():
    img = Image.new("RGB", (4000, 1000), "white")
    out = Image.open(io.BytesIO(sanitize_upload(_encode(img, "PNG"))))
    assert max(out.size) == 2000
    assert not out.info.get("exif")


def test_transparent_png_is_flattened_on_white():
    img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    out = Image.open(io.BytesIO(sanitize_upload(_encode(img, "PNG")))).convert("RGB")
    assert out.getpixel((5, 5)) == (255, 255, 255)


def test_animated_gif_rejected():
    frames = [Image.new("RGB", (8, 8), c) for c in ("red", "blue")]
    data = _encode(frames[0], "GIF", save_all=True, append_images=frames[1:])
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(data)
    assert exc.value.code == "format"


def test_svg_rejected():
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    assert exc.value.code in ("format", "corrupt")


def test_truncated_file_rejected():
    data = _encode(Image.new("RGB", (200, 200), "red"), "PNG")
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(data[:60])
    assert exc.value.code == "corrupt"


def test_too_many_bytes_rejected():
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(b"x" * 11, max_bytes=10)
    assert exc.value.code == "too_large"


def test_too_many_pixels_rejected():
    data = _encode(Image.new("RGB", (100, 100), "white"), "PNG")
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(data, max_pixels=1000)
    assert exc.value.code == "too_large"


def test_messages_are_french_and_safe():
    with pytest.raises(InvalidImage) as exc:
        sanitize_upload(b"n'importe quoi")
    assert "Traceback" not in exc.value.message and exc.value.message


def test_to_panel_pads_portrait_to_exact_size():
    out = to_panel(Image.new("RGB", (100, 400), "black"), (800, 480))
    assert out.size == (800, 480) and out.mode == "RGB"
    assert out.getpixel((0, 240)) == (255, 255, 255)  # bandes blanches à gauche


def test_to_panel_contrast_changes_pixels():
    base = Image.new("RGB", (800, 480), (100, 100, 100))
    base.paste((200, 200, 200), (400, 0, 800, 480))        # image non uniforme
    assert to_panel(base, (800, 480), contrast=2.0).getpixel((5, 5)) != (100, 100, 100)


def test_multi_picture_jpeg_from_a_phone_is_accepted_using_its_first_picture():
    first = Image.new("RGB", (60, 40), "red")
    second = Image.new("RGB", (30, 20), "blue")                  # ex. carte de gain Ultra HDR
    data = _encode(first, "MPO", save_all=True, append_images=[second])
    assert Image.open(io.BytesIO(data)).format == "MPO"
    out = Image.open(io.BytesIO(sanitize_upload(data)))
    assert out.size == (60, 40) and out.format == "PNG"


def test_clean_caption_removes_control_and_invisible_characters():
    assert clean_caption("a\nb\x00c   d\t\u202eE\u200b", 128) == "a bc d E"


def test_clean_caption_truncates_to_the_maximum():
    assert len(clean_caption("x" * 300, 128)) == 128
    assert clean_caption("  Été à Paris — œuf  ", 128) == "Été à Paris — œuf"


def _bbox_of_change(before, after):
    from PIL import ImageChops
    return ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox()


def test_draw_caption_without_text_leaves_the_image_untouched():
    base = Image.new("RGB", (800, 480), (120, 120, 120))
    assert _bbox_of_change(base, draw_caption(base, "")) is None
    assert _bbox_of_change(base, draw_caption(base, "   ")) is None


def test_draw_caption_is_centered_at_the_bottom_and_leaves_the_rest_alone():
    base = Image.new("RGB", (800, 480), (120, 120, 120))
    left, top, right, bottom = _bbox_of_change(base, draw_caption(base, "Bonjour tout le monde"))
    assert abs((left + right) / 2 - 400) <= 3
    assert top > 480 * 0.7 and bottom <= 480
    assert base.getpixel((400, 100)) == (120, 120, 120)


def test_draw_caption_text_is_large_and_keeps_a_bottom_margin():
    base = Image.new("RGB", (800, 480), (120, 120, 120))
    left, top, right, bottom = _bbox_of_change(base, draw_caption(base, "Bonjour"))
    assert 15 <= bottom - top <= 30                # discret : corps de 20 px au plus
    assert bottom <= 480 - 40                      # marge par défaut : la marquise en carton masque le bas
    _, _, _, low = _bbox_of_change(base, draw_caption(base, "Bonjour", bottom_margin=100))
    assert low <= 480 - 100
    _, _, _, flush = _bbox_of_change(base, draw_caption(base, "Bonjour", bottom_margin=0))
    assert flush > 480 - 40


def test_draw_caption_does_not_modify_its_input():
    base = Image.new("RGB", (800, 480), (120, 120, 120))
    draw_caption(base, "Bonjour")
    assert base.getpixel((400, 470)) == (120, 120, 120)


def test_draw_caption_longest_text_stays_inside_the_image_on_a_few_lines():
    base = Image.new("RGB", (800, 480), (120, 120, 120))
    for text in ("W" * 128, "mot " * 32, "x" * 128, "Été à Paris — œuf " * 7):
        left, top, right, bottom = _bbox_of_change(base, draw_caption(base, text[:128]))
        assert left >= 0 and right <= 800 and bottom <= 480
        assert top > 480 * 0.6          # au plus trois lignes dans le bas de l'image


FRENCH = "éèêëàâîïôöùûüçœÉÀÇ’«»—"


def _renders_as_glyph(font, char):
    from PIL import ImageDraw

    def render(c):
        im = Image.new("L", (80, 80))
        ImageDraw.Draw(im).text((10, 10), c, font=font, fill=255)
        return im.tobytes()

    return render(char) != render("\U0010FFFF")           # différent du carré « glyphe absent »


def test_the_caption_font_draws_french_letters_when_a_system_font_is_available():
    import pytest

    from slowmovie_inkdrop.imaging import find_caption_font
    font = find_caption_font(28)
    if not _renders_as_glyph(font, "é"):
        pytest.skip("aucune police système avec accents (installer fonts-dejavu-core)")
    assert all(_renders_as_glyph(font, c) for c in FRENCH)


def test_without_a_font_that_has_accents_they_are_dropped_instead_of_drawn_as_boxes(monkeypatch):
    from PIL import ImageFont

    from slowmovie_inkdrop import imaging
    monkeypatch.setattr(imaging, "find_caption_font", lambda size: ImageFont.load_default(size))
    base = Image.new("RGB", (800, 480), (120, 120, 120))
    assert not _renders_as_glyph(ImageFont.load_default(28), "é")        # le défaut de Pillow n'a pas les accents
    accented = draw_caption(base, "Été à Paris")
    plain = draw_caption(base, "Ete a Paris")
    assert accented.tobytes() == plain.tobytes()


def test_a_character_no_font_has_is_replaced_by_a_question_mark(monkeypatch):
    from PIL import ImageFont

    from slowmovie_inkdrop import imaging
    monkeypatch.setattr(imaging, "find_caption_font", lambda size: ImageFont.load_default(size))
    base = Image.new("RGB", (800, 480), (120, 120, 120))
    assert draw_caption(base, "a😀b").tobytes() == draw_caption(base, "a?b").tobytes()


def test_font_can_be_forced_with_an_environment_variable(monkeypatch, tmp_path):
    from slowmovie_inkdrop.imaging import find_caption_font
    find_caption_font.cache_clear()
    monkeypatch.setenv("SLOWMOVIE_FONT", str(tmp_path / "absente.ttf"))
    assert find_caption_font(20) is not None          # police introuvable : repli, pas d'exception
    find_caption_font.cache_clear()

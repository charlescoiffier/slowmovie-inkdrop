import io

import pytest
from PIL import Image

from slowmovie_inkdrop.imaging import InvalidImage, sanitize_upload, to_panel


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

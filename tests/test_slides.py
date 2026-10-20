import io
import json
from urllib.parse import quote

import httpx
import pytest
from PIL import Image

from slowmovie_inkdrop.config import Settings
from slowmovie_inkdrop.relayclient import RelayClient, RelayError
from slowmovie_inkdrop.slides import RelaySlideSource, SlideError

ALBUM = "https://photos.google.com/share/AF1QipSECRET?key=KEYKEY"


def png(size=(20, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, "PNG")
    return buf.getvalue()


class Relay:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.content = png()
        self.headers = {"content-type": "image/png", "x-slide-caption": quote("π-piquant • 1 = 10° • 5 décimales", safe=""),
                        "x-slide-caption-size": "14"}
        self.json_error: object = {"detail": "Album illisible."}
        self.error: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.status >= 400:
            return httpx.Response(self.status, json=self.json_error)
        return httpx.Response(200, content=self.content, headers=self.headers)


def client(relay, **kw):
    return RelayClient("https://relais.test", "jeton", client=httpx.Client(transport=httpx.MockTransport(relay)), **kw)


def source(relay):
    return RelaySlideSource(lambda url, token: client(relay))


SETTINGS = Settings(relay_url="https://relais.test", relay_token="jeton", photos_album_url=ALBUM)


def test_the_client_posts_the_body_as_json_and_decodes_the_caption():
    relay = Relay()
    data, caption, size = client(relay).slide({"source": "pipiquant", "title": True})
    request = relay.requests[0]
    assert request.method == "POST" and request.url.path == "/api/device/slide" and request.url.query == b""
    assert json.loads(request.content) == {"source": "pipiquant", "title": True}
    assert request.headers["authorization"] == "Bearer jeton"
    assert data[:4] == b"\x89PNG" and caption == "π-piquant • 1 = 10° • 5 décimales" and size == 14


def test_the_album_link_travels_in_the_body_never_in_the_address():
    relay = Relay()
    client(relay).slide({"source": "photos", "album_url": ALBUM})
    request = relay.requests[0]
    assert "SECRET" not in str(request.url) and ALBUM.encode() in request.content


def test_the_client_cuts_oversized_answers():
    relay = Relay()
    relay.content = b"x" * 500
    with pytest.raises(RelayError, match="lourde"):
        client(relay, max_download=100).slide({"source": "pipiquant", "title": True})


def test_the_client_reports_the_relay_detail():
    relay = Relay()
    relay.status = 502
    with pytest.raises(RelayError) as caught:
        client(relay).slide({"source": "photos", "album_url": ALBUM})
    assert "Album illisible." in str(caught.value) and caught.value.status_code == 502
    relay.json_error = "pas un objet"
    with pytest.raises(RelayError):
        client(relay).slide({"source": "photos", "album_url": ALBUM})


def test_a_pipiquant_slide():
    relay = Relay()
    slide = source(relay).fetch("pipiquant", Settings(relay_url="https://relais.test", relay_token="jeton",
                                                       pipiquant_title=False))
    assert json.loads(relay.requests[0].content) == {"source": "pipiquant", "title": False}
    assert slide.image.size == (20, 10) and slide.caption.startswith("π-piquant") and slide.caption_size == 14


def test_a_photos_slide_sends_the_album():
    relay = Relay()
    relay.headers = {"content-type": "image/png", "x-slide-caption": "", "x-slide-caption-size": "0"}
    slide = source(relay).fetch("photos", SETTINGS)
    assert json.loads(relay.requests[0].content) == {"source": "photos", "album_url": ALBUM}
    assert slide.caption == "" and slide.caption_size == 0


@pytest.mark.parametrize("raw, expected", [("14", 14), ("9", 0), ("21", 0), ("abc", 0), ("", 0), ("-3", 0)])
def test_the_caption_size_is_validated(raw, expected):
    relay = Relay()
    relay.headers["x-slide-caption-size"] = raw
    assert source(relay).fetch("pipiquant", SETTINGS).caption_size == expected


def test_the_caption_is_cleaned_and_cut():
    relay = Relay()
    relay.headers["x-slide-caption"] = quote("ok‮" + "z" * 400, safe="")
    caption = source(relay).fetch("pipiquant", Settings(relay_url="https://relais.test", relay_token="jeton",
                                                        caption_max_chars=30)).caption
    assert caption.startswith("ok") and len(caption) == 30 and "‮" not in caption


@pytest.mark.parametrize("content", [b"pas une image", b"", png((8000, 8000))])
def test_unreadable_or_enormous_images_are_refused(content):
    relay = Relay()
    relay.content = content
    with pytest.raises(SlideError):
        source(relay).fetch("pipiquant", SETTINGS)


def test_unconfigured_relay_and_missing_album():
    relay = Relay()
    with pytest.raises(SlideError, match="non configuré"):
        source(relay).fetch("pipiquant", Settings())
    with pytest.raises(SlideError, match="album"):
        source(relay).fetch("photos", Settings(relay_url="https://relais.test", relay_token="jeton"))
    assert not relay.requests


def test_relay_errors_become_slide_errors_without_the_album_link():
    relay = Relay()
    relay.status = 502
    with pytest.raises(SlideError) as caught:
        source(relay).fetch("photos", SETTINGS)
    assert "Album illisible." in str(caught.value) and "SECRET" not in str(caught.value)
    relay.status = 200
    relay.error = httpx.ConnectError("éteint")
    with pytest.raises(SlideError) as caught:
        source(relay).fetch("photos", SETTINGS)
    assert "SECRET" not in str(caught.value) and "KEYKEY" not in str(caught.value)


def test_the_client_is_reused_while_the_relay_settings_do_not_change():
    made = []
    relay = Relay()

    def factory(url, token):
        made.append((url, token))
        return client(relay)

    src = RelaySlideSource(factory)
    src.fetch("pipiquant", SETTINGS)
    src.fetch("pipiquant", SETTINGS)
    src.fetch("pipiquant", Settings(relay_url="https://autre.test", relay_token="jeton"))
    assert made == [("https://relais.test", "jeton"), ("https://autre.test", "jeton")]

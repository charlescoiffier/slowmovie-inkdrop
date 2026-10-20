import io
import logging
import random
from urllib.parse import parse_qs, unquote

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_relay_api import DEVICE, PASSWORD, TOKEN, Clock, png
from test_relay_photos import LONG, SHORT, Web
from test_relay_pipiquant import BASE, KEY, Extreme

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1000, 600), "green").save(buf, "JPEG")
    return buf.getvalue()


class World:
    """Faux service π-piquant et faux Google Photos derrière un seul transport."""

    def __init__(self):
        self.web = Web()
        self.web.image = httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        self.pi_calls: list[httpx.Request] = []
        self.pi_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "pi-piquant.test":
            self.pi_calls.append(request)
            return httpx.Response(self.pi_status, content=png((800, 480), "white"),
                                  headers={"content-type": "image/png"})
        return self.web(request)


def make(tmp_path, *, key=KEY, rng=None):
    world = World()
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password(PASSWORD), device_token=TOKEN,
                         pipiquant_api_url=BASE, pipiquant_api_key=key)
    app = create_app(config, clock=Clock(), http_client=httpx.Client(transport=httpx.MockTransport(world)), rng=rng)
    return TestClient(app), world


def slide(client, body, headers=DEVICE):
    return client.post("/api/device/slide", json=body, headers=headers)


def query(request):
    return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}


def test_the_device_token_is_required(tmp_path):
    client, world = make(tmp_path)
    assert slide(client, {"source": "pipiquant", "title": True}, headers={}).status_code == 401
    assert slide(client, {"source": "pipiquant", "title": True}, headers={"Authorization": "Bearer x"}).status_code == 401
    assert not world.pi_calls


def test_a_pipiquant_slide_with_title(tmp_path):
    client, world = make(tmp_path, rng=random.Random(3))
    r = slide(client, {"source": "pipiquant", "title": True})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    q = query(world.pi_calls[0])
    assert q["source"] == "pi" and q["taille"] == "800" and q["titre"] == "non"
    assert world.pi_calls[0].headers["authorization"] == f"Bearer {KEY}"
    caption = unquote(r.headers["x-slide-caption"])
    word = "décimale" if q["decimales"] == "1" else "décimales"
    assert caption == f"π-piquant • 1 = {q['angle']}° • {q['decimales']} {word}"
    assert r.headers["x-slide-caption-size"] == "14"


def test_a_pipiquant_slide_without_title(tmp_path):
    client, _ = make(tmp_path)
    r = slide(client, {"source": "pipiquant", "title": False})
    assert r.status_code == 200 and r.headers["x-slide-caption"] == "" and r.headers["x-slide-caption-size"] == "0"


@pytest.mark.parametrize("pick,expected", [("lo", (1, 1)), ("hi", (200, 359))])
def test_slide_draw_bounds_are_inclusive(tmp_path, pick, expected):
    rng = Extreme(pick)
    client, world = make(tmp_path, rng=rng)
    assert slide(client, {"source": "pipiquant", "title": True}).status_code == 200
    q = query(world.pi_calls[0])
    assert (int(q["decimales"]), int(q["angle"])) == expected
    assert rng.calls == [(1, 200), (1, 359)]


def test_pipiquant_is_unavailable_without_a_key_or_when_the_service_is_saturated(tmp_path):
    client, world = make(tmp_path, key="")
    assert slide(client, {"source": "pipiquant", "title": True}).status_code == 503
    assert not world.pi_calls
    client, world = make(tmp_path / "b")
    world.pi_status = 503
    assert slide(client, {"source": "pipiquant", "title": True}).status_code == 503
    world.pi_status = 500
    assert slide(client, {"source": "pipiquant", "title": True}).status_code == 502


def test_a_photo_slide(tmp_path):
    client, world = make(tmp_path)
    r = slide(client, {"source": "photos", "album_url": SHORT})
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert r.headers["x-slide-caption"] == "" and r.headers["x-slide-caption-size"] == "0"
    assert not world.pi_calls
    assert Image.open(io.BytesIO(r.content)).size == (1000, 600)


@pytest.mark.parametrize("url", ["https://example.com/x", "http://photos.google.com/share/x", "", "https://photos.google.com"])
def test_an_invalid_album_link_is_refused_without_echo(tmp_path, url, caplog):
    caplog.set_level(logging.DEBUG)
    client, world = make(tmp_path)
    r = slide(client, {"source": "photos", "album_url": url})
    assert r.status_code == 422 and "example.com" not in r.text and "photos.google.com/share" not in r.text
    assert not world.web.calls


def test_an_unreadable_album_is_a_readable_502(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    client, world = make(tmp_path)
    world.web.album = httpx.Response(200, text="<html>vide</html>")
    r = slide(client, {"source": "photos", "album_url": LONG})
    assert r.status_code == 502 and "illisible" in r.json()["detail"]
    assert "SECRET" not in r.text and "SECRET" not in caplog.text and "KEYKEY" not in caplog.text
    world.web.error = httpx.ConnectError("x")
    r = slide(client, {"source": "photos", "album_url": SHORT})
    assert r.status_code == 502 and "injoignable" in r.json()["detail"]


def test_a_photo_that_is_not_a_picture_is_a_502(tmp_path):
    client, world = make(tmp_path)
    world.web.image = httpx.Response(200, content=b"pas une image", headers={"content-type": "image/jpeg"})
    r = slide(client, {"source": "photos", "album_url": LONG})
    assert r.status_code == 502


@pytest.mark.parametrize("body", [
    {"source": "autre"}, {}, {"source": "pipiquant"}, {"source": "pipiquant", "title": "oui"},
    {"source": "photos"}, {"source": "photos", "album_url": 5}, [], "texte", None,
])
def test_invalid_bodies_are_refused(tmp_path, body):
    client, world = make(tmp_path)
    assert slide(client, body).status_code == 422
    assert not world.pi_calls and not world.web.calls


def test_a_huge_body_is_refused(tmp_path):
    client, _ = make(tmp_path)
    r = client.post("/api/device/slide", content=b'{"source":"photos","album_url":"' + b"a" * 5000 + b'"}',
                    headers={**DEVICE, "content-type": "application/json"})
    assert r.status_code == 413


def test_slides_do_not_use_the_queue_or_the_send_limit(tmp_path):
    client, _ = make(tmp_path)
    for _ in range(25):
        assert slide(client, {"source": "pipiquant", "title": False}).status_code == 200
    assert client.get("/api/device/pending", headers=DEVICE).json() == []


def test_an_unexpected_failure_while_reading_an_album_is_a_clean_502(tmp_path, caplog):
    import logging as _logging
    caplog.set_level(_logging.DEBUG)

    class Exploding:
        def photo(self, url):
            raise RuntimeError(f"boom {url}")

    world = World()
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password(PASSWORD), device_token=TOKEN,
                         pipiquant_api_url=BASE, pipiquant_api_key=KEY)
    client = TestClient(create_app(config, clock=Clock(), http_client=httpx.Client(transport=httpx.MockTransport(world)),
                                   album_photos=Exploding()))
    r = slide(client, {"source": "photos", "album_url": LONG})
    assert r.status_code == 502 and "illisible" in r.json()["detail"]
    assert "SECRET" not in r.text and "SECRET" not in caplog.text and "boom" not in r.text

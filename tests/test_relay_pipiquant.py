import random
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from test_relay_api import DEVICE, PASSWORD, TOKEN, Clock, png

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password

KEY = "k" * 24
BASE = "https://pi-piquant.test"


class Api:
    """Faux service π-piquant : enregistre les appels et répond comme réglé."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.status = 200
        self.body = png((800, 480), "white")
        self.content_type = "image/png"
        self.error: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.error:
            raise self.error
        return httpx.Response(self.status, content=self.body, headers={"content-type": self.content_type})


def make(tmp_path, *, key=KEY, rng=None, **overrides):
    api = Api()
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password(PASSWORD), device_token=TOKEN,
                         pipiquant_api_url=BASE, pipiquant_api_key=key, **overrides)
    client = httpx.Client(transport=httpx.MockTransport(api))
    clock = Clock()
    app = create_app(config, clock=clock, http_client=client, rng=rng)
    test_client = TestClient(app)
    test_client.clock = clock
    return test_client, api


@pytest.fixture
def env(tmp_path):
    return make(tmp_path)


def draw(client, *, password=PASSWORD, duration="300"):
    return client.post("/api/send-pipiquant", data={"password": password, "duration": duration})


def query(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}


def test_a_draw_calls_the_api_with_the_key_and_default_settings(env):
    client, api = env
    r = draw(client)
    assert r.status_code == 202
    (call,) = api.calls
    assert call.method == "GET" and call.url.path == "/api/png"
    assert call.headers["authorization"] == f"Bearer {KEY}"
    q = query(call)
    assert set(q) == {"source", "decimales", "angle", "taille", "titre"}
    assert q["titre"] == "non"                      # la légende est écrite par le Pi, plus grosse
    assert q["source"] == "pi" and q["taille"] == "800"
    body = r.json()
    assert len(body["id"]) == 32 and body["position"] == 1
    assert (body["decimals"], body["angle"]) == (int(q["decimales"]), int(q["angle"]))


def test_the_legend_travels_as_the_caption(tmp_path):
    client, api = make(tmp_path, rng=Extreme("hi"))
    image_id = draw(client).json()["id"]
    (item,) = client.get("/api/device/pending", headers=DEVICE).json()
    assert item["id"] == image_id
    assert item["caption"] == "π-piquant • 1 = 359° • 200 décimales"
    client2, _ = make(tmp_path / "b", rng=Extreme("lo"))
    draw(client2)
    assert client2.get("/api/device/pending", headers=DEVICE).json()[0]["caption"] == "π-piquant • 1 = 1° • 1 décimale"


def test_the_image_is_queued_like_an_upload(env):
    client, _ = env
    image_id = draw(client, duration="120").json()["id"]
    assert client.get(f"/api/status/{image_id}").json()["status"] == "queued"
    (item,) = client.get("/api/device/pending", headers=DEVICE).json()
    assert item["id"] == image_id and item["duration"] == 120
    assert client.get(f"/api/device/image/{image_id}", headers=DEVICE).content.startswith(b"\x89PNG")


class Extreme:
    def __init__(self, pick):
        self.pick = pick
        self.calls = []

    def randint(self, lo, hi):
        self.calls.append((lo, hi))
        return lo if self.pick == "lo" else hi


@pytest.mark.parametrize("pick,expected", [("lo", (1, 1)), ("hi", (200, 359))])
def test_the_bounds_are_inclusive(tmp_path, pick, expected):
    rng = Extreme(pick)
    client, api = make(tmp_path, rng=rng)
    assert draw(client).status_code == 202
    q = query(api.calls[0])
    assert (int(q["decimales"]), int(q["angle"])) == expected
    assert rng.calls == [(1, 200), (1, 359)]


def test_many_draws_stay_in_range_and_vary(tmp_path):
    client, api = make(tmp_path, rng=random.Random(7), queue_max=1000)
    for _ in range(300):
        assert draw(client).status_code == 202
        client.clock.t += 4000      # hors de la fenêtre de limitation des envois
    values = [(int(query(c)["decimales"]), int(query(c)["angle"])) for c in api.calls]
    assert all(1 <= d <= 200 and 1 <= a <= 359 for d, a in values)
    assert len({d for d, _ in values}) > 50 and len({a for _, a in values}) > 100


def test_the_default_rng_is_not_a_fixed_sequence(tmp_path):
    client, api = make(tmp_path, queue_max=1000)
    for _ in range(30):
        draw(client)
        client.clock.t += 4000
    assert len({(query(c)["decimales"], query(c)["angle"]) for c in api.calls}) > 20


def test_wrong_password_never_reaches_the_api(env):
    client, api = env
    r = draw(client, password="faux")
    assert r.status_code == 401 and not api.calls


def test_bad_duration_never_reaches_the_api(env):
    client, api = env
    for bad in ("", "abc", "10", "99999", "-5"):
        assert draw(client, duration=bad).status_code == 422
    assert not api.calls


def test_failed_passwords_are_rate_limited(env):
    client, api = env
    for _ in range(5):
        draw(client, password="faux")
    assert draw(client).status_code == 429 and not api.calls


def test_recent_sends_are_limited_like_uploads(tmp_path):
    client, api = make(tmp_path, queue_max=1000)
    codes = [draw(client).status_code for _ in range(40)]
    assert 429 in codes and codes.index(429) == len(api.calls)


def test_a_full_queue_does_not_call_the_api(tmp_path):
    client, api = make(tmp_path, queue_max=1)
    assert draw(client).status_code == 202
    r = draw(client)
    assert r.status_code == 409 and len(api.calls) == 1


def test_the_feature_is_off_without_a_key(tmp_path):
    client, api = make(tmp_path, key="")
    assert client.get("/api/config").json()["pipiquant"] is False
    assert draw(client).status_code == 503 and not api.calls


def test_the_config_announces_the_feature(env):
    client, _ = env
    assert client.get("/api/config").json()["pipiquant"] is True


@pytest.mark.parametrize("status,expected", [(401, 502), (403, 502), (404, 502), (500, 502), (503, 503)])
def test_api_errors_become_readable_errors(tmp_path, status, expected):
    client, api = make(tmp_path)
    api.status = status
    api.body = b'{"error": "secret interne"}'
    api.content_type = "application/json"
    r = draw(client)
    assert r.status_code == expected
    detail = r.json()["detail"]
    assert "π-piquant" in detail and "secret interne" not in detail and KEY not in detail
    assert client.get("/api/device/pending", headers=DEVICE).json() == []


@pytest.mark.parametrize("error", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
def test_unreachable_api(tmp_path, error):
    client, api = make(tmp_path)
    api.error = error
    r = draw(client)
    assert r.status_code == 502 and "π-piquant" in r.json()["detail"]


def test_a_non_image_answer_is_refused(tmp_path):
    client, api = make(tmp_path)
    api.body, api.content_type = b"<html>pas une image</html>", "text/html"
    assert draw(client).status_code == 502
    api.body, api.content_type = b"pas du png", "image/png"
    assert draw(client).status_code == 502


def test_an_oversized_answer_is_refused(tmp_path):
    client, api = make(tmp_path, max_upload_bytes=1000)
    api.body = png((800, 480), "red") + b"0" * 5000
    r = draw(client)
    assert r.status_code == 502 and "trop lourde" in r.json()["detail"]   # coupé pendant la lecture, avant tout décodage


def test_a_body_without_length_or_too_big_is_refused_before_parsing(env):
    client, api = env
    def chunks():
        yield b"password=x&duration=300"
    r = client.post("/api/send-pipiquant", content=chunks(), headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 411
    big = client.post("/api/send-pipiquant", data={"password": "x" * 70000, "duration": "300"})
    assert big.status_code == 413 and not api.calls


def test_the_main_page_has_the_pipiquant_tab(env):
    client, _ = env
    html = client.get("/").text
    for expected in ('id="tab-pi"', 'id="panel-pi"', "/api/send-pipiquant", "c.pipiquant"):
        assert expected in html, expected


def test_the_confirmation_page_shows_the_drawing_parameters(env):
    client, _ = env
    html = client.get("/envoi/" + "a" * 32).text
    assert "décimales" in html and "location.search" in html

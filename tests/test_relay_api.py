import io
import logging
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password

PASSWORD = "correct horse battery"
TOKEN = "t" * 40
DEVICE = {"Authorization": f"Bearer {TOKEN}"}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def png(size=(50, 30), color="blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password(PASSWORD), device_token=TOKEN)
    return TestClient(create_app(config, clock=clock)), clock, config


def send(client, *, password=PASSWORD, duration="300", data=None, name="p.png", mime="image/png"):
    return client.post("/api/send", data={"password": password, "duration": duration},
                       files={"image": (name, data if data is not None else png(), mime)})


def test_page_and_config(env):
    client, *_ = env
    assert client.get("/").status_code == 200 and "<form" in client.get("/").text
    assert client.get("/api/config").json() == {"min_duration": 60, "max_duration": 3600, "max_mb": 10,
                                                "caption_max": 128, "admin": False, "pipiquant": False}


def test_upload_ok_returns_position_and_eta(env):
    client, *_ = env
    r = send(client)
    assert r.status_code == 202
    body = r.json()
    assert body["position"] == 1 and body["eta_seconds"] == 0 and len(body["id"]) == 32
    r2 = send(client, duration="120")
    assert r2.json()["position"] == 2 and r2.json()["eta_seconds"] == 300


def test_wrong_password(env):
    client, *_ = env
    assert send(client, password="faux").status_code == 401


def test_empty_password_is_refused_not_crashed(env):
    client, *_ = env
    assert send(client, password="").status_code == 401


def test_lockout_after_five_failures_even_with_good_password(env):
    client, clock, _ = env
    for _ in range(5):
        send(client, password="faux")
    r = send(client)
    assert r.status_code == 429 and "minute" in r.json()["detail"]
    clock.t += 901
    assert send(client).status_code == 202


def test_password_never_logged(env, caplog):
    client, *_ = env
    with caplog.at_level(logging.DEBUG):
        send(client, password="Secret-à-ne-pas-logger")
        send(client)
    assert "Secret-à-ne-pas-logger" not in caplog.text and PASSWORD not in caplog.text


@pytest.mark.parametrize("duration", ["", "abc", "1.5", "-5", "0", "59", "3601", "9" * 30, " 300x"])
def test_bad_durations(env, duration):
    client, *_ = env
    r = send(client, duration=duration)
    assert r.status_code == 422 and "durée" in r.json()["detail"].lower()


def test_svg_rejected_as_unsupported(env):
    client, *_ = env
    r = send(client, data=b'<svg xmlns="http://www.w3.org/2000/svg"/>', name="a.svg", mime="image/svg+xml")
    assert r.status_code in (415, 422)


def test_corrupt_image_rejected(env):
    client, *_ = env
    assert send(client, data=png()[:40]).status_code == 422


def test_oversize_rejected(env, tmp_path):
    noisy = io.BytesIO()
    Image.frombytes("RGB", (100, 100), os.urandom(30000)).save(noisy, "PNG")   # incompressible
    config = RelayConfig(data_dir=tmp_path / "x", password_hash=hash_password(PASSWORD),
                         device_token=TOKEN, max_upload_bytes=1000)
    client = TestClient(create_app(config))
    assert send(client, data=noisy.getvalue()).status_code == 413


def test_queue_full_is_refused_with_message(env, tmp_path):
    config = RelayConfig(data_dir=tmp_path / "y", password_hash=hash_password(PASSWORD),
                         device_token=TOKEN, queue_max=2)
    client = TestClient(create_app(config))
    assert send(client).status_code == 202 and send(client).status_code == 202
    r = send(client)
    assert r.status_code == 409 and "file" in r.json()["detail"].lower()


def test_device_endpoints_require_token(env):
    client, *_ = env
    assert client.get("/api/device/pending").status_code == 401
    assert client.get("/api/device/pending", headers={"Authorization": "Bearer faux"}).status_code == 401


def test_device_flow_pending_fetch_ack(env):
    client, *_ = env
    image_id = send(client, duration="120").json()["id"]
    pending = client.get("/api/device/pending", headers=DEVICE).json()
    assert pending == [{"id": image_id, "duration": 120, "caption": "", "caption_size": 0}]
    data = client.get(f"/api/device/image/{image_id}", headers=DEVICE)
    assert data.status_code == 200 and data.content[:4] == b"\x89PNG"
    assert client.get("/api/device/pending", headers=DEVICE).json() == []   # déjà récupérée
    assert client.get(f"/api/status/{image_id}").json()["status"] == "fetched"
    r = client.post(f"/api/device/ack/{image_id}", headers=DEVICE, json={"status": "shown"})
    assert r.status_code == 200
    assert client.get(f"/api/status/{image_id}").json()["status"] == "shown"
    assert client.get(f"/api/device/image/{image_id}", headers=DEVICE).status_code == 404


def test_ack_failed_frees_queue(env):
    client, *_ = env
    image_id = send(client).json()["id"]
    client.get(f"/api/device/image/{image_id}", headers=DEVICE)
    client.post(f"/api/device/ack/{image_id}", headers=DEVICE, json={"status": "failed"})
    assert client.get(f"/api/status/{image_id}").json()["status"] == "failed"


def test_ack_rejects_unknown_status(env):
    client, *_ = env
    image_id = send(client).json()["id"]
    r = client.post(f"/api/device/ack/{image_id}", headers=DEVICE, json={"status": "nimporte"})
    assert r.status_code == 422


def test_unknown_id_is_404_and_path_traversal_is_safe(env):
    client, *_ = env
    assert client.get("/api/status/" + "0" * 32).status_code == 404
    assert client.get("/api/device/image/..%2F..%2Fetc%2Fpasswd", headers=DEVICE).status_code in (404, 422)


def test_unacked_items_expire_and_free_the_queue(env):
    client, clock, config = env
    image_id = send(client).json()["id"]
    client.get(f"/api/device/image/{image_id}", headers=DEVICE)    # récupérée, accusé jamais reçu
    clock.t += config.expiry_seconds + 1
    client.get("/api/device/pending", headers=DEVICE)               # déclenche le nettoyage
    assert client.get(f"/api/status/{image_id}").json()["status"] == "expired"


def test_upload_without_content_length_is_refused(env):
    client, *_ = env
    r = client.post("/api/send", content=iter([b"--x\r\n", b"--x--\r\n"]),
                    headers={"content-type": "multipart/form-data; boundary=x",
                             "transfer-encoding": "chunked"})
    assert r.status_code == 411


def test_fetched_but_unacked_item_is_offered_again_after_the_lease(env):
    client, clock, config = env
    image_id = send(client).json()["id"]
    assert client.get(f"/api/device/image/{image_id}", headers=DEVICE).status_code == 200
    assert client.get("/api/device/pending", headers=DEVICE).json() == []     # bail en cours
    clock.t += config.lease_seconds + 1                                       # la réponse s'est perdue
    assert [i["id"] for i in client.get("/api/device/pending", headers=DEVICE).json()] == [image_id]
    assert client.get(f"/api/device/image/{image_id}", headers=DEVICE).status_code == 200


def _send_caption(client, caption):
    return client.post("/api/send", data={"password": PASSWORD, "duration": "300", "caption": caption},
                       files={"image": ("p.png", png(), "image/png")})


def test_caption_is_stored_cleaned_and_offered_to_the_device(env):
    client, *_ = env
    r = _send_caption(client, "  Joyeux\nanniversaire\x00 Zoé !  ")
    assert r.status_code == 202
    pending = client.get("/api/device/pending", headers=DEVICE).json()
    assert pending[0]["caption"] == "Joyeux anniversaire Zoé !"


def test_caption_of_exactly_the_maximum_is_accepted_and_one_more_is_refused(env):
    client, *_ = env
    assert _send_caption(client, "é" * 128).status_code == 202
    r = _send_caption(client, "é" * 129)
    assert r.status_code == 422 and "128" in r.json()["detail"]


def test_caption_limit_is_configurable(tmp_path):
    config = RelayConfig(data_dir=tmp_path / "c", password_hash=hash_password(PASSWORD),
                         device_token=TOKEN, caption_max=10)
    client = TestClient(create_app(config))
    assert client.get("/api/config").json()["caption_max"] == 10
    assert _send_caption(client, "x" * 11).status_code == 422


def test_caption_is_optional(env):
    client, *_ = env
    assert send(client).status_code == 202
    assert client.get("/api/device/pending", headers=DEVICE).json()[0]["caption"] == ""


def test_caption_of_only_invisible_characters_counts_as_empty(env):
    client, *_ = env
    assert _send_caption(client, "\u200b\u202e \n").status_code == 202
    assert client.get("/api/device/pending", headers=DEVICE).json()[0]["caption"] == ""


def test_upload_page_has_the_caption_field(env):
    client, *_ = env
    html = client.get("/").text
    assert 'name="caption"' in html and "maxlength" in html


def test_plain_uploads_carry_no_caption_size(env):
    client, *_ = env
    assert send(client).status_code == 202
    (item,) = client.get("/api/device/pending", headers=DEVICE).json()
    assert item["caption_size"] == 0

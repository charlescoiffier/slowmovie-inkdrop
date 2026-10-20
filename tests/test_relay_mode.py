import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_relay_admin import ADMIN_PASSWORD, DEVICE, TOKEN, UPLOAD_PASSWORD, XRW, Clock

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password

ALBUM = "https://photos.google.com/share/AF1QipSECRET?key=KEYKEY"
STATE = {"sent_at": 5000.0, "mode": "FILM", "source": "pipiquant", "next_at": 5300.0, "error": "Album illisible.",
         "paused": False, "video": None, "queue": [], "films": [],
         "settings": {"delay": 40, "mode": "pipiquant", "pipiquant_interval": 600, "photos_interval": 1800,
                      "pipiquant_title": True, "photos_album_set": True, "photos_album_host": "photos.google.com"}}


def make(tmp_path, *, key="k" * 24):
    clock = Clock()
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password(UPLOAD_PASSWORD), device_token=TOKEN,
                         admin_password_hash=hash_password(ADMIN_PASSWORD), session_secret="k" * 32,
                         pipiquant_api_key=key)
    return TestClient(create_app(config, clock=clock), base_url="https://testserver"), clock, tmp_path


@pytest.fixture
def env(tmp_path):
    client, clock, path = make(tmp_path)
    client.put("/api/device/state", content=json.dumps(STATE), headers=DEVICE)
    return client, clock, path


def call(client, route, body=None, *, password=UPLOAD_PASSWORD, headers=XRW):
    return client.post(f"/api/mode/{route}", json={"password": password, **(body or {})}, headers=headers)


def pending(client):
    return [json.loads(c["command"]) if isinstance(c["command"], str) else c["command"]
            for c in client.get("/api/device/commands", headers=DEVICE).json()]


def test_state_shape_and_no_secret(env):
    client, clock, _ = env
    clock.t = 1_000_000.0
    r = call(client, "state")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "pipiquant" and body["online"] is True and body["title"] is True
    assert body["rhythms"] == {"slowmovie": 40, "pipiquant": 600, "photos": 1800}
    assert body["album_set"] is True and body["album_host"] == "photos.google.com"
    assert body["pipiquant_available"] is True and body["error"] == "Album illisible."
    assert body["presets"] == {"slowmovie": [30, 60, 120, 300], "slide": [300, 600, 1800, 3600, 10800]}
    assert body["next_in_seconds"] == 300
    assert "AF1Qip" not in r.text and "photos_album_url" not in r.text


def test_state_without_any_snapshot_is_safe(tmp_path):
    client, _, _ = make(tmp_path)
    body = call(client, "state").json()
    assert body["online"] is False and body["source"] is None and body["rhythms"] == {
        "slowmovie": None, "pipiquant": None, "photos": None}
    assert body["album_set"] is False and body["next_in_seconds"] is None


def test_state_never_passes_through_an_album_link_from_the_pi(tmp_path):
    client, _, _ = make(tmp_path)
    hostile = {**STATE, "settings": {**STATE["settings"], "photos_album_url": ALBUM, "photos_album_host": ALBUM}}
    client.put("/api/device/state", content=json.dumps(hostile), headers=DEVICE)
    text = call(client, "state").text
    assert "SECRET" not in text and "KEYKEY" not in text


def test_next_in_decreases_with_time_and_never_goes_negative(env):
    client, clock, _ = env
    clock.t += 100
    assert call(client, "state").json()["next_in_seconds"] == 200
    clock.t += 10_000
    assert call(client, "state").json()["next_in_seconds"] in (0, None)


@pytest.mark.parametrize("route,body", [("state", {}), ("set", {"source": "photos"}),
                                        ("rhythm", {"source": "photos", "seconds": 600}), ("title", {"title": False})])
def test_wrong_password_and_missing_header_are_refused(env, route, body):
    client, _, _ = env
    assert call(client, route, body, password="faux").status_code == 401
    assert call(client, route, body, headers={}).status_code == 403
    assert call(client, route, body, password=ADMIN_PASSWORD).status_code == 401      # le mot de passe admin n'ouvre rien ici
    assert pending(client) == []


def test_five_failures_block_and_the_send_limiter_is_separate(env):
    client, _, _ = env
    for _ in range(5):
        call(client, "state", password="faux")
    assert call(client, "state").status_code == 429
    r = client.post("/api/send", data={"password": UPLOAD_PASSWORD, "duration": "300"},
                    files={"image": ("p.png", b"x", "image/png")})
    assert r.status_code != 429                       # l'envoi n'est pas bloqué par les échecs de l'onglet Mode


def test_mode_actions_do_not_use_the_send_quota(env):
    client, clock, _ = env
    for i in range(30):
        clock.t += 1
        assert call(client, "state").status_code == 200
    for i in range(5):
        assert call(client, "set", {"source": "slowmovie"}).status_code == 202


@pytest.mark.parametrize("source,changes", [("slowmovie", {"mode": "slowmovie"}), ("pipiquant", {"mode": "pipiquant"}),
                                            ("photos", {"mode": "photos"})])
def test_set_creates_the_settings_command(env, source, changes):
    client, _, _ = env
    r = call(client, "set", {"source": source})
    assert r.status_code == 202 and len(r.json()["id"]) == 32 and r.json()["online"] is True
    assert pending(client) == [{"kind": "settings", "changes": changes}]


def test_set_refuses_what_cannot_work(tmp_path):
    client, _, _ = make(tmp_path, key="")
    client.put("/api/device/state", content=json.dumps(
        {**STATE, "settings": {**STATE["settings"], "photos_album_set": False}}), headers=DEVICE)
    assert call(client, "set", {"source": "photos"}).status_code == 409
    assert call(client, "set", {"source": "pipiquant"}).status_code in (409, 503)
    assert call(client, "set", {"source": "autre"}).status_code == 422
    assert call(client, "set", {}).status_code == 422
    assert pending(client) == []
    assert call(client, "set", {"source": "slowmovie"}).status_code == 202


def test_photos_without_any_snapshot_is_refused(tmp_path):
    client, _, _ = make(tmp_path)
    assert call(client, "set", {"source": "photos"}).status_code == 409


@pytest.mark.parametrize("source,seconds,field", [
    ("slowmovie", 30, "delay"), ("slowmovie", 300, "delay"), ("pipiquant", 300, "pipiquant_interval"),
    ("pipiquant", 10800, "pipiquant_interval"), ("photos", 1800, "photos_interval")])
def test_rhythm_presets_create_the_right_command(env, source, seconds, field):
    client, _, _ = env
    assert call(client, "rhythm", {"source": source, "seconds": seconds}).status_code == 202
    assert pending(client) == [{"kind": "settings", "changes": {field: seconds}}]


@pytest.mark.parametrize("body", [
    {"source": "slowmovie", "seconds": 45}, {"source": "slowmovie", "seconds": 600},
    {"source": "pipiquant", "seconds": 30}, {"source": "photos", "seconds": 299}, {"source": "photos", "seconds": 86400},
    {"source": "photos", "seconds": True}, {"source": "photos", "seconds": "600"}, {"source": "photos", "seconds": 600.0 + 0.5},
    {"source": "autre", "seconds": 600}, {"seconds": 600}, {"source": "photos"},
])
def test_rhythm_outside_the_presets_is_refused(env, body):
    client, _, _ = env
    assert call(client, "rhythm", body).status_code == 422 and pending(client) == []


def test_title(env):
    client, _, _ = env
    assert call(client, "title", {"title": False}).status_code == 202
    assert pending(client) == [{"kind": "settings", "changes": {"pipiquant_title": False}}]
    for bad in ({"title": "non"}, {"title": 0}, {}):
        assert call(client, "title", bad).status_code == 422


def test_get_is_not_allowed_and_bodies_are_capped(env):
    client, _, _ = env
    assert client.get("/api/mode/state").status_code == 405
    r = client.post("/api/mode/state", content=b'{"password":"' + b"a" * 5000 + b'"}', headers=XRW)
    assert r.status_code == 413


def test_the_pi_offline_still_accepts_the_command(tmp_path):
    client, clock, _ = make(tmp_path)
    client.put("/api/device/state", content=json.dumps(STATE), headers=DEVICE)
    clock.t += 10_000
    r = call(client, "set", {"source": "slowmovie"})
    assert r.status_code == 202 and r.json()["online"] is False


# -- le lien de l'album est un secret ----------------------------------------

def admin_client(tmp_path):
    client, clock, path = make(tmp_path)
    assert client.post("/api/admin/login", json={"password": ADMIN_PASSWORD}, headers=XRW).status_code == 200
    client.put("/api/device/state", content=json.dumps(STATE), headers=DEVICE)
    return client, clock, path


def stored_payloads(path):
    db = sqlite3.connect(path / "relay.db")
    rows = [r[0] for r in db.execute("SELECT payload FROM commands")]
    db.close()
    return rows


def test_only_the_admin_session_can_set_the_album_link(tmp_path):
    client, _, _ = make(tmp_path)
    r = client.post("/api/admin/commands", json={"kind": "settings", "changes": {"photos_album_url": ALBUM}}, headers=XRW)
    assert r.status_code == 401
    client, _, path = admin_client(tmp_path / "b")
    ok = client.post("/api/admin/commands", json={"kind": "settings", "changes": {"photos_album_url": ALBUM}}, headers=XRW)
    assert ok.status_code == 202
    bad = client.post("/api/admin/commands", json={"kind": "settings",
                      "changes": {"photos_album_url": "https://evil.example/secret?key=zzz"}}, headers=XRW)
    assert bad.status_code == 422 and "evil.example" not in bad.text and "secret?key" not in bad.text


def test_the_link_is_wiped_from_stored_commands_once_acknowledged(tmp_path):
    client, _, path = admin_client(tmp_path)
    command_id = client.post("/api/admin/commands", json={"kind": "settings", "changes": {"photos_album_url": ALBUM}},
                             headers=XRW).json()["id"]
    assert any("SECRET" in p for p in stored_payloads(path))               # encore en attente : le Pi doit le recevoir
    assert "AF1QipSECRET" in client.get("/api/device/commands", headers=DEVICE).text
    ack = client.post(f"/api/device/commands/{command_id}/ack", json={"status": "applied"}, headers=DEVICE)
    assert ack.status_code == 200
    assert not any("SECRET" in p or "KEYKEY" in p for p in stored_payloads(path))
    assert client.get(f"/api/admin/commands/{command_id}", headers=XRW).json()["status"] == "applied"
    assert "SECRET" not in client.get("/api/admin/state", headers=XRW).text


def test_the_link_is_also_wiped_from_expired_and_failed_commands(tmp_path):
    client, clock, path = admin_client(tmp_path)
    first = client.post("/api/admin/commands", json={"kind": "settings", "changes": {"photos_album_url": ALBUM}},
                        headers=XRW).json()["id"]
    client.post(f"/api/device/commands/{first}/ack", json={"status": "failed", "message": "refusé"}, headers=DEVICE)
    client.post("/api/admin/commands", json={"kind": "settings", "changes": {"photos_album_url": ALBUM + "2"}}, headers=XRW)
    clock.t += 700                                                          # au-delà des 10 minutes
    client.get("/api/device/commands", headers=DEVICE)
    assert not any("SECRET" in p for p in stored_payloads(path))


def test_state_flags_a_pi_that_is_not_updated(tmp_path):
    client, _, _ = make(tmp_path)
    old = {k: v for k, v in STATE.items() if k != "source"}
    client.put("/api/device/state", content=json.dumps(old), headers=DEVICE)
    assert call(client, "state").json()["pi_outdated"] is True
    client.put("/api/device/state", content=json.dumps(STATE), headers=DEVICE)
    assert call(client, "state").json()["pi_outdated"] is False
    fresh, _, _ = make(tmp_path / "b")
    assert call(fresh, "state").json()["pi_outdated"] is False          # aucun état reçu : pas de reproche


def test_the_general_password_can_read_the_result_of_its_own_command(env):
    client, _, _ = env
    command_id = call(client, "set", {"source": "slowmovie"}).json()["id"]
    assert call(client, "command", {"id": command_id}).json() == {"status": "pending", "message": ""}
    client.post(f"/api/device/commands/{command_id}/ack", json={"status": "failed", "message": "réglage inconnu"},
                headers=DEVICE)
    assert call(client, "command", {"id": command_id}).json() == {"status": "failed", "message": "réglage inconnu"}
    assert call(client, "command", {"id": "0" * 32}).status_code == 404
    assert call(client, "command", {"id": "nimporte"}).status_code == 404
    assert call(client, "command", {"id": command_id}, password="faux").status_code == 401


def test_deleted_command_payloads_are_not_left_in_the_database_file(tmp_path):
    client, _, path = admin_client(tmp_path)
    command_id = client.post("/api/admin/commands", json={"kind": "settings", "changes": {"photos_album_url": ALBUM}},
                             headers=XRW).json()["id"]
    client.post(f"/api/device/commands/{command_id}/ack", json={"status": "applied"}, headers=DEVICE)
    raw = b"".join(p.read_bytes() for p in path.glob("relay.db*"))
    assert b"AF1QipSECRET" not in raw

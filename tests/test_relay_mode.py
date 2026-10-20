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
    assert body["presets"] == {"slowmovie": [30, 60, 120, 300, 600, 1800],
                               "slide": [300, 600, 1800, 3600, 10800, 21600, 43200, 86400]}
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


def test_title_of_the_active_pipiquant_mode_is_sent_at_once_otherwise_it_is_a_draft(tmp_path):
    client, _, _ = make(tmp_path)
    client.put("/api/device/state", content=json.dumps(STATE), headers=DEVICE)
    assert call(client, "title", {"title": False}).status_code == 202
    assert pending(client) == [{"kind": "settings", "changes": {"pipiquant_title": False}}]
    other, _, _ = make(tmp_path / "b")
    other.put("/api/device/state", content=json.dumps({**STATE, "source": "slowmovie"}), headers=DEVICE)
    r = call(other, "title", {"title": False})
    assert r.json() == {"draft": True, "mode": "pipiquant"} and pending(other) == []
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


# -- brouillons : transmis au Pi seulement à l'activation ---------------------------------

def test_activation_sends_the_mode_and_the_draft_in_one_command(env):
    client, _, _ = env
    call(client, "rhythm", {"source": "slowmovie", "seconds": 600})
    assert pending(client) == []
    r = call(client, "set", {"source": "slowmovie"})
    assert r.status_code == 202
    assert pending(client) == [{"kind": "settings", "changes": {"mode": "slowmovie", "delay": 600}}]
    command_id = r.json()["id"]
    assert call(client, "state").json()["drafts"] == {"slowmovie": {"delay": 600}}        # encore en attente du Pi
    client.post(f"/api/device/commands/{command_id}/ack", json={"status": "applied"}, headers=DEVICE)
    assert "drafts" in call(client, "state").json() and call(client, "state").json()["drafts"] == {}


def test_a_refused_activation_keeps_the_draft(env):
    client, _, _ = env
    call(client, "rhythm", {"source": "photos", "seconds": 600})
    client.put("/api/device/state", content=json.dumps({**STATE}), headers=DEVICE)
    command_id = call(client, "set", {"source": "slowmovie"}).json()["id"]
    client.post(f"/api/device/commands/{command_id}/ack", json={"status": "failed", "message": "non"}, headers=DEVICE)
    assert call(client, "state").json()["drafts"]["photos"] == {"photos_interval": 600}


def test_the_draft_of_one_mode_never_leaks_into_another_activation(env):
    client, _, _ = env
    call(client, "rhythm", {"source": "photos", "seconds": 600})
    call(client, "rhythm", {"source": "slowmovie", "seconds": 30})
    call(client, "set", {"source": "slowmovie"})
    assert pending(client) == [{"kind": "settings", "changes": {"mode": "slowmovie", "delay": 30}}]


def test_photos_can_be_activated_with_a_pending_album_link(tmp_path):
    client, _, _ = make(tmp_path)
    no_album = {**STATE, "settings": {**STATE["settings"], "photos_album_set": False}}
    client.put("/api/device/state", content=json.dumps(no_album), headers=DEVICE)
    assert call(client, "set", {"source": "photos"}).status_code == 409
    admin = TestClient(client.app, base_url="https://testserver")
    assert admin.post("/api/admin/login", json={"password": ADMIN_PASSWORD}, headers=XRW).status_code == 200
    r = admin.post("/api/admin/commands", headers=XRW,
                   json={"kind": "settings", "for_mode": "photos", "changes": {"photos_album_url": ALBUM}})
    assert r.status_code == 202 and r.json()["draft"] is True
    assert pending(client) == []
    assert call(client, "state").json()["album_set"] is False               # le Pi ne le connaît pas encore
    assert call(client, "set", {"source": "photos"}).status_code == 202
    assert pending(client) == [{"kind": "settings", "changes": {"mode": "photos", "photos_album_url": ALBUM}}]


def test_admin_settings_of_an_inactive_mode_are_drafts_and_the_active_mode_applies_at_once(tmp_path):
    client, _, path = admin_client(tmp_path)
    r = client.post("/api/admin/commands", headers=XRW,
                    json={"kind": "settings", "for_mode": "slowmovie", "changes": {"delay": 90, "loop": True}})
    assert r.status_code == 202 and r.json()["draft"] is True and pending(client) == []
    now = client.post("/api/admin/commands", headers=XRW,
                      json={"kind": "settings", "for_mode": "pipiquant", "changes": {"pipiquant_interval": 3600}})
    assert now.status_code == 202 and "draft" not in now.json()
    assert pending(client) == [{"kind": "settings", "changes": {"pipiquant_interval": 3600}}]
    state = client.get("/api/admin/state", headers=XRW).json()
    assert state["drafts"] == {"slowmovie": {"delay": 90, "loop": True}} and state["source"] == "pipiquant"


def test_admin_drafts_are_validated_like_commands_and_limited_to_the_modes_own_fields(tmp_path):
    client, _, _ = admin_client(tmp_path)

    def post(body):
        return client.post("/api/admin/commands", headers=XRW, json={"kind": "settings", **body})
    assert post({"for_mode": "pipiquant", "changes": {"pipiquant_interval": 5}}).status_code == 422
    assert post({"for_mode": "slowmovie", "changes": {"pipiquant_interval": 600}}).status_code == 422
    assert post({"for_mode": "slowmovie", "changes": {"relay_poll_seconds": 30}}).status_code == 422
    assert post({"for_mode": "slowmovie", "changes": {"contrast": 1.5}}).status_code == 422       # réglage commun, jamais en brouillon
    assert post({"for_mode": "slowmovie", "changes": {"active_hours": "08:00-22:00"}}).status_code == 422
    assert post({"for_mode": "autre", "changes": {"delay": 40}}).status_code == 422
    assert post({"for_mode": "photos", "changes": {"photos_album_url": "https://evil.example/x"}}).status_code == 422
    assert post({"for_mode": "slowmovie", "changes": {"delay": "40"}}).status_code == 422
    assert post({"for_mode": "slowmovie", "changes": {"relay_url": "https://x.io"}}).status_code == 422
    assert client.get("/api/admin/state", headers=XRW).json()["drafts"] == {}


def test_send_settings_are_always_immediate(tmp_path):
    client, _, _ = admin_client(tmp_path)
    r = client.post("/api/admin/commands", headers=XRW, json={"kind": "settings", "changes": {"relay_poll_seconds": 30}})
    assert r.status_code == 202 and "draft" not in r.json()
    assert pending(client) == [{"kind": "settings", "changes": {"relay_poll_seconds": 30}}]


def test_drafts_never_expose_the_album_link(tmp_path):
    client, _, path = admin_client(tmp_path)
    client.post("/api/admin/commands", headers=XRW,
                json={"kind": "settings", "for_mode": "photos", "changes": {"photos_album_url": ALBUM, "photos_interval": 600}})
    for text in (client.get("/api/admin/state", headers=XRW).text, call(client, "state").text):
        assert "AF1QipSECRET" not in text and "KEYKEY" not in text
    assert call(client, "state").json()["drafts"]["photos"] == {"photos_album_url": True, "photos_interval": 600}
    command_id = call(client, "set", {"source": "photos"}).json()["id"]
    client.post(f"/api/device/commands/{command_id}/ack", json={"status": "applied"}, headers=DEVICE)
    assert not any("SECRET" in p for p in stored_payloads(path))
    raw = b"".join(p.read_bytes() for p in path.glob("relay.db*"))
    assert b"AF1QipSECRET" not in raw


def test_the_state_carries_the_film_and_what_is_on_screen(env):
    client, _, _ = env
    film = {**STATE, "source": "slowmovie", "video": "Sintel.mkv", "position_seconds": 252.0, "duration_seconds": 888.0}
    client.put("/api/device/state", content=json.dumps(film), headers=DEVICE)
    body = call(client, "state").json()
    assert body["film"] == {"name": "Sintel.mkv", "position_seconds": 252.0, "duration_seconds": 888.0}
    assert body["displaying"] == "FILM"
    shown = {**film, "mode": "IMAGE"}
    client.put("/api/device/state", content=json.dumps(shown), headers=DEVICE)
    assert call(client, "state").json()["displaying"] == "IMAGE"


def test_current_slide_card(env):
    import base64
    client, _, _ = env
    assert call(client, "current", {"source": "pipiquant"}).status_code == 404
    store_dir = env[2]
    from slowmovie_inkdrop.relay.store import Store
    Store(store_dir).save_current_slide("pipiquant", {"decimals": 87, "angle": 214}, b"\xff\xd8thumb", now=1_000_000.0)
    r = call(client, "current", {"source": "pipiquant"})
    assert r.status_code == 200
    body = r.json()
    assert body["details"] == {"decimals": 87, "angle": 214} and body["source"] == "pipiquant"
    assert body["thumbnail"] == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8thumb").decode()
    assert body["age_seconds"] == 0
    assert call(client, "current", {"source": "slowmovie"}).status_code == 422
    assert call(client, "current", {"source": "../x"}).status_code == 422
    assert call(client, "current", {"source": "photos"}, password="faux").status_code == 401

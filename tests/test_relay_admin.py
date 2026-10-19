import json
import logging

import pytest
from fastapi.testclient import TestClient

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password

ADMIN_PASSWORD = "phrase administrateur solide"
UPLOAD_PASSWORD = "phrase d'envoi"
TOKEN = "t" * 40
DEVICE = {"Authorization": f"Bearer {TOKEN}"}
XRW = {"X-Requested-With": "slowmovie"}
STATE = {"mode": "FILM", "video": "a.mkv", "settings": {"delay": 40},
         "films": [{"name": "a.mkv", "status": "ready"},
                   {"name": "<img src=x onerror=alert(1)>.mkv", "status": "ready"}]}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def make(tmp_path, **overrides):
    clock = Clock()
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password(UPLOAD_PASSWORD), device_token=TOKEN,
                         admin_password_hash=hash_password(ADMIN_PASSWORD), session_secret="k" * 32, **overrides)
    return TestClient(create_app(config, clock=clock), base_url="https://testserver"), clock, config


@pytest.fixture
def env(tmp_path):
    return make(tmp_path)


def login(client, password=ADMIN_PASSWORD):
    return client.post("/api/admin/login", json={"password": password}, headers=XRW)


def logged_in(env):
    client, clock, config = env
    assert login(client).status_code == 200
    return client, clock, config


def put_state(client, state=STATE):
    return client.put("/api/device/state", content=json.dumps(state), headers=DEVICE)


def command(client, payload):
    return client.post("/api/admin/commands", json=payload, headers=XRW)


def test_admin_is_disabled_without_a_password_hash(tmp_path):
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password("x"), device_token=TOKEN)
    client = TestClient(create_app(config), base_url="https://testserver")
    assert client.get("/api/config").json()["admin"] is False
    assert client.post("/api/admin/login", json={"password": "x"}, headers=XRW).status_code == 503
    assert client.get("/api/admin/state", headers=XRW).status_code == 503


def test_config_announces_the_admin_tab(env):
    client, *_ = env
    assert client.get("/api/config").json()["admin"] is True


def test_login_sets_a_hardened_cookie(env):
    client, *_ = env
    r = login(client)
    assert r.status_code == 200 and r.json() == {"ok": True}
    cookie = r.headers["set-cookie"].lower()
    for attribute in ("sm_admin=", "httponly", "secure", "samesite=strict", "path=/api/admin", "max-age=28800"):
        assert attribute in cookie


def test_wrong_password_and_the_upload_password_are_refused(env):
    client, *_ = env
    assert login(client, "faux").status_code == 401
    assert login(client, UPLOAD_PASSWORD).status_code == 401           # le mot de passe d'envoi n'ouvre rien
    assert login(client, "").status_code == 401


def test_login_requires_the_custom_header(env):
    client, *_ = env
    assert client.post("/api/admin/login", json={"password": ADMIN_PASSWORD}).status_code == 403


def test_lockout_after_five_failures_is_separate_from_uploads(env):
    client, clock, _ = env
    for _ in range(5):
        login(client, "faux")
    r = login(client)
    assert r.status_code == 429 and "minute" in r.json()["detail"]
    clock.t += 901
    assert login(client).status_code == 200


def test_upload_failures_do_not_lock_the_admin_out(env):
    client, *_ = env
    for _ in range(5):
        client.post("/api/send", data={"password": "faux", "duration": "60"},
                    files={"image": ("a.png", b"x", "image/png")})
    assert login(client).status_code == 200


def test_password_is_never_logged(env, caplog):
    client, *_ = env
    with caplog.at_level(logging.DEBUG):
        login(client, "Secret-administrateur-à-ne-pas-logger")
        login(client)
    assert "Secret-administrateur" not in caplog.text and ADMIN_PASSWORD not in caplog.text


def test_admin_routes_need_the_cookie_and_the_header(env):
    client, *_ = env
    assert client.get("/api/admin/state", headers=XRW).status_code == 401          # pas de cookie
    login(client)
    assert client.get("/api/admin/state").status_code == 403                        # cookie sans en-tête (CSRF)
    assert client.get("/api/admin/state", headers=XRW).status_code == 200


def test_tampered_or_expired_cookies_are_refused(env):
    client, clock, config = env
    login(client)
    good = client.cookies.get("sm_admin")
    client.cookies.clear()
    client.cookies.set("sm_admin", good[:-3] + "abc", domain="testserver", path="/api/admin")
    assert client.get("/api/admin/state", headers=XRW).status_code == 401
    client.cookies.clear()
    login(client)
    clock.t += config.session_seconds + 1
    assert client.get("/api/admin/state", headers=XRW).status_code == 401


def test_logout_clears_the_session(env):
    client, *_ = logged_in(env)
    assert client.post("/api/admin/logout", headers=XRW).status_code == 200
    assert client.get("/api/admin/state", headers=XRW).status_code == 401


def test_device_endpoints_still_need_the_device_token(env):
    client, *_ = env
    assert client.put("/api/device/state", content="{}").status_code == 401
    assert client.get("/api/device/commands").status_code == 401
    assert client.post("/api/device/commands/" + "a" * 32 + "/ack", json={"status": "applied"}).status_code == 401
    login(client)
    assert client.get("/api/device/commands").status_code == 401          # le cookie administrateur n'ouvre pas l'appareil


def test_state_is_stored_and_shown_to_the_admin_with_the_online_flag(env):
    client, clock, _ = logged_in(env)
    assert client.get("/api/admin/state", headers=XRW).json()["online"] is False
    assert put_state(client).status_code == 200
    body = client.get("/api/admin/state", headers=XRW).json()
    assert body["online"] is True and body["state"]["video"] == "a.mkv" and body["last_seen_seconds"] == 0
    clock.t += 61
    assert client.get("/api/admin/state", headers=XRW).json()["online"] is False


def test_hostile_strings_are_returned_verbatim_as_json_data(env):
    client, *_ = logged_in(env)
    put_state(client)
    r = client.get("/api/admin/state", headers=XRW)
    assert r.headers["content-type"].startswith("application/json")
    assert any(f["name"].startswith("<img") for f in r.json()["state"]["films"])


@pytest.mark.parametrize("content", ["pas du json", "[1, 2]", "42", '"texte"'])
def test_invalid_state_is_refused(env, content):
    client, *_ = env
    assert client.put("/api/device/state", content=content, headers=DEVICE).status_code == 422


def test_oversized_state_is_refused_even_when_chunked(env):
    client, *_ = env
    big = json.dumps({"x": "y" * 70000})
    assert client.put("/api/device/state", content=big, headers=DEVICE).status_code == 413
    chunked = client.put("/api/device/state", content=iter([big.encode()[:30000], big.encode()[30000:]]),
                         headers=DEVICE)
    assert chunked.status_code == 413


def test_a_valid_command_is_queued_and_offered_to_the_device(env):
    client, *_ = logged_in(env)
    put_state(client)
    r = command(client, {"kind": "play", "video": "a.mkv"})
    assert r.status_code == 202 and len(r.json()["id"]) == 32 and r.json()["online"] is True
    offered = client.get("/api/device/commands", headers=DEVICE).json()
    assert offered == [{"id": r.json()["id"], "command": {"kind": "play", "video": "a.mkv"}}]


@pytest.mark.parametrize("payload", [
    {"kind": "dance"}, {"kind": "play", "video": "inconnu.mkv"}, {"kind": "play", "video": "../a.mkv"},
    {"kind": "seek", "video": "a.mkv", "seconds": -1},
    {"kind": "settings", "changes": {"relay_url": "https://evil"}},
    {"kind": "settings", "changes": {"delay": "10"}}, {"kind": "settings", "changes": {}}, "n'importe quoi", None,
])
def test_invalid_commands_are_refused_before_being_queued(env, payload):
    client, *_ = logged_in(env)
    put_state(client)
    r = command(client, payload)
    assert r.status_code == 422 and r.json()["detail"]
    assert client.get("/api/device/commands", headers=DEVICE).json() == []


def test_film_commands_need_a_known_state(env):
    client, *_ = logged_in(env)
    assert command(client, {"kind": "play", "video": "a.mkv"}).status_code == 422          # pas encore d'état du Pi
    assert command(client, {"kind": "pause"}).status_code == 202


def test_commands_need_the_cookie_and_the_header(env):
    client, *_ = env
    assert client.post("/api/admin/commands", json={"kind": "pause"}, headers=XRW).status_code == 401
    login(client)
    assert client.post("/api/admin/commands", json={"kind": "pause"}).status_code == 403


def test_too_many_pending_commands_are_refused(env):
    client, *_ = logged_in(env)
    for _ in range(20):
        assert command(client, {"kind": "pause"}).status_code == 202
    assert command(client, {"kind": "pause"}).status_code == 429


def test_ack_updates_the_status_seen_by_the_admin(env):
    client, *_ = logged_in(env)
    put_state(client)
    cid = command(client, {"kind": "play", "video": "a.mkv"}).json()["id"]
    assert client.get(f"/api/admin/commands/{cid}", headers=XRW).json()["status"] == "pending"
    r = client.post(f"/api/device/commands/{cid}/ack", json={"status": "applied", "message": ""}, headers=DEVICE)
    assert r.status_code == 200
    assert client.get(f"/api/admin/commands/{cid}", headers=XRW).json() == {"status": "applied", "message": ""}
    assert client.get("/api/device/commands", headers=DEVICE).json() == []
    again = client.post(f"/api/device/commands/{cid}/ack", json={"status": "failed", "message": "x"}, headers=DEVICE)
    assert again.status_code == 404                                                   # jamais deux fois


def test_failed_ack_carries_its_message(env):
    client, *_ = logged_in(env)
    cid = command(client, {"kind": "pause"}).json()["id"]
    client.post(f"/api/device/commands/{cid}/ack", json={"status": "failed", "message": "film introuvable"},
                headers=DEVICE)
    assert client.get(f"/api/admin/commands/{cid}", headers=XRW).json()["message"] == "film introuvable"


@pytest.mark.parametrize("body", [{"status": "nimporte"}, {"status": "pending"}, {}])
def test_invalid_ack_status_is_refused(env, body):
    client, *_ = logged_in(env)
    cid = command(client, {"kind": "pause"}).json()["id"]
    assert client.post(f"/api/device/commands/{cid}/ack", json=body, headers=DEVICE).status_code == 422


def test_unknown_or_malformed_command_ids(env):
    client, *_ = logged_in(env)
    assert client.get("/api/admin/commands/" + "0" * 32, headers=XRW).status_code == 404
    assert client.get("/api/admin/commands/..%2Fx", headers=XRW).status_code in (404, 422)
    assert client.post("/api/device/commands/" + "0" * 32 + "/ack", json={"status": "applied"},
                       headers=DEVICE).status_code == 404


def test_commands_expire_and_are_never_offered_late(env):
    client, clock, config = logged_in(env)
    cid = command(client, {"kind": "pause"}).json()["id"]
    clock.t += config.command_ttl_seconds + 1
    assert client.get("/api/device/commands", headers=DEVICE).json() == []
    assert client.get(f"/api/admin/commands/{cid}", headers=XRW).json()["status"] == "expired"


def test_the_admin_state_lists_recent_commands(env):
    client, *_ = logged_in(env)
    for _ in range(3):
        command(client, {"kind": "pause"})
    assert len(client.get("/api/admin/state", headers=XRW).json()["commands"]) == 3


def test_online_threshold_follows_the_poll_interval_the_pi_reports(env):
    client, clock, _ = logged_in(env)
    slow = dict(STATE, settings={"relay_poll_seconds": 120})
    put_state(client, slow)
    clock.t += 200                                              # moins de 3 intervalles : toujours en ligne
    assert client.get("/api/admin/state", headers=XRW).json()["online"] is True
    clock.t += 200                                              # 400 s sans nouvelles : hors ligne
    assert client.get("/api/admin/state", headers=XRW).json()["online"] is False


@pytest.mark.parametrize("poll", ["x", None, -5, 10**9, 2, True])
def test_a_garbage_poll_interval_falls_back_to_the_default_threshold(env, poll):
    client, clock, _ = logged_in(env)
    put_state(client, dict(STATE, settings={"relay_poll_seconds": poll}))
    clock.t += 61
    assert client.get("/api/admin/state", headers=XRW).json()["online"] is False


def test_login_refuses_oversized_bodies_even_when_chunked(env):
    client, *_ = env
    big = json.dumps({"password": "x" * 5000}).encode()
    assert client.post("/api/admin/login", content=big, headers={**XRW, "content-type": "application/json"}
                       ).status_code == 413
    chunked = client.post("/api/admin/login", content=iter([big[:2000], big[2000:]]),
                          headers={**XRW, "content-type": "application/json"})
    assert chunked.status_code == 413
    assert login(client).status_code == 200                      # un corps normal passe toujours


@pytest.mark.parametrize("content", ["pas du json", "[1]", "null", '{"password": 5}', '{"password": null}', ""])
def test_login_with_a_malformed_body_is_refused_not_crashed(env, content):
    client, *_ = env
    r = client.post("/api/admin/login", content=content, headers={**XRW, "content-type": "application/json"})
    assert r.status_code in (401, 422)

"""De l'envoi sur le relais jusqu'à l'écran, sans réseau ni matériel."""
import json

from conftest import png_bytes
from fastapi.testclient import TestClient

from slowmovie_inkdrop.config import Settings
from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password
from slowmovie_inkdrop.relayclient import AckOutbox, RelayClient, sync_cycle


def test_upload_to_screen_and_ack(make_player, tmp_path):
    config = RelayConfig(data_dir=tmp_path / "relay", password_hash=hash_password("pw pw pw"),
                         device_token="j" * 40)
    web = TestClient(create_app(config))
    sent = web.post("/api/send", data={"password": "pw pw pw", "duration": "120"},
                    files={"image": ("a.png", png_bytes(), "image/png")})
    image_id = sent.json()["id"]

    ctx = make_player()
    outbox = AckOutbox(tmp_path / "acks.json")
    ctx.player.on_done = lambda item, status: outbox.add(item.id, status)
    client = RelayClient("http://testserver", "j" * 40, client=web)

    sync_cycle(client, ctx.queue, outbox, Settings(), ctx.clock())
    ctx.player.tick()
    assert ctx.player.mode == "IMAGE"

    sync_cycle(client, ctx.queue, outbox, Settings(), ctx.clock())      # envoie l'accusé
    assert web.get(f"/api/status/{image_id}").json()["status"] == "shown"
    assert json.loads(ctx.paths.state.read_text())["mode"] == "IMAGE"


def test_caption_goes_from_the_upload_form_to_the_screen(make_player, tmp_path):
    from PIL import ImageChops

    config = RelayConfig(data_dir=tmp_path / "relay2", password_hash=hash_password("pw pw pw"),
                         device_token="j" * 40)
    web = TestClient(create_app(config))
    for caption in ("", "Bon anniversaire !"):
        web.post("/api/send", data={"password": "pw pw pw", "duration": "60", "caption": caption},
                 files={"image": ("a.png", png_bytes(size=(800, 480), color="gray"), "image/png")})
    ctx = make_player()
    outbox = AckOutbox(tmp_path / "acks2.json")
    client = RelayClient("http://testserver", "j" * 40, client=web)
    sync_cycle(client, ctx.queue, outbox, Settings(), ctx.clock())
    ctx.player.tick()
    ctx.clock.advance(61)
    ctx.player.tick()
    plain, captioned = ctx.display.shown[-2:]
    assert ImageChops.difference(plain, captioned).getbbox() is not None


def test_admin_command_travels_from_the_web_to_the_player(make_player, tmp_path):
    import threading

    from slowmovie_inkdrop.relayclient import CommandLog, sync_control

    config = RelayConfig(data_dir=tmp_path / "relay3", password_hash=hash_password("pw pw pw"),
                         device_token="j" * 40, admin_password_hash=hash_password("admin pw pw"),
                         session_secret="k" * 32)
    web = TestClient(create_app(config), base_url="https://testserver")
    xrw = {"X-Requested-With": "slowmovie"}
    assert web.post("/api/admin/login", json={"password": "admin pw pw"}, headers=xrw).status_code == 200

    ctx = make_player(videos=("a.mp4", "b.mp4"))
    client = RelayClient("https://testserver", "j" * 40, client=web)
    log = CommandLog(tmp_path / "commands3.json")
    ctx.player.tick()
    sync_control(client, ctx.player, log, ctx.clock())                      # 1er tour : envoie l'état du Pi
    state = web.get("/api/admin/state", headers=xrw).json()
    assert state["online"] and {f["name"] for f in state["state"]["films"]} == {"a.mp4", "b.mp4"}

    first = web.post("/api/admin/commands", json={"kind": "play", "video": "b.mp4"}, headers=xrw).json()["id"]
    second = web.post("/api/admin/commands", json={"kind": "settings", "changes": {"delay": 15}},
                      headers=xrw).json()["id"]
    refused = web.post("/api/admin/commands", json={"kind": "settings", "changes": {"relay_url": "x"}}, headers=xrw)
    assert refused.status_code == 422

    runner = threading.Thread(target=sync_control, args=(client, ctx.player, log, ctx.clock()))
    runner.start()
    for _ in range(200):                                                    # le lecteur traite les commandes dans tick()
        ctx.player.tick()
        if not runner.is_alive():
            break
        runner.join(timeout=0.02)
    runner.join(timeout=5)
    assert web.get(f"/api/admin/commands/{first}", headers=xrw).json()["status"] == "applied"
    assert web.get(f"/api/admin/commands/{second}", headers=xrw).json()["status"] == "applied"
    assert ctx.player.video.name == "b.mp4" and ctx.player.settings.delay == 15
    assert Settings.load(ctx.paths.conf).delay == 15

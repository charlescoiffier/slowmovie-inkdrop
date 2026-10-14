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

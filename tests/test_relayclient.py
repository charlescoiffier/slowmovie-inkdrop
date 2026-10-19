import io
import json
from concurrent.futures import Future

import httpx
from PIL import Image

from slowmovie_inkdrop.config import Settings
from slowmovie_inkdrop.image_queue import ImageQueue
from slowmovie_inkdrop.relayclient import (
    AckOutbox,
    CommandLog,
    RelayClient,
    apply_remote_command,
    sync_control,
    sync_cycle,
)

A, B, C = "a" * 32, "b" * 32, "c" * 32


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buf, "PNG")
    return buf.getvalue()


class FakeRelay:
    """Simule le relais avec httpx.MockTransport et garde la trace des appels."""

    def __init__(self, pending, images=None, fail=False):
        self.pending, self.images, self.fail = pending, images or {}, fail
        self.acks: list[tuple[str, str]] = []
        self.ack_codes: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("relais éteint")
        assert request.headers["authorization"] == "Bearer jeton"
        path = request.url.path
        if path == "/api/device/pending":
            return httpx.Response(200, json=self.pending)
        if path.startswith("/api/device/image/"):
            return httpx.Response(200, content=self.images[path.rsplit("/", 1)[1]])
        if path.startswith("/api/device/ack/"):
            image_id = path.rsplit("/", 1)[1]
            if image_id in self.ack_codes:
                return httpx.Response(self.ack_codes[image_id], json={"detail": "x"})
            self.acks.append((image_id, json.loads(request.content)["status"]))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    def client(self) -> RelayClient:
        return RelayClient("https://relais.test", "jeton",
                           client=httpx.Client(transport=httpx.MockTransport(self.handler)))


def make(tmp_path, **kw):
    queue = ImageQueue(tmp_path / "queue", max_items=kw.get("max_items", 10))
    return queue, AckOutbox(tmp_path / "acks.json")


def test_downloads_new_images_into_queue(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 120}], {A: png()})
    queue, outbox = make(tmp_path)
    status = sync_cycle(relay.client(), queue, outbox, Settings(), now=100.0)
    assert [i.id for i in queue.items()] == [A] and queue.items()[0].duration == 120
    assert "1" in status


def test_same_image_delivered_twice_is_queued_once(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 60}], {A: png()})
    queue, outbox = make(tmp_path)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    queue.discard(queue.items()[0])               # affichée entre-temps
    sync_cycle(relay.client(), queue, outbox, Settings(), now=2)   # le relais la propose encore
    assert len(queue) == 0                         # pas d'affichage en double


def test_full_queue_leaves_items_pending(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 60}, {"id": B, "duration": 60}], {A: png(), B: png()})
    queue, outbox = make(tmp_path, max_items=1)
    sync_cycle(relay.client(), queue, outbox, Settings(queue_max=1), now=1)
    assert [i.id for i in queue.items()] == [A]
    assert relay.acks == []                        # B reste en attente côté relais


def test_invalid_image_is_acked_failed(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 60}], {A: b"pas une image"})
    queue, outbox = make(tmp_path)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert len(queue) == 0 and relay.acks == [(A, "failed")]


def test_malicious_id_from_relay_is_ignored(tmp_path):
    relay = FakeRelay([{"id": "../../evil", "duration": 60}], {"../../evil": png()})
    queue, outbox = make(tmp_path)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert len(queue) == 0
    assert not (tmp_path / "evil.png").exists()


def test_relay_down_is_reported_not_raised(tmp_path):
    relay = FakeRelay([], fail=True)
    queue, outbox = make(tmp_path)
    status = sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert "injoignable" in status


def test_lost_ack_is_retried_until_delivered(tmp_path):
    relay = FakeRelay([])
    queue, outbox = make(tmp_path)
    outbox.add(A, "shown")
    relay.fail = True
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert outbox.pending() == [{"id": A, "status": "shown"}]      # conservé
    relay.fail = False
    sync_cycle(relay.client(), queue, outbox, Settings(), now=2)
    assert relay.acks == [(A, "shown")] and outbox.pending() == []


def test_outbox_survives_restart(tmp_path):
    AckOutbox(tmp_path / "acks.json").add(C, "failed")
    assert AckOutbox(tmp_path / "acks.json").pending() == [{"id": C, "status": "failed"}]


def test_ack_for_an_id_the_relay_forgot_is_dropped_and_polling_continues(tmp_path):
    # le relais a été redéployé (données vides) : l'accusé de A répond 404 pour toujours
    relay = FakeRelay([{"id": B, "duration": 60}], {B: png()})
    relay.ack_codes[A] = 404
    queue, outbox = make(tmp_path)
    outbox.add(A, "shown")
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert outbox.pending() == []
    assert [i.id for i in queue.items()] == [B]


def test_failing_ack_is_kept_but_does_not_stop_polling(tmp_path):
    relay = FakeRelay([{"id": B, "duration": 60}], {B: png()})
    relay.ack_codes[A] = 500
    queue, outbox = make(tmp_path)
    outbox.add(A, "shown")
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert outbox.pending() == [{"id": A, "status": "shown"}]
    assert [i.id for i in queue.items()] == [B]


def test_non_numeric_duration_is_acked_failed(tmp_path):
    relay = FakeRelay([{"id": A, "duration": "abc"}], {A: png()})
    queue, outbox = make(tmp_path)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert len(queue) == 0 and relay.acks == [(A, "failed")]


def test_image_with_too_many_pixels_is_acked_failed(tmp_path, monkeypatch):
    from slowmovie_inkdrop import relayclient
    monkeypatch.setattr(relayclient, "MAX_PIXELS", 100)       # l'image de test fait 20x20 = 400
    relay = FakeRelay([{"id": A, "duration": 60}], {A: png()})
    queue, outbox = make(tmp_path)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert len(queue) == 0 and relay.acks == [(A, "failed")]


def test_download_larger_than_the_limit_is_refused():
    import pytest
    relay = FakeRelay([], {A: b"x" * 100})
    client = RelayClient("https://relais.test", "jeton", max_download=10,
                         client=httpx.Client(transport=httpx.MockTransport(relay.handler)))
    with pytest.raises(Exception, match="lourde"):
        client.download(A)


def test_caption_travels_with_the_image_and_is_cleaned_by_the_pi(tmp_path):
    hostile = "ok\u202e\x00" + "z" * 400
    relay = FakeRelay([{"id": A, "duration": 60, "caption": hostile}], {A: png()})
    queue, outbox = make(tmp_path)
    sync_cycle(relay.client(), queue, outbox, Settings(caption_max_chars=50), now=1)
    caption = queue.items()[0].caption
    assert caption.startswith("ok") and len(caption) == 50
    assert "\u202e" not in caption and "\x00" not in caption


def test_missing_caption_field_means_no_caption(tmp_path):
    relay = FakeRelay([{"id": A, "duration": 60}], {A: png()})
    queue, outbox = make(tmp_path)
    sync_cycle(relay.client(), queue, outbox, Settings(), now=1)
    assert queue.items()[0].caption == ""


class FakePlayer:
    """Lecteur factice : enregistre les commandes reçues et répond comme le vrai."""

    def __init__(self, replies=None):
        self.received = []
        self.replies = replies or {}

    def remote_snapshot(self, now):
        return {"sent_at": now, "mode": "FILM", "films": [], "settings": {"delay": 40}}

    def submit(self, cmd):
        self.received.append(cmd)
        future = Future()
        future.set_result(self.replies.get(cmd["cmd"], {"ok": True}))
        return future


class ControlRelay:
    """Relais factice côté pilotage."""

    def __init__(self, commands=None):
        self.commands = commands or []
        self.states = []
        self.acks = []
        self.ack_codes = {}
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("relais éteint")
        assert request.headers["authorization"] == "Bearer jeton"
        path = request.url.path
        if path == "/api/device/state" and request.method == "PUT":
            self.states.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        if path == "/api/device/commands":
            return httpx.Response(200, json=self.commands)
        if path.startswith("/api/device/commands/") and path.endswith("/ack"):
            cid = path.split("/")[-2]
            if cid in self.ack_codes:
                return httpx.Response(self.ack_codes[cid], json={"detail": "x"})
            self.acks.append((cid, json.loads(request.content)))
            self.commands = [c for c in self.commands if c["id"] != cid]     # comme le vrai relais : plus proposée
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    def client(self) -> RelayClient:
        return RelayClient("https://relais.test", "jeton",
                           client=httpx.Client(transport=httpx.MockTransport(self.handler)))


def test_apply_remote_command_translates_and_reports():
    player = FakePlayer()
    assert apply_remote_command(player, {"kind": "play", "video": "a.mkv"}) == {"status": "applied", "message": ""}
    assert player.received == [{"cmd": "play", "video": "a.mkv"}]


def test_apply_remote_command_revalidates_everything():
    player = FakePlayer()
    for bad in ({"kind": "settings", "changes": {"relay_url": "https://evil"}}, {"kind": "play", "video": "../x"},
                {"kind": "dance"}, "n'importe quoi", None):
        result = apply_remote_command(player, bad)
        assert result["status"] == "failed" and result["message"]
    assert player.received == []                                       # rien n'a atteint le lecteur


def test_apply_remote_command_reports_a_refusal_from_the_player():
    player = FakePlayer({"play": {"ok": False, "error": "film introuvable ou pas encore prêt"}})
    assert apply_remote_command(player, {"kind": "play", "video": "a.mkv"}) == {
        "status": "failed", "message": "film introuvable ou pas encore prêt"}


def test_sync_control_applies_new_commands_acks_them_and_sends_the_state(tmp_path):
    relay = ControlRelay([{"id": A, "command": {"kind": "pause"}}, {"id": B, "command": {"kind": "skip"}}])
    player, log = FakePlayer(), CommandLog(tmp_path / "commands.json")
    status = sync_control(relay.client(), player, log, now=5.0)
    assert [c["cmd"] for c in player.received] == ["pause", "skip"]
    assert [a[0] for a in relay.acks] == [A, B] and relay.acks[0][1]["status"] == "applied"
    assert relay.states and relay.states[0]["settings"] == {"delay": 40}
    assert log.pending() == {} and "2" in status


def test_a_command_offered_again_is_never_applied_twice(tmp_path):
    relay = ControlRelay([{"id": A, "command": {"kind": "pause"}}])
    relay.ack_codes[A] = 500                                           # l'accusé n'arrive pas
    player, log = FakePlayer(), CommandLog(tmp_path / "commands.json")
    sync_control(relay.client(), player, log, now=1.0)
    sync_control(relay.client(), player, log, now=2.0)
    assert len(player.received) == 1                                    # appliquée une seule fois
    relay.ack_codes.clear()
    sync_control(relay.client(), player, log, now=3.0)
    assert relay.acks[-1][0] == A and log.pending() == {} and len(player.received) == 1


def test_an_ack_for_a_command_the_relay_forgot_is_dropped(tmp_path):
    relay = ControlRelay([])
    relay.ack_codes[A] = 404
    log = CommandLog(tmp_path / "commands.json")
    log.put(A, "applied", "")
    sync_control(relay.client(), FakePlayer(), log, now=1.0)
    assert log.pending() == {}


def test_a_failing_ack_does_not_stop_the_other_commands_or_the_state(tmp_path):
    relay = ControlRelay([{"id": A, "command": {"kind": "pause"}}, {"id": B, "command": {"kind": "skip"}}])
    relay.ack_codes[A] = 500
    player, log = FakePlayer(), CommandLog(tmp_path / "commands.json")
    sync_control(relay.client(), player, log, now=1.0)
    assert [c["cmd"] for c in player.received] == ["pause", "skip"] and relay.states
    assert [a[0] for a in relay.acks] == [B] and list(log.pending()) == [A]


def test_sync_control_never_raises_when_the_relay_is_down(tmp_path):
    relay = ControlRelay()
    relay.down = True
    status = sync_control(relay.client(), FakePlayer(), CommandLog(tmp_path / "c.json"), now=1.0)
    assert "injoignable" in status


def test_command_log_survives_restart(tmp_path):
    CommandLog(tmp_path / "c.json").put(C, "failed", "raison")
    assert CommandLog(tmp_path / "c.json").pending() == {C: {"status": "failed", "message": "raison"}}


def test_a_command_that_times_out_is_reported_with_an_unknown_result():
    class Busy:
        def submit(self, cmd):
            return Future()                                    # le lecteur est occupé : jamais de réponse

    result = apply_remote_command(Busy(), {"kind": "pause"}, timeout=0.05)
    assert result["status"] == "failed" and "inconnu" in result["message"]


def test_a_crash_between_applying_and_logging_never_replays_the_command(tmp_path):
    relay = ControlRelay([{"id": A, "command": {"kind": "seek", "video": "a.mkv", "seconds": 10}}])
    log = CommandLog(tmp_path / "commands.json")

    class Crashing:
        received = []

        def remote_snapshot(self, now):
            return {}

        def submit(self, cmd):
            raise KeyboardInterrupt                            # coupure en plein milieu de l'application

    try:
        sync_control(relay.client(), Crashing(), log, now=1.0)
    except KeyboardInterrupt:
        pass
    assert A in log.pending()                                  # la commande est déjà notée « interrompue »
    player = FakePlayer()
    sync_control(relay.client(), player, log, now=2.0)         # après le redémarrage : le relais la propose encore
    assert player.received == []                               # jamais rejouée
    assert relay.acks[-1][0] == A and relay.acks[-1][1]["status"] == "failed"
    assert "interrompue" in relay.acks[-1][1]["message"]

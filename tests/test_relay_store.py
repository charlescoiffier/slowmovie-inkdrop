import json

from slowmovie_inkdrop.relay.store import Store


def test_state_is_replaced_and_timestamped(tmp_path):
    store = Store(tmp_path)
    assert store.get_state() is None
    store.put_state(json.dumps({"video": "a.mkv"}), now=10.0)
    store.put_state(json.dumps({"video": "b.mkv"}), now=20.0)
    assert store.get_state() == ({"video": "b.mkv"}, 20.0)


def test_command_lifecycle(tmp_path):
    store = Store(tmp_path)
    store.add_command("c1", "play", {"kind": "play", "video": "a.mkv"}, now=100.0)
    store.add_command("c2", "pause", {"kind": "pause"}, now=101.0)
    assert [c["id"] for c in store.pending_commands(now=110.0, ttl=600)] == ["c1", "c2"]
    assert store.pending_commands(110.0, 600)[0]["payload"] == {"kind": "play", "video": "a.mkv"}
    assert store.ack_command("c1", "applied", "", now=111.0) is True
    assert store.ack_command("c1", "failed", "", now=112.0) is False          # un accusé ne compte qu'une fois
    assert store.get_command("c1")["status"] == "applied"
    assert [c["id"] for c in store.pending_commands(115.0, 600)] == ["c2"]
    assert store.pending_count() == 1


def test_commands_expire_and_are_never_offered_late(tmp_path):
    store = Store(tmp_path)
    store.add_command("c1", "pause", {"kind": "pause"}, now=0.0)
    assert store.pending_commands(now=601.0, ttl=600) == []
    store.expire_commands(now=601.0, ttl=600)
    assert store.get_command("c1")["status"] == "expired"
    assert store.ack_command("c1", "applied", "", now=602.0) is False


def test_unknown_command_and_recent_list(tmp_path):
    store = Store(tmp_path)
    assert store.get_command("nope") is None and store.ack_command("nope", "applied", "", 1.0) is False
    for i in range(15):
        store.add_command(f"c{i:02d}", "pause", {"kind": "pause"}, now=float(i))
    recent = store.recent_commands(10)
    assert len(recent) == 10 and recent[0]["id"] == "c14"                       # plus récente d'abord


def test_message_is_truncated(tmp_path):
    store = Store(tmp_path)
    store.add_command("c1", "pause", {"kind": "pause"}, now=0.0)
    store.ack_command("c1", "failed", "x" * 1000, now=1.0)
    assert len(store.get_command("c1")["message"]) <= 300

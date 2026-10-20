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


def test_caption_size_defaults_to_zero_and_is_stored(tmp_path):
    store = Store(tmp_path)
    store.add("a" * 32, 60, b"png", 1.0)
    store.add("b" * 32, 60, b"png", 2.0, caption="π", caption_size=14)
    assert store.get("a" * 32)["caption_size"] == 0
    assert store.get("b" * 32)["caption_size"] == 14


def test_a_database_created_before_caption_size_is_migrated(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "relay.db")
    db.execute("CREATE TABLE items (id TEXT PRIMARY KEY, status TEXT NOT NULL, duration INTEGER NOT NULL, "
               "created_at REAL NOT NULL, fetched_at REAL, caption TEXT NOT NULL DEFAULT '')")
    db.execute("INSERT INTO items (id, status, duration, created_at, caption) VALUES ('old', 'queued', 60, 1, 'x')")
    db.commit()
    db.close()
    store = Store(tmp_path)
    assert store.get("old")["caption_size"] == 0
    store.add("c" * 32, 60, b"png", 3.0, caption_size=14)
    assert store.get("c" * 32)["caption_size"] == 14


# -- brouillons de réglages par mode ------------------------------------------

def test_drafts_merge_per_mode_and_survive_a_restart(tmp_path):
    store = Store(tmp_path)
    assert store.drafts() == {}
    store.merge_draft("pipiquant", {"pipiquant_interval": 3600})
    store.merge_draft("pipiquant", {"pipiquant_title": False, "pipiquant_interval": 7200})
    store.merge_draft("photos", {"photos_interval": 600})
    assert store.drafts() == {"pipiquant": {"pipiquant_interval": 7200, "pipiquant_title": False},
                              "photos": {"photos_interval": 600}}
    assert Store(tmp_path).drafts() == store.drafts()
    store.clear_draft("photos")
    assert "photos" not in store.drafts()


def test_activation_attaches_the_draft_and_an_applied_command_removes_what_was_sent(tmp_path):
    store = Store(tmp_path)
    store.merge_draft("pipiquant", {"pipiquant_interval": 3600, "pipiquant_title": False})
    sent = store.draft_for("pipiquant")
    store.add_command("c1", "settings", {"kind": "settings", "changes": {"mode": "pipiquant", **sent}}, now=1.0)
    store.attach_draft("pipiquant", "c1", sent)
    store.merge_draft("pipiquant", {"pipiquant_interval": 10800})          # modifié pendant l'envoi
    store.ack_command("c1", "applied", "", now=2.0)
    assert store.drafts() == {"pipiquant": {"pipiquant_interval": 10800}}  # ce qui n'avait pas été envoyé reste


def test_a_refused_or_expired_activation_keeps_the_draft(tmp_path):
    store = Store(tmp_path)
    store.merge_draft("photos", {"photos_interval": 600})
    store.add_command("c1", "settings", {"kind": "settings", "changes": {"mode": "photos", "photos_interval": 600}}, 1.0)
    store.attach_draft("photos", "c1", store.draft_for("photos"))
    store.ack_command("c1", "failed", "refusé", now=2.0)
    assert store.drafts() == {"photos": {"photos_interval": 600}}
    store.add_command("c2", "settings", {"kind": "settings", "changes": {"mode": "photos", "photos_interval": 600}}, 10.0)
    store.attach_draft("photos", "c2", store.draft_for("photos"))
    store.expire_commands(now=10_000.0, ttl=600)
    assert store.drafts() == {"photos": {"photos_interval": 600}}
    store.add_command("c3", "settings", {"kind": "settings", "changes": {"mode": "photos"}}, 20_000.0)
    store.attach_draft("photos", "c3", store.draft_for("photos"))
    store.ack_command("c3", "applied", "", now=20_001.0)
    assert store.drafts() == {}


def test_the_current_slide_is_kept_per_source_and_survives_a_restart(tmp_path):
    store = Store(tmp_path)
    assert store.current_slide("pipiquant") is None
    store.save_current_slide("pipiquant", {"decimals": 87, "angle": 214}, b"\xff\xd8thumb", now=5.0)
    store.save_current_slide("photos", {"index": 3, "count": 9}, b"\xff\xd8other", now=6.0)
    assert store.current_slide("pipiquant") == ({"decimals": 87, "angle": 214}, b"\xff\xd8thumb", 5.0)
    assert Store(tmp_path).current_slide("photos") == ({"index": 3, "count": 9}, b"\xff\xd8other", 6.0)
    assert store.current_slide("slowmovie") is None

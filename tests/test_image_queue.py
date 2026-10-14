import pytest

from slowmovie_inkdrop.image_queue import ImageQueue, QueueFull

A, B, C = "a" * 32, "b" * 32, "c" * 32


def test_fifo_order(tmp_path):
    q = ImageQueue(tmp_path, max_items=5)
    q.add(B, b"2", 60, now=20)
    q.add(A, b"1", 60, now=10)
    assert [i.id for i in q.items()] == [A, B]
    assert q.peek(now=30).id == A


def test_full_queue_raises(tmp_path):
    q = ImageQueue(tmp_path, max_items=1)
    q.add(A, b"1", 60, now=0)
    with pytest.raises(QueueFull):
        q.add(B, b"2", 60, now=1)


def test_expired_images_are_dropped(tmp_path):
    q = ImageQueue(tmp_path, expiry_seconds=100)
    q.add(A, b"1", 60, now=0)
    q.add(B, b"2", 60, now=90)
    assert q.peek(now=150).id == B
    assert len(q) == 1


def test_persists_across_instances(tmp_path):
    ImageQueue(tmp_path).add(A, b"1", 60, now=0)
    assert [i.id for i in ImageQueue(tmp_path).items()] == [A]


def test_discard_removes_files_and_remembers_id(tmp_path):
    q = ImageQueue(tmp_path)
    item = q.add(A, b"1", 60, now=0)
    q.discard(item)
    assert not item.path.exists() and len(q) == 0
    assert q.known(A)
    assert ImageQueue(tmp_path).known(A)  # mémorisé sur disque


def test_known_covers_queued_items(tmp_path):
    q = ImageQueue(tmp_path)
    q.add(A, b"1", 60, now=0)
    assert q.known(A) and not q.known(B)


@pytest.mark.parametrize("bad", ["../evil", "A" * 32, "abc", "", "g" * 32])
def test_rejects_unsafe_ids(tmp_path, bad):
    with pytest.raises(ValueError):
        ImageQueue(tmp_path).add(bad, b"1", 60, now=0)


def test_remove_and_clear(tmp_path):
    q = ImageQueue(tmp_path)
    q.add(A, b"1", 60, now=0)
    q.add(B, b"2", 60, now=1)
    q.add(C, b"3", 60, now=2)
    assert q.remove(B).id == B and q.remove(B) is None
    assert [i.id for i in q.clear()] == [A, C]
    assert len(q) == 0


def test_corrupt_metadata_is_ignored(tmp_path):
    q = ImageQueue(tmp_path)
    (tmp_path / f"{A}.json").write_text("{pas du json")
    assert q.items() == []


def test_caption_is_persisted_with_the_image(tmp_path):
    q = ImageQueue(tmp_path)
    q.add(A, b"1", 60, now=0, caption="Bonne année")
    assert ImageQueue(tmp_path).items()[0].caption == "Bonne année"


def test_metadata_written_before_captions_existed_still_loads(tmp_path):
    import json
    (tmp_path / f"{A}.json").write_text(json.dumps({"id": A, "duration": 60, "received_at": 0}))
    assert ImageQueue(tmp_path).items()[0].caption == ""

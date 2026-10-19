import json

from slowmovie_inkdrop.library import Library
from slowmovie_inkdrop.videos import VideoInfo

INFO = VideoInfo(24.0, 100.0, 2400, 1920, 1080)


def make(tmp_path, probe=lambda p: INFO):
    vdir = tmp_path / "Videos"
    vdir.mkdir(exist_ok=True)
    return vdir, Library(vdir, tmp_path / "library.json", probe, settle_seconds=10)


def test_empty_directory_has_no_ready_videos(tmp_path):
    _, lib = make(tmp_path)
    lib.poll(0)
    assert lib.ready() == []


def test_new_file_needs_to_settle(tmp_path):
    vdir, lib = make(tmp_path)
    (vdir / "a.mp4").write_bytes(b"x" * 10)
    lib.poll(0)
    assert lib.ready() == []
    lib.poll(5)
    assert lib.ready() == []
    lib.poll(11)
    assert lib.ready() == [vdir / "a.mp4"]


def test_growing_file_is_never_ready(tmp_path):
    vdir, lib = make(tmp_path)
    f = vdir / "a.mp4"
    f.write_bytes(b"x" * 10)
    lib.poll(0)
    f.write_bytes(b"x" * 20)   # la copie Samba continue
    lib.poll(11)
    assert lib.ready() == []
    lib.poll(22)               # taille stable depuis 11 s
    assert lib.ready() == [f]


def test_corrupt_file_becomes_error_not_ready(tmp_path):
    def boom(path):
        raise ValueError("illisible")

    vdir, lib = make(tmp_path, probe=boom)
    (vdir / "a.mp4").write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    assert lib.ready() == []
    [row] = lib.rows()
    assert row["status"] == "error" and "illisible" in row["error"]


def test_hidden_and_unsupported_files_ignored(tmp_path):
    vdir, lib = make(tmp_path)
    (vdir / ".a.mp4").write_bytes(b"x")
    (vdir / "notes.txt").write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    assert lib.rows() == []


def test_removed_file_disappears(tmp_path):
    vdir, lib = make(tmp_path)
    f = vdir / "a.mp4"
    f.write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    f.unlink()
    lib.poll(12)
    assert lib.ready() == []


def test_known_ready_file_survives_restart_without_waiting(tmp_path):
    vdir, lib = make(tmp_path)
    (vdir / "a.mp4").write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    _, lib2 = make(tmp_path)
    lib2.poll(1000)
    assert lib2.ready() == [vdir / "a.mp4"]
    assert json.loads((tmp_path / "library.json").read_text())["a.mp4"]["status"] == "ready"


def test_error_entry_is_probed_again_when_the_file_is_rewritten_with_the_same_size(tmp_path):
    import os
    calls = []

    def flaky(path):
        calls.append(path)
        if len(calls) == 1:
            raise ValueError("index manquant")
        return INFO

    vdir, lib = make(tmp_path, probe=flaky)
    f = vdir / "a.mp4"
    f.write_bytes(b"x" * 10)
    lib.poll(0)
    lib.poll(11)
    assert lib.rows()[0]["status"] == "error"
    os.utime(f, ns=(1, 2_000_000_000))                  # copie terminée : même taille, autre date
    lib.poll(12)
    lib.poll(23)
    assert lib.ready() == [f]


def test_ready_file_that_changes_again_leaves_the_ready_list(tmp_path):
    import os
    vdir, lib = make(tmp_path)
    f = vdir / "a.mp4"
    f.write_bytes(b"x" * 10)
    lib.poll(0)
    lib.poll(11)
    assert lib.ready() == [f]
    os.utime(f, ns=(1, 3_000_000_000))                  # la copie reprend
    lib.poll(12)
    assert lib.ready() == []


def test_ready_entry_records_fps_and_subtitle_tracks(tmp_path):
    info = VideoInfo(24.0, 100.0, 2400, 1920, 1080, ("subrip",))
    vdir, lib = make(tmp_path, probe=lambda p: info)
    (vdir / "a.mp4").write_bytes(b"x")
    lib.poll(0)
    lib.poll(11)
    row = lib.rows()[0]
    assert row["fps"] == 24.0 and row["subtitle_codecs"] == ["subrip"]


def test_ready_entry_saved_by_an_older_version_is_probed_again_without_waiting(tmp_path):
    vdir = tmp_path / "Videos"
    vdir.mkdir()
    f = vdir / "a.mp4"
    f.write_bytes(b"x")
    st = f.stat()
    (tmp_path / "library.json").write_text(json.dumps({"a.mp4": {
        "size": st.st_size, "mtime": st.st_mtime_ns, "status": "ready", "changed_at": 0,
        "frames": 5, "duration": 1.0}}))
    lib = Library(vdir, tmp_path / "library.json", lambda p: INFO, settle_seconds=10)
    lib.poll(1)                                   # pas d'attente de 10 s : le film était déjà prêt
    assert lib.rows()[0]["fps"] == 24.0 and lib.ready() == [f]

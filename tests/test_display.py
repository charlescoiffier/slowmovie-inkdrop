from PIL import Image

from slowmovie_inkdrop.display import FileDisplay, make_display


def test_file_display_records_events_and_writes_png(tmp_path):
    d = FileDisplay(tmp_path)
    d.prepare()
    d.display(Image.new("RGB", (800, 480), "red"))
    d.sleep()
    assert d.events == ["prepare", "display", "sleep"]
    assert (tmp_path / "last.png").exists()
    assert (d.width, d.height) == (800, 480)


def test_make_display_uses_fake_dir(tmp_path):
    d = make_display("waveshare_epd.epd7in5_V2", fake_dir=tmp_path)
    assert isinstance(d, FileDisplay)


def test_make_display_uses_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWMOVIE_FAKE_DISPLAY", str(tmp_path))
    assert isinstance(make_display("peu importe"), FileDisplay)

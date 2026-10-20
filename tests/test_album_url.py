import pytest

from slowmovie_inkdrop.album_url import ALBUM_HOSTS, album_host, valid_album_url


@pytest.mark.parametrize("url", [
    "https://photos.app.goo.gl/abc123",
    "https://photos.google.com/share/AF1Qip?key=abc",
])
def test_google_photos_links_are_accepted(url):
    assert valid_album_url(url)


@pytest.mark.parametrize("url", [
    "http://photos.app.goo.gl/abc123",
    "https://example.com/abc",
    "https://photos.google.com.evil.com/x",
    "https://evil.com/photos.google.com/x",
    "https://user@photos.google.com/x",
    "https://user:pw@photos.google.com/x",
    "https://photos.google.com:8443/x",
    "https://photos.google.com",
    "https://photos.google.com/",
    "",
    "   ",
    "https://photos.google.com/" + "a" * 500,
    "https://photos.google.com/a b",
    "https://photos.google.com/a\nb",
    "https://photos.google.com/a\x00b",
    "ftp://photos.google.com/x",
    "//photos.google.com/x",
    "javascript:alert(1)",
])
def test_other_links_are_refused(url):
    assert not valid_album_url(url)


def test_non_strings_are_refused():
    for value in (None, 12, b"https://photos.google.com/x", ["https://photos.google.com/x"]):
        assert not valid_album_url(value)


def test_album_host():
    assert album_host("https://photos.google.com/share/x?key=y") == "photos.google.com"
    assert album_host("https://photos.app.goo.gl/x") == "photos.app.goo.gl"
    assert album_host("https://evil.com/x") == ""
    assert album_host("") == ""
    assert set(ALBUM_HOSTS) == {"photos.app.goo.gl", "photos.google.com"}

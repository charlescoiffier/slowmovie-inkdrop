import json
import logging
import random

import httpx
import pytest

from slowmovie_inkdrop.relay.photos import AlbumPhotos, PhotosError, parse_album_page

SHORT = "https://photos.app.goo.gl/SECRETshort"
LONG = "https://photos.google.com/share/AF1QipSECRETlong?key=KEYKEY"
IMG = "https://lh3.googleusercontent.com/pw/AP1GczPHOTO"


def page(urls, *, tail='""'):
    items = [[f"AF1Qip{i}", [u, 1600, 1200, None], 1690000000000, "x"] for i, u in enumerate(urls)]
    data = json.dumps([None, items, "", [], None, 0])
    return ("<html><head><title>Album - Google Photos</title></head><body><script>"
            "AF_initDataCallback({key: 'ds:0', hash: '1', data:[], sideChannel: {}});"
            f"AF_initDataCallback({{key: 'ds:1', hash: '2', data:{data}, sideChannel: {{}}}});"
            "</script></body></html>")


def test_parse_album_page_lists_photos_in_order():
    urls = [f"{IMG}{n}" for n in "ABC"]
    assert parse_album_page(page(urls)) == urls


def test_duplicates_and_foreign_hosts_are_dropped():
    urls = [IMG + "A", IMG + "A", "https://evil.example/pw/x", IMG + "B"]
    assert parse_album_page(page(urls)) == [IMG + "A", IMG + "B"]


@pytest.mark.parametrize("html", [
    "", "<html>rien</html>", page([]),
    "AF_initDataCallback({key: 'ds:1', hash: '2', data:{pas du json, sideChannel: {}});",
    "AF_initDataCallback({key: 'ds:1', hash: '2', data:[null, 5, \"\"], sideChannel: {}});",
])
def test_an_unreadable_page_is_reported(html):
    with pytest.raises(PhotosError) as caught:
        parse_album_page(html)
    assert "illisible" in caught.value.message


class Web:
    """Faux Google : redirection du lien court, page de l'album, images."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.pages = {"/SECRETshort": httpx.Response(302, headers={"location": LONG})}
        self.photos = [IMG + "A", IMG + "B", IMG + "C"]
        self.album = httpx.Response(200, text=page(self.photos), headers={"content-type": "text/html"})
        self.image = httpx.Response(200, content=b"\xff\xd8\xff\xe0jpeg", headers={"content-type": "image/jpeg"})
        self.error: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.error:
            raise self.error
        if request.url.host == "photos.app.goo.gl":
            return self.pages[request.url.path]
        if request.url.host == "photos.google.com":
            return self.album
        return self.image


def make(web, **kw):
    clock = kw.pop("clock", None) or Clock()
    return AlbumPhotos(httpx.Client(transport=httpx.MockTransport(web)), clock=clock,
                       rng=kw.pop("rng", None) or random.Random(1), **kw), clock


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_the_short_link_is_followed_and_a_photo_is_downloaded_resized():
    web = Web()
    album, _ = make(web)
    assert album.photo(SHORT).startswith(b"\xff\xd8")
    hosts = [c.url.host for c in web.calls]
    assert hosts[:2] == ["photos.app.goo.gl", "photos.google.com"] and hosts[-1] == "lh3.googleusercontent.com"
    assert str(web.calls[-1].url).endswith("=w1000-h1000")


def test_the_photo_is_drawn_at_random_from_the_list():
    web = Web()
    album, clock = make(web, rng=random.Random(5), cache_seconds=10**9)
    chosen = {str(album.photo(LONG) and web.calls[-1].url).rsplit("=", 1)[0] for _ in range(40)}
    assert chosen == set(web.photos)


def test_the_listing_is_cached_then_refreshed():
    web = Web()
    album, clock = make(web, cache_seconds=100)
    album.listing(LONG)
    album.listing(LONG)
    pages = [c for c in web.calls if c.url.host == "photos.google.com"]
    assert len(pages) == 1
    clock.t += 101
    album.listing(LONG)
    assert len([c for c in web.calls if c.url.host == "photos.google.com"]) == 2


@pytest.mark.parametrize("target", [
    "https://evil.example/x", "http://photos.google.com/share/x", "https://photos.google.com:8443/share/x",
    "https://169.254.169.254/latest", "https://user@photos.google.com/share/x",
])
def test_redirects_leaving_google_photos_are_refused(target):
    web = Web()
    web.pages["/SECRETshort"] = httpx.Response(302, headers={"location": target})
    album, _ = make(web)
    with pytest.raises(PhotosError):
        album.listing(SHORT)
    assert all(c.url.host in ("photos.app.goo.gl",) for c in web.calls)


def test_too_many_redirects_are_refused():
    web = Web()
    web.pages["/SECRETshort"] = httpx.Response(302, headers={"location": SHORT})
    album, _ = make(web)
    with pytest.raises(PhotosError):
        album.listing(SHORT)
    assert len(web.calls) <= 4


@pytest.mark.parametrize("url", ["https://example.com/x", "", "http://photos.google.com/x", "photos.google.com"])
def test_an_invalid_link_never_reaches_the_network(url):
    web = Web()
    album, _ = make(web)
    with pytest.raises(PhotosError):
        album.listing(url)
    assert not web.calls


def test_an_unreadable_album_page_is_reported_and_not_cached():
    web = Web()
    web.album = httpx.Response(200, text="<html>plus de données</html>")
    album, _ = make(web)
    with pytest.raises(PhotosError) as caught:
        album.photo(LONG)
    assert "illisible" in caught.value.message
    web.album = httpx.Response(200, text=page([IMG + "A"]))
    assert album.listing(LONG) == [IMG + "A"]


def test_foreign_image_host_in_the_page_is_never_downloaded():
    web = Web()
    web.album = httpx.Response(200, text=page(["https://evil.example/pw/x"]))
    album, _ = make(web)
    with pytest.raises(PhotosError):
        album.photo(LONG)
    assert all(c.url.host != "evil.example" for c in web.calls)


def test_a_photo_that_is_not_an_image_is_refused():
    web = Web()
    web.image = httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    album, _ = make(web)
    with pytest.raises(PhotosError):
        album.photo(LONG)


def test_a_photo_over_the_limit_is_cut_while_streaming():
    web = Web()
    read = []

    def chunks():
        for _ in range(100):
            read.append(1)
            yield b"x" * 1000

    web.image = httpx.Response(200, content=chunks(), headers={"content-type": "image/jpeg"})
    album, _ = make(web, max_bytes=3000)
    with pytest.raises(PhotosError) as caught:
        album.photo(LONG)
    assert "lourde" in caught.value.message and len(read) < 10


def test_an_image_redirect_is_not_followed():
    web = Web()
    web.image = httpx.Response(302, headers={"location": "https://evil.example/x"})
    album, _ = make(web)
    with pytest.raises(PhotosError):
        album.photo(LONG)
    assert all(c.url.host != "evil.example" for c in web.calls)


@pytest.mark.parametrize("error", [httpx.ReadTimeout("t"), httpx.ConnectError("c")])
def test_network_failures_are_reported(error):
    web = Web()
    web.error = error
    album, _ = make(web)
    with pytest.raises(PhotosError) as caught:
        album.photo(SHORT)
    assert "injoignable" in caught.value.message


def test_the_link_never_appears_in_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    web = Web()
    web.album = httpx.Response(500)
    album, _ = make(web)
    with pytest.raises(PhotosError) as caught:
        album.photo(LONG)
    web.error = httpx.ConnectError("c")
    with pytest.raises(PhotosError) as caught2:
        album.photo(SHORT)
    for text in (caught.value.message, caught2.value.message, caplog.text, repr(caught.value), repr(caught2.value)):
        assert "SECRET" not in text and "KEYKEY" not in text


def test_photo_with_position_reports_rank_and_album_size():
    web = Web()
    album, _ = make(web, rng=random.Random(2), cache_seconds=10**9)
    seen = set()
    for _ in range(30):
        data, index, count = album.photo_with_position(LONG)
        assert data.startswith(b"\xff\xd8") and count == 3 and 1 <= index <= 3
        assert str(web.calls[-1].url).startswith(web.photos[index - 1])
        seen.add(index)
    assert seen == {1, 2, 3}

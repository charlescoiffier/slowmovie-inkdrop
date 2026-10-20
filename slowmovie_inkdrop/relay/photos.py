"""Lecture d'un album Google Photos par son lien de partage (page publique, méthode non officielle).

Le lien est un secret : il n'apparaît ni dans les messages d'erreur ni dans les journaux."""
from __future__ import annotations

import json
import logging
import random
import re
import time
from urllib.parse import urljoin

import httpx

from ..album_url import valid_album_url

log = logging.getLogger("slowmovie.relay")

IMAGE_HOST = re.compile(r"lh\d\.googleusercontent\.com")
PHOTO_PREFIX = "https://lh3.googleusercontent.com/pw/"
SIZE_SUFFIX = "=w1000-h1000"           # photo réduite : l'écran ne fait que 800 x 480
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_HOPS = 3
CACHE_ENTRIES = 4
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_BLOCK = re.compile(r"AF_initDataCallback\(\{key: 'ds:\d+'.*?data:(.*?), sideChannel", re.S)


class PhotosError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


UNREADABLE = "Album illisible : la page de l'album n'a pas pu être lue."
UNREACHABLE = "Album injoignable. Réessayez dans un instant."


def parse_album_page(html: str) -> list[str]:
    """Adresses des photos de la page d'un album, dans l'ordre, sans doublon."""
    found: list[str] = []
    for raw in _BLOCK.findall(html or ""):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        items = data[1] if isinstance(data, list) and len(data) > 1 else None
        if not isinstance(items, list):
            continue
        for item in items:
            try:
                url = item[1][0]
            except (TypeError, IndexError, KeyError):
                continue
            if isinstance(url, str) and url.startswith(PHOTO_PREFIX) and url not in found:
                found.append(url)
    if not found:
        raise PhotosError(UNREADABLE)
    return found


class AlbumPhotos:
    def __init__(self, client: httpx.Client, *, clock=time.time, rng: random.Random | None = None,
                 cache_seconds: float = 21600, max_bytes: int = 10 * 1024 * 1024, timeout: float = 15.0):
        for name in ("httpx", "httpcore"):       # leurs journaux INFO contiennent l'adresse demandée, donc le lien
            logging.getLogger(name).setLevel(logging.WARNING)
        self.client, self.clock = client, clock
        self.rng = rng or random.SystemRandom()
        self.cache_seconds, self.max_bytes, self.timeout = cache_seconds, max_bytes, timeout
        self._cache: dict[str, tuple[float, list[str]]] = {}

    def listing(self, album_url: str) -> list[str]:
        if not valid_album_url(album_url):
            raise PhotosError("Le lien de l'album n'est pas valide.")
        cached = self._cache.get(album_url)
        if cached and self.clock() - cached[0] < self.cache_seconds:
            return cached[1]
        urls = parse_album_page(self._page(album_url))
        if len(self._cache) >= CACHE_ENTRIES:
            self._cache.pop(next(iter(self._cache)))
        self._cache[album_url] = (self.clock(), urls)
        return urls

    def photo(self, album_url: str) -> bytes:
        return self._download(self.rng.choice(self.listing(album_url)) + SIZE_SUFFIX)

    def _page(self, url: str) -> str:
        for _ in range(MAX_HOPS + 1):
            try:
                with self.client.stream("GET", url, headers={"User-Agent": USER_AGENT},
                                        timeout=self.timeout, follow_redirects=False) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        if not valid_album_url(url):
                            raise PhotosError(UNREADABLE)
                        continue
                    if response.status_code != 200:
                        log.warning("album : réponse %s", response.status_code)
                        raise PhotosError(UNREADABLE)
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data += chunk
                        if len(data) > MAX_PAGE_BYTES:
                            raise PhotosError(UNREADABLE)
                    return data.decode("utf-8", "replace")
            except httpx.HTTPError as exc:
                log.warning("album injoignable : %s", type(exc).__name__)
                raise PhotosError(UNREACHABLE) from None
        raise PhotosError(UNREADABLE)

    def _download(self, url: str) -> bytes:
        parts = httpx.URL(url)
        if parts.scheme != "https" or not IMAGE_HOST.fullmatch(parts.host or ""):
            raise PhotosError(UNREADABLE)
        try:
            with self.client.stream("GET", url, headers={"User-Agent": USER_AGENT},
                                    timeout=self.timeout, follow_redirects=False) as response:
                if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
                    log.warning("photo d'album : réponse %s", response.status_code)
                    raise PhotosError("Photo d'album illisible.")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data += chunk
                    if len(data) > self.max_bytes:
                        raise PhotosError("Photo d'album trop lourde.")
                return bytes(data)
        except httpx.HTTPError as exc:
            log.warning("photo d'album injoignable : %s", type(exc).__name__)
            raise PhotosError(UNREACHABLE) from None

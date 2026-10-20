"""Validation du lien de partage d'un album Google Photos : jamais d'adresse arbitraire."""
from __future__ import annotations

from urllib.parse import urlsplit

ALBUM_HOSTS = ("photos.app.goo.gl", "photos.google.com")
MAX_LENGTH = 500


def valid_album_url(url) -> bool:
    if not isinstance(url, str) or not url or len(url) > MAX_LENGTH:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname in ALBUM_HOSTS and port is None
            and parts.username is None and parts.password is None
            and parts.path.strip("/") != "")


def album_host(url) -> str:
    return urlsplit(url).hostname or "" if valid_album_url(url) else ""

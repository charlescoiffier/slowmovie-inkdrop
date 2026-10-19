"""Mot de passe haché (scrypt) et limitation des essais."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from collections import defaultdict, deque
from collections.abc import Callable

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt:{base64.b64encode(salt).decode()}:{base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split(":")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class RateLimiter:
    def __init__(self, clock: Callable[[], float], max_failures: int = 5,
                 block_seconds: float = 900, max_sends: int = 20, window: float = 3600):
        self.clock = clock
        self.max_failures, self.block_seconds = max_failures, block_seconds
        self.max_sends, self.window = max_sends, window
        self._failures: dict[str, deque] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._sends: dict[str, deque] = defaultdict(deque)

    def blocked(self, ip: str) -> float:
        until = self._blocked_until.get(ip, 0.0)
        remaining = until - self.clock()
        if remaining <= 0:
            self._blocked_until.pop(ip, None)
            return 0.0
        return remaining

    def fail(self, ip: str) -> None:
        now = self.clock()
        failures = self._failures[ip]
        failures.append(now)
        while failures and now - failures[0] > self.block_seconds:
            failures.popleft()
        if len(failures) >= self.max_failures:
            self._blocked_until[ip] = now + self.block_seconds
            failures.clear()

    def success(self, ip: str) -> None:
        self._failures.pop(ip, None)

    def allow_send(self, ip: str) -> bool:
        now = self.clock()
        sends = self._sends[ip]
        while sends and now - sends[0] > self.window:
            sends.popleft()
        if len(sends) >= self.max_sends:
            return False
        sends.append(now)
        return True


def make_session_token(secret: bytes, now: float, ttl: float) -> str:
    body = f"{int(now + ttl)}.{secrets.token_hex(8)}"
    signature = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_session_token(secret: bytes, token: str, now: float) -> bool:
    try:
        expiry, nonce, signature = token.split(".")
        expected = hmac.new(secret, f"{expiry}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and int(expiry) > now
    except (ValueError, AttributeError):
        return False

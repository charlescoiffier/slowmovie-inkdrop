from slowmovie_inkdrop.relay.security import RateLimiter, hash_password, verify_password


def test_hash_and_verify():
    stored = hash_password("correct horse")
    assert stored != "correct horse" and "correct" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("autre", stored)


def test_hashes_are_salted():
    assert hash_password("x") != hash_password("x")


def test_verify_rejects_malformed_hash():
    assert not verify_password("x", "n'importe quoi")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_blocks_after_five_failures_then_unblocks():
    clock = Clock()
    limiter = RateLimiter(clock)
    for _ in range(4):
        limiter.fail("1.2.3.4")
    assert limiter.blocked("1.2.3.4") == 0
    limiter.fail("1.2.3.4")
    assert limiter.blocked("1.2.3.4") > 0
    assert limiter.blocked("5.6.7.8") == 0
    clock.t = 901
    assert limiter.blocked("1.2.3.4") == 0


def test_success_resets_failures():
    limiter = RateLimiter(Clock())
    for _ in range(4):
        limiter.fail("ip")
    limiter.success("ip")
    limiter.fail("ip")
    assert limiter.blocked("ip") == 0


def test_send_window():
    clock = Clock()
    limiter = RateLimiter(clock, max_sends=2, window=100)
    assert limiter.allow_send("ip") and limiter.allow_send("ip")
    assert not limiter.allow_send("ip")
    clock.t = 101
    assert limiter.allow_send("ip")


def test_hash_is_safe_in_env_files():
    # '$' est interpolé par docker compose dans un fichier .env : le haché ne doit pas en contenir
    stored = hash_password("x")
    assert "$" not in stored and " " not in stored and "'" not in stored
    assert verify_password("x", stored)


def test_session_token_roundtrip_expiry_and_tampering():
    from slowmovie_inkdrop.relay.security import make_session_token, verify_session_token
    secret = b"s" * 32
    token = make_session_token(secret, now=1000.0, ttl=100.0)
    assert verify_session_token(secret, token, now=1050.0)
    assert not verify_session_token(secret, token, now=1101.0)               # expiré
    assert not verify_session_token(b"autre" * 8, token, now=1050.0)         # autre clé
    expiry, nonce, sig = token.split(".")
    assert not verify_session_token(secret, f"{int(expiry) + 999}.{nonce}.{sig}", now=1050.0)   # falsifié
    for bad in ("", "x", "a.b", "a.b.c.d", "abc.def.ghi", ".."):
        assert not verify_session_token(secret, bad, now=1050.0)
    assert make_session_token(secret, 1000.0, 100.0) != token                  # aléa différent

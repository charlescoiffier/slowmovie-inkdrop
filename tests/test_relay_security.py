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

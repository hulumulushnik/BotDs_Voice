from cogs.triarch_voice.antispam import Cooldown, RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_cooldown_blocks_until_expired():
    clock = FakeClock()
    cd = Cooldown(30, clock)
    assert cd.remaining("u") == 0
    cd.trigger("u")
    clock.now = 10
    assert cd.remaining("u") == 20
    clock.now = 30
    assert cd.remaining("u") == 0


def test_rate_limiter_sliding_window():
    clock = FakeClock()
    rl = RateLimiter(limit=3, period=10, clock=clock)
    assert [rl.hit("u") for _ in range(3)] == [0, 0, 0]
    assert rl.hit("u") == 10  # 4-е нажатие отклонено
    clock.now = 5
    assert rl.hit("u") == 5  # спам не продлевает блокировку
    clock.now = 10
    assert rl.hit("u") == 0  # окно сдвинулось
    assert rl.hit("other") == 0  # ключи независимы

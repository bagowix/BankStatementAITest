import datetime as dt

import pytest

from autostudio.booking.limits import RateLimiter

T0 = dt.datetime(2026, 10, 20, 8, 0, tzinfo=dt.UTC)


def test__rate_limiter__over_limit__refused_until_window_passes():
    limiter = RateLimiter(2, dt.timedelta(minutes=10))
    assert limiter.allow('tg:1', T0) and limiter.allow('tg:1', T0 + dt.timedelta(minutes=1))
    assert not limiter.allow('tg:1', T0 + dt.timedelta(minutes=2))
    assert limiter.allow('tg:2', T0 + dt.timedelta(minutes=2))  # у каждого свой счёт
    assert limiter.allow('tg:1', T0 + dt.timedelta(minutes=10))  # первое событие вышло из окна


def test__rate_limiter__many_keys__stale_ones_pruned():
    limiter = RateLimiter(1, dt.timedelta(minutes=1))
    for index in range(1001):
        limiter.allow(f'tg:{index}', T0)
    assert len(limiter) == 1001
    limiter.allow('tg:fresh', T0 + dt.timedelta(minutes=5))
    assert len(limiter) == 1


def test__rate_limiter__zero_limit__value_error():
    with pytest.raises(ValueError, match='limit'):
        RateLimiter(0, dt.timedelta(minutes=1))

from bankstatement.web.limits import TokenBucket


def test__take__within_burst__allowed():
    bucket = TokenBucket(burst=3, refill=1.0)

    assert [bucket.take('ip', now=0) for _ in range(3)] == [True, True, True]


def test__take__over_burst__denied():
    bucket = TokenBucket(burst=2, refill=1.0)

    for _ in range(2):
        bucket.take('ip', now=0)

    assert bucket.take('ip', now=0) is False


def test__take__after_waiting__refilled():
    """Ведро наполняется временем, а не сбросом: поток ограничен, всплеск — нет."""
    bucket = TokenBucket(burst=2, refill=1.0)

    for _ in range(2):
        bucket.take('ip', now=0)

    assert bucket.take('ip', now=0.5) is False
    assert bucket.take('ip', now=1.5) is True


def test__take__different_clients__counted_apart():
    bucket = TokenBucket(burst=1, refill=1.0)

    assert bucket.take('первый', now=0) is True
    assert bucket.take('второй', now=0) is True
    assert bucket.take('первый', now=0) is False


def test__forget__silent_clients__dropped():
    """Словарь клиентов не должен расти без предела."""
    bucket = TokenBucket(burst=1, refill=1.0)
    bucket.take('ip', now=0)

    bucket.forget(older_than=10, now=100)

    assert bucket.take('ip', now=100) is True


def test__take__too_many_clients__silent_ones_forgotten_on_the_way(monkeypatch):
    """Уборка встроена в приём: отдельный вызов забыли бы, и словарь рос бы вечно."""
    from bankstatement.web import limits

    monkeypatch.setattr(limits, 'MAX_CLIENTS', 2)
    bucket = TokenBucket(burst=1, refill=1.0)
    bucket.take('a', now=0)
    bucket.take('b', now=0)

    bucket.take('c', now=limits.FORGET_AFTER + 1)

    assert set(bucket._seen) == {'c'}

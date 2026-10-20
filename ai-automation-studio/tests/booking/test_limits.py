import datetime as dt

import pytest

from autostudio.booking.limits import Handoff, HandoffGate, RateLimiter, same_text

T0 = dt.datetime(2026, 10, 20, 8, 0, tzinfo=dt.UTC)
MIN = dt.timedelta(minutes=1)


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


def test__rate_limiter__would_allow_and_forget__check_without_counting_then_reset():
    limiter = RateLimiter(1, dt.timedelta(minutes=10))
    assert limiter.would_allow('tg:1', T0) and limiter.would_allow('tg:1', T0)  # проверка не учитывается
    assert limiter.allow('tg:1', T0)
    assert not limiter.would_allow('tg:1', T0 + MIN)
    assert limiter.would_allow('tg:1', T0 + 10 * MIN)
    limiter.forget('tg:1')
    limiter.forget('tg:unknown')
    assert limiter.allow('tg:1', T0 + MIN)


def make_gate() -> HandoffGate:
    return HandoffGate(burst=2, burst_window=10 * MIN, daily=3, repeat_window=60 * MIN)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [('  Есть   ПАРКОВКА?? ', 'есть парковка'), ('«Где вы?»', 'где вы'), ('Call me\nback!', 'call me back')],
)
def test__same_text__case_spaces_edge_punctuation__normalized(text, expected):
    assert same_text(text) == expected


def test__handoff_gate__repeat_of_forwarded_text__repeat_not_counted_until_memory_expires():
    gate = make_gate()
    assert gate.decide('tg:1', 'Is there parking?', T0) == Handoff.FORWARD
    assert gate.decide('tg:1', '  is there   PARKING??', T0 + MIN) == Handoff.REPEAT
    assert gate.decide('tg:1', 'Can I pay by card?', T0 + 2 * MIN) == Handoff.FORWARD  # повтор не съел лимит
    assert gate.decide('tg:2', 'Is there parking?', T0 + 2 * MIN) == Handoff.FORWARD  # у каждого своя память
    assert gate.decide('tg:1', 'Is there parking?', T0 + 61 * MIN) == Handoff.FORWARD  # час прошёл — новый вопрос


def test__handoff_gate__burst_then_daily__limited_refusals_not_counted():
    gate = make_gate()
    assert [gate.decide('tg:1', f'q{index}', T0) for index in range(5)] == [
        Handoff.FORWARD,
        Handoff.FORWARD,
        Handoff.LIMITED,
        Handoff.LIMITED,
        Handoff.LIMITED,
    ]
    # Три отказа не съели суточный лимит: в новом коротком окне проходит третий текст.
    assert gate.decide('tg:1', 'q2', T0 + 10 * MIN) == Handoff.FORWARD
    assert gate.decide('tg:1', 'q3', T0 + 30 * MIN) == Handoff.LIMITED  # суточный
    assert gate.decide('tg:1', 'q0', T0 + 31 * MIN) == Handoff.REPEAT  # повтор — правда, а не отказ
    assert gate.decide('tg:1', 'q3', T0 + 24 * 60 * MIN) == Handoff.FORWARD


def test__handoff_gate__release__counters_and_repeat_memory_reset():
    gate = make_gate()
    gate.decide('tg:1', 'q0', T0)
    gate.decide('tg:1', 'q1', T0)
    assert gate.decide('tg:1', 'q2', T0) == Handoff.LIMITED
    gate.release('tg:1')
    gate.release('tg:unknown')
    assert gate.decide('tg:1', 'q0', T0 + MIN) == Handoff.FORWARD  # и лимит, и память повторов — заново


def test__handoff_gate__many_keys__stale_ones_pruned():
    gate = make_gate()
    for index in range(1001):
        gate.decide(f'tg:{index}', 'hello', T0)
    assert len(gate) == 1001
    gate.decide('tg:fresh', 'hello', T0 + 2 * 60 * MIN)
    assert len(gate) == 1

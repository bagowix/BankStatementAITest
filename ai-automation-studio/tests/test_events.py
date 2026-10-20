import datetime as dt

import pytest

from autostudio.events import EventLog


def test__event_log__record_and_between__ordered_and_bounded(tmp_path):
    log = EventLog(tmp_path / 'c' / 'events.sqlite')
    tz = dt.timezone(dt.timedelta(hours=4))
    log.record('booking', 'booking.created', dt.datetime(2026, 10, 2, 10, tzinfo=tz), {'service': 'cut'})
    log.record('booking', 'booking.created', dt.datetime(2026, 10, 1, 10, tzinfo=tz))
    log.record('booking', 'booking.created', dt.datetime(2026, 11, 1, 0, 30, tzinfo=tz))
    events = log.between(dt.datetime(2026, 10, 1, tzinfo=tz), dt.datetime(2026, 11, 1, tzinfo=tz))
    assert [e.at.day for e in events] == [1, 2]
    assert events[1].payload == {'service': 'cut'}
    assert events[0].at.tzinfo is not None


def test__event_log__naive_time__value_error(tmp_path):
    with pytest.raises(ValueError, match='часовым поясом'):
        EventLog(tmp_path / 'e.sqlite').record('x', 'y', dt.datetime(2026, 1, 1))  # noqa: DTZ001


def test__event_log__last__latest_of_kinds(tmp_path):
    log = EventLog(tmp_path / 'e.sqlite')
    base = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    assert log.last('inbox', ('system.run_ok',)) is None
    assert log.last('inbox', ()) is None
    log.record('inbox', 'system.run_failed', base, {'error': 'x'})
    log.record('inbox', 'inbox.triaged', base + dt.timedelta(minutes=1))
    log.record('prices', 'system.run_ok', base + dt.timedelta(minutes=2))
    last = log.last('inbox', ('system.run_ok', 'system.run_failed'))
    assert last is not None
    assert last.kind == 'system.run_failed'
    assert last.payload == {'error': 'x'}

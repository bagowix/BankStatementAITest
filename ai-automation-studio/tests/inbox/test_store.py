import datetime as dt

import pytest

from autostudio.inbox.store import InboxStore
from autostudio.inbox.triage import TriageResult
from tests.inbox.helpers import NOW


@pytest.fixture
def store(tmp_path):
    return InboxStore(tmp_path / 'deep' / 'inbox.sqlite')


def test__lock__second_acquire__refused_until_released_or_expired(store):
    ttl = dt.timedelta(minutes=30)
    assert store.acquire_lock(NOW, ttl)
    assert not store.acquire_lock(NOW + dt.timedelta(minutes=5), ttl)
    assert store.acquire_lock(NOW + dt.timedelta(minutes=31), ttl)
    store.release_lock()
    assert store.acquire_lock(NOW, ttl)


def test__cursor__per_uidvalidity_and_only_forward(store):
    assert store.cursor('INBOX', 1) is None
    store.set_cursor('INBOX', 1, 10)
    store.set_cursor('INBOX', 1, 5)
    assert store.cursor('INBOX', 1) == 10
    assert store.cursor('INBOX', 2) is None


def test__failures__counted_and_cleared_by_mark_uid(store):
    assert store.record_failure('INBOX', 1, 7, 'boom') == 1
    assert store.record_failure('INBOX', 1, 7, 'boom') == 2
    store.mark_uid('INBOX', 1, 7, '<m@x>', NOW)
    assert store.uid_done('INBOX', 1, 7)
    assert store.record_failure('INBOX', 1, 7, 'again') == 1


def test__message_lifecycle__triage_actions_finish_rows_purge(store):
    triage = TriageResult(category='lead', confidence=0.9, summary='s', phone='+1 555', urgency='high')
    store.save_triage('<m@x>', triage, NOW)
    store.add_action('<m@x>', 'draft', NOW)
    store.add_action('<m@x>', 'notify', NOW)
    stored = store.get('<m@x>')
    assert stored is not None and stored.status == 'triaged'
    assert stored.actions == frozenset({'draft', 'notify'}) and stored.triage == triage

    store.finish('<m@x>', status='done', category='lead', urgency='high', sheet_row=['a', 'b'], now=NOW)
    done = store.get('<m@x>')
    assert done is not None and done.status == 'done' and done.triage is None
    assert store.pending_rows() == [('<m@x>', ['a', 'b'])]
    assert store.purge(NOW + dt.timedelta(days=1)) == 0
    store.rows_written(['<m@x>'])
    assert store.pending_rows() == []
    assert store.purge(NOW + dt.timedelta(days=1)) == 1
    assert store.get('<m@x>') is None
    assert store.get('<unknown@x>') is None


def test__add_action__unknown_message__no_crash(store):
    store.add_action('<ghost@x>', 'draft', NOW)
    assert store.get('<ghost@x>') is None


def test__naive_time__value_error(store):
    with pytest.raises(ValueError):
        store.mark_uid('INBOX', 1, 1, None, NOW.replace(tzinfo=None))

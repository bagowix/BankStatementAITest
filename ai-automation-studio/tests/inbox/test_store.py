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
    assert store.record_failure('INBOX', 1, 7, 'boom', now=NOW) == 1
    assert store.record_failure('INBOX', 1, 7, 'boom', now=NOW) == 2
    store.mark_uid('INBOX', 1, 7, '<m@x>', NOW)
    assert store.uid_done('INBOX', 1, 7)
    assert store.record_failure('INBOX', 1, 7, 'again', now=NOW) == 1


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


def test__open__database_from_first_schema__columns_added_and_usable(tmp_path):
    import sqlite3

    path = tmp_path / 'old.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript(
            'CREATE TABLE messages (message_id TEXT PRIMARY KEY, status TEXT NOT NULL, category TEXT, urgency TEXT, '
            "source TEXT, actions TEXT NOT NULL DEFAULT '', triage TEXT, sheet_row TEXT, created_at TEXT NOT NULL, "
            'updated_at TEXT NOT NULL);'
            'CREATE TABLE failures (folder TEXT NOT NULL, uidvalidity INTEGER NOT NULL, uid INTEGER NOT NULL, '
            'attempts INTEGER NOT NULL, last_error TEXT NOT NULL, PRIMARY KEY (folder, uidvalidity, uid));'
            'CREATE TABLE locks (name TEXT PRIMARY KEY, expires_at TEXT NOT NULL);'
        )
    conn.close()
    store = InboxStore(path)
    assert store.record_failure('INBOX', 1, 3, 'x', now=NOW, message_id='<m@x>') == 1
    assert store.failure_message_id('INBOX', 1, 3) == '<m@x>'
    token = store.acquire_lock(NOW, dt.timedelta(minutes=5))
    assert token is not None
    store.release_lock(token)


def test__purge__stale_triage_and_failures__erased(store):
    triage = TriageResult(category='lead', confidence=0.9, summary='s', phone='+995 599 12 34 56')
    store.save_triage('<stuck@x>', triage, NOW)
    store.record_failure('INBOX', 1, 9, 'NotifyError: down', now=NOW, message_id='<stuck@x>', count=False)
    assert store.failed_uids('INBOX', 1) == {9}
    assert store.purge(NOW + dt.timedelta(days=1)) == 1
    assert store.get('<stuck@x>') is None and store.failed_uids('INBOX', 1) == set()


def test__record_failure__postponed__not_counted_but_listed(store):
    assert store.record_failure('INBOX', 1, 4, 'down', now=NOW, count=False) == 0
    assert store.record_failure('INBOX', 1, 4, 'down', now=NOW) == 1
    assert store.failed_uids('INBOX', 1) == {4} and store.failure_message_id('INBOX', 1, 5) is None


def test__health__model_last_ok_remembered(store):
    assert store.healthy_at('model') is None
    store.mark_healthy('model', NOW)
    assert store.healthy_at('model') == NOW

"""Хранилище состояния: словарь и Redis должны вести себя одинаково.

Redis проверяется через fakeredis — это настоящая логика клиента (сериализация,
индекс, срок жизни), только без сети. Интеграция с живым сервером остаётся за
docker-compose из README.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis
import pytest

from bankstatement.models import (
    Account,
    ExtractionResult,
    Guarantee,
    Reconciliation,
    ReconStatus,
    StatementPeriod,
    Summary,
)
from bankstatement.worker.store import JobRecord, JobState, MemoryStore, RedisStore


@pytest.fixture(params=['memory', 'redis'])
def store(request):
    if request.param == 'memory':
        return MemoryStore()

    return RedisStore(fakeredis.FakeStrictRedis(), throttle=0.0)


def make_job(job_id: str = 'a1', digest: str = 'd1', path: str = '/tmp/x.pdf', **kwargs) -> JobRecord:
    return JobRecord(id=job_id, name='выписка.pdf', path=path, digest=digest, **kwargs)


def test__put_get__round_trip__state_survives(store):
    store.put(make_job())

    found = store.get('a1')

    assert found is not None
    assert (found.name, found.digest) == ('выписка.pdf', 'd1')


def test__get__unknown_id__none(store):
    assert store.get('нет такого') is None


def test__find_by_digest__same_content__returns_the_first_job(store):
    store.put(make_job())

    assert store.find_by_digest('d1') is not None
    assert store.find_by_digest('другой') is None


def test__recent__newest_first(store):
    old = make_job('старый', 'd1', started=datetime.now(UTC) - timedelta(minutes=5))
    fresh = make_job('свежий', 'd2')
    store.put(old)
    store.put(fresh)

    assert [j.id for j in store.recent()] == ['свежий', 'старый']


def test__put_get__result_with_money__decimals_are_not_lost(store):
    """Деньги обязаны пережить сеть: float здесь означал бы копейки мимо."""
    period = StatementPeriod(
        account=Account(),
        summary=Summary(beginning_balance=Decimal('2014882.47')),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.CHAIN,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
    )
    store.put(make_job(result=ExtractionResult(periods=[period], source_file='x.pdf')))

    found = store.get('a1')

    assert found is not None
    assert found.result is not None
    assert found.result.periods[0].summary.beginning_balance == Decimal('2014882.47')


def test__evict__over_the_limit__index_is_trimmed(store):
    for index in range(6):
        store.put(make_job(f'j{index}', f'd{index}', state=JobState.DONE))

    store.evict(keep=3)

    assert len(store.recent()) <= 3


def test__evict__uploaded_file__removed_by_every_store(store, tmp_path):
    """Запись уходит по сроку жизни ключа, а файл на диске — нет: его нужно убрать явно."""
    doomed = tmp_path / 'старый.pdf'
    doomed.write_bytes(b'%PDF-1.7')
    kept = tmp_path / 'свежий.pdf'
    kept.write_bytes(b'%PDF-1.7')

    started = datetime.now(UTC) - timedelta(minutes=5)
    store.put(make_job('старый', 'd1', str(doomed), uploaded=True, started=started, state=JobState.DONE))
    store.put(make_job('свежий', 'd2', str(kept), uploaded=True, state=JobState.DONE))

    store.evict(keep=1)

    assert not doomed.exists()
    assert kept.exists()


def test__redis_store__progress_updates__throttled():
    """Гнать сеть на каждую из девяноста девяти страниц незачем."""
    store = RedisStore(fakeredis.FakeStrictRedis(), throttle=60.0)
    job = make_job()
    store.put(job)

    job.pages_done = 7
    store.put(job, force=False)

    stored = store.get('a1')
    assert stored is not None
    assert stored.pages_done == 0  # придержано порогом

    store.put(job, force=True)
    stored = store.get('a1')
    assert stored is not None
    assert stored.pages_done == 7


def test__counters__survive_a_new_store_instance(store):
    """Счётчики метрик переживают рестарт процесса — иначе прод показывает нули."""
    store.incr('submitted')
    store.incr('submitted')
    store.incr('reused')

    assert store.counters()['submitted'] == 2
    assert store.counters()['reused'] == 1


def test__counters__redis_backed__shared_between_processes():
    """Два экземпляра поверх одного Redis видят одни и те же счётчики."""
    shared = fakeredis.FakeStrictRedis()
    first = RedisStore(shared, throttle=0.0)
    second = RedisStore(shared, throttle=0.0)

    first.incr('submitted')

    assert second.counters()['submitted'] == 1

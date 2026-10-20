import datetime as dt
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal

import msgspec
import pytest

from autostudio.errors import ConfigError
from autostudio.office.ledger import Receipt
from autostudio.office.rates import Rate
from autostudio.office.store import OfficeStore, format_number
from tests.office.helpers import make_draft

_BASE = Receipt(
    received_on=dt.date(2026, 10, 14),
    amount=Decimal('600.00'),
    currency='USD',
    rate=Decimal('2.7123'),
    rate_source='manual',
    channel='bank',
    counterparty='Example Corp',
    client_country='US',
)


def _receipt(**changes: object) -> Receipt:
    return msgspec.structs.replace(_BASE, **changes)


def test__issue__same_year__sequential_numbers(store, now):
    numbers = [store.issue_invoice(make_draft(), prefix='AS-', now=now).number for _ in range(3)]
    assert numbers == ['AS-2026-001', 'AS-2026-002', 'AS-2026-003']


def test__issue__new_year__numbering_restarts_per_year(store, now):
    store.issue_invoice(make_draft(issue_date=dt.date(2026, 12, 31)), prefix='AS-', now=now)
    store.issue_invoice(make_draft(issue_date=dt.date(2026, 12, 31)), prefix='AS-', now=now)
    first_2027 = store.issue_invoice(make_draft(issue_date=dt.date(2027, 1, 2)), prefix='AS-', now=now)
    late_2026 = store.issue_invoice(make_draft(issue_date=dt.date(2026, 12, 31)), prefix='AS-', now=now)
    assert first_2027.number == 'AS-2027-001'
    assert late_2026.number == 'AS-2026-003'


def test__void__number_stays_and_is_never_reused(store, now):
    first = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    voided = store.void_invoice(first.number, reason='wrong client', now=now)
    assert voided.status == 'void'
    assert voided.void_reason == 'wrong client'
    following = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    assert following.number == 'AS-2026-002'
    registry = store.list_invoices(2026)
    assert [(r.invoice.number, r.status) for r in registry] == [('AS-2026-001', 'void'), ('AS-2026-002', 'issued')]


def test__invoices__cannot_be_deleted_or_counter_rewound(store, now):
    store.issue_invoice(make_draft(), prefix='AS-', now=now)
    with closing(sqlite3.connect(store.path)) as conn, pytest.raises(sqlite3.IntegrityError, match='never deleted'):
        conn.execute("DELETE FROM invoices WHERE number = 'AS-2026-001'")
    with closing(sqlite3.connect(store.path)) as conn, pytest.raises(sqlite3.IntegrityError, match='never reused'):
        conn.execute('UPDATE invoice_counters SET last_seq = 0')


def test__issue__counter_row_lost__still_no_reuse(store, now):
    store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.issue_invoice(make_draft(), prefix='AS-', now=now)
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute('DELETE FROM invoice_counters')
    assert store.issue_invoice(make_draft(), prefix='AS-', now=now).number == 'AS-2026-003'


def test__issue__invalid_draft__number_not_consumed(store, now):
    with pytest.raises(ConfigError):
        store.issue_invoice(make_draft(price='0'), prefix='AS-', now=now)
    assert store.issue_invoice(make_draft(), prefix='AS-', now=now).number == 'AS-2026-001'


def test__issue__concurrent_processes__unique_gapless_numbers(store, now):
    # Каждый поток — своё соединение, как две консоли оператора одновременно.
    def issue(index: int) -> str:
        return OfficeStore(store.path).issue_invoice(make_draft(name=f'Client {index}'), prefix='AS-', now=now).number

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(issue, range(40)))
    assert len(set(numbers)) == 40
    assert sorted(numbers) == [format_number('AS-', 2026, seq) for seq in range(1, 41)]


def test__format_number__beyond_999__longer_not_wrapped():
    assert format_number('AS-', 2026, 1000) == 'AS-2026-1000'


def test__void__twice_paid_unknown_or_no_reason__refused(store, now):
    invoice = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    with pytest.raises(ConfigError, match='причину'):
        store.void_invoice(invoice.number, reason=' ', now=now)
    with pytest.raises(ConfigError, match='нет в реестре'):
        store.void_invoice('AS-2026-999', reason='x', now=now)
    store.void_invoice(invoice.number, reason='typo', now=now)
    with pytest.raises(ConfigError, match='уже аннулирован'):
        store.void_invoice(invoice.number, reason='again', now=now)
    with pytest.raises(ConfigError, match='аннулирован'):
        store.pay_invoice(invoice.number, _receipt(), now=now)
    paid = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.pay_invoice(paid.number, _receipt(), now=now)
    with pytest.raises(ConfigError, match='оплачен'):
        store.void_invoice(paid.number, reason='x', now=now)
    with pytest.raises(ConfigError, match='уже отмечен'):
        store.pay_invoice(paid.number, _receipt(), now=now)


def test__pay__marks_paid_and_records_linked_receipt(store, now):
    invoice = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    record, receipt = store.pay_invoice(invoice.number, _receipt(platform_fee=Decimal('5.50')), now=now)
    assert record.status == 'paid'
    assert record.paid_on == dt.date(2026, 10, 14)
    assert receipt.id is not None
    assert receipt.invoice_number == invoice.number
    [stored] = store.receipts()
    assert stored == receipt
    assert stored.platform_fee == Decimal('5.50')
    assert isinstance(stored.amount, Decimal) and isinstance(stored.rate, Decimal)


def test__get_invoice__unknown__config_error(store):
    with pytest.raises(ConfigError, match='нет в реестре'):
        store.get_invoice('AS-2026-001')


def test__add_receipt__invoice_checks_and_until_filter(store, now):
    with pytest.raises(ConfigError, match='нет в реестре'):
        store.add_receipt(_receipt(invoice_number='AS-2026-404'), now=now)
    voided = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.void_invoice(voided.number, reason='x', now=now)
    with pytest.raises(ConfigError, match='аннулирован'):
        store.add_receipt(_receipt(invoice_number=voided.number), now=now)
    store.add_receipt(_receipt(received_on=dt.date(2026, 9, 30), channel='wise', rate_source='nbg'), now=now)
    store.add_receipt(_receipt(received_on=dt.date(2026, 10, 1), currency='GEL', rate=Decimal(1)), now=now)
    assert [r.received_on for r in store.receipts(until=dt.date(2026, 9, 30))] == [dt.date(2026, 9, 30)]
    assert [r.channel for r in store.receipts()] == ['wise', 'bank']
    assert [r.rate_source for r in store.receipts()] == ['nbg', 'manual']


def test__rate_cache__roundtrip_and_overwrite(store):
    rate = Rate(
        currency='JPY',
        on_date=dt.date(2026, 10, 14),
        gel_per_unit=Decimal('0.018234'),
        quantity=Decimal(100),
        published_rate=Decimal('1.8234'),
        source_date=dt.date(2026, 10, 14),
    )
    assert store.get_rate('JPY', dt.date(2026, 10, 14)) is None
    store.put_rate(rate)
    assert store.get_rate('JPY', dt.date(2026, 10, 14)) == rate
    store.put_rate(rate)
    assert store.get_rate('JPY', dt.date(2026, 10, 15)) is None

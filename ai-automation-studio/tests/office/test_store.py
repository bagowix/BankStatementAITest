import datetime as dt
import re
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
from tests.office.helpers import make_draft, make_issuer

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


def test__issue__new_year__numbering_restarts_per_year(store):
    now = dt.datetime(2027, 1, 2, 9, 0, tzinfo=dt.UTC)
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


def test__issue__backdated_in_series__refused_unless_allowed(store, now):
    store.issue_invoice(make_draft(issue_date=dt.date(2026, 10, 1)), prefix='AS-', now=now)
    with pytest.raises(ConfigError, match='раньше последнего счёта серии'):
        store.issue_invoice(make_draft(issue_date=dt.date(2026, 3, 1)), prefix='AS-', now=now)
    # Номер при отказе не израсходован; осознанный выпуск задним числом — с allow_date.
    late = store.issue_invoice(make_draft(issue_date=dt.date(2026, 3, 1)), prefix='AS-', now=now, allow_date=True)
    assert late.number == 'AS-2026-002'
    same_day = store.issue_invoice(make_draft(issue_date=dt.date(2026, 10, 1)), prefix='AS-', now=now)
    assert same_day.number == 'AS-2026-003'


@pytest.mark.parametrize(
    ('issue_date', 'message'),
    [
        (dt.date(2026, 10, 28), 'в будущем'),
        (dt.date(2027, 1, 5), 'в будущем'),
        (dt.date(2024, 12, 31), 'прошлого года'),
    ],
)
def test__issue__implausible_date__refused_without_consuming_number(store, now, issue_date, message):
    with pytest.raises(ConfigError, match=message):
        store.issue_invoice(make_draft(issue_date=issue_date), prefix='AS-', now=now)
    assert store.list_invoices() == []
    allowed = store.issue_invoice(make_draft(issue_date=issue_date), prefix='AS-', now=now, allow_date=True)
    assert allowed.number.endswith('-001')


def test__issue__a_week_ahead_and_last_year__accepted(store, now):
    assert (
        store.issue_invoice(make_draft(issue_date=dt.date(2025, 1, 2)), prefix='AS-', now=now).number == 'AS-2025-001'
    )
    assert store.issue_invoice(make_draft(issue_date=dt.date(2026, 10, 27)), prefix='AS-', now=now).number == (
        'AS-2026-001'
    )


def test__issue__snapshot_of_issuer__stored_with_invoice(store, now):
    issued = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    stored = store.get_invoice(issued.number).invoice
    assert stored.issuer == make_issuer()


def test__store__missing_database__refused_with_absolute_path(tmp_path):
    missing = OfficeStore(tmp_path / 'nowhere' / 'office.sqlite')
    with pytest.raises(ConfigError, match='office init') as caught:
        missing.list_invoices()
    assert str(tmp_path / 'nowhere' / 'office.sqlite') in str(caught.value)
    assert not (tmp_path / 'nowhere').exists()
    assert missing.init() is True
    assert missing.init() is False
    assert missing.list_invoices() == []


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


def test__pay__receipt_already_in_ledger__no_second_receipt(store, now):
    invoice = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.add_receipt(_receipt(invoice_number=invoice.number, received_on=dt.date(2026, 10, 5)), now=now)
    # Полная сумма второй записью удвоила бы оборот — отказ без top_up.
    with pytest.raises(ConfigError, match='удвоит оборот'):
        store.pay_invoice(invoice.number, _receipt(), now=now)
    assert len(store.receipts()) == 1
    record = store.mark_paid(invoice.number)
    assert record.status == 'paid' and record.paid_on == dt.date(2026, 10, 5)
    assert len(store.receipts_for_invoice(invoice.number)) == 1


def test__pay__top_up_and_mark_paid_without_receipts(store, now):
    invoice = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    with pytest.raises(ConfigError, match='нет поступлений'):
        store.mark_paid(invoice.number)
    store.add_receipt(_receipt(invoice_number=invoice.number, amount=Decimal('300.00')), now=now)
    second = _receipt(amount=Decimal('300.00'), received_on=dt.date(2026, 10, 15))
    record, saved = store.pay_invoice(invoice.number, second, now=now, top_up=True)
    assert record.status == 'paid' and saved.invoice_number == invoice.number
    assert [r.amount for r in store.receipts_for_invoice(invoice.number)] == [Decimal('300.00')] * 2
    with pytest.raises(ConfigError, match='уже отмечен'):
        store.mark_paid(invoice.number, paid_on=dt.date(2026, 10, 15))


def test__add_receipt__paid_invoice_same_payment_again__refused_without_extra(store, now):
    # invoice paid уже записал поступление; та же проводка через ledger add --invoice удвоила бы оборот.
    invoice = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.pay_invoice(invoice.number, _receipt(), now=now)
    with pytest.raises(ConfigError, match=re.escape('#1: 2026-10-14 600.00 USD — та же сумма и дата')):
        store.add_receipt(_receipt(invoice_number=invoice.number), now=now)
    # Другая дата не спасает: в валюте счёта получено бы больше суммы счёта.
    with pytest.raises(
        ConfigError, match=re.escape('уже получено 600.00 из 600.00 USD, с этой записью было бы 1200.00')
    ):
        store.add_receipt(_receipt(invoice_number=invoice.number, received_on=dt.date(2026, 10, 15)), now=now)
    assert len(store.receipts()) == 1
    # Клиент правда заплатил дважды — оператор подтверждает явно, деньги идут в оборот.
    store.add_receipt(_receipt(invoice_number=invoice.number), now=now, extra=True)
    assert [r.amount for r in store.receipts_for_invoice(invoice.number)] == [Decimal('600.00')] * 2


def test__add_receipt__remainder_of_partially_paid_invoice__accepted(store, now):
    # Первая часть через invoice paid --amount, остаток через ledger add --invoice — это не повтор.
    invoice = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.pay_invoice(invoice.number, _receipt(amount=Decimal('250.00'), received_on=dt.date(2026, 10, 10)), now=now)
    store.add_receipt(_receipt(invoice_number=invoice.number, amount=Decimal('350.00')), now=now)
    assert sum(r.amount for r in store.receipts_for_invoice(invoice.number)) == Decimal('600.00')
    with pytest.raises(ConfigError, match='больше суммы счёта'):
        store.add_receipt(_receipt(invoice_number=invoice.number, amount=Decimal('0.01')), now=now)


def test__add_receipt__issued_invoice_recorded_twice__refused_before_paid(store, now):
    # Повтор ловится и до invoice paid: две записи ledger add --invoice на всю сумму.
    invoice = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.add_receipt(_receipt(invoice_number=invoice.number, received_on=dt.date(2026, 10, 10)), now=now)
    with pytest.raises(ConfigError, match='больше суммы счёта'):
        store.add_receipt(_receipt(invoice_number=invoice.number), now=now)
    # Остаток в другой валюте у невыплаченного счёта сверить нельзя — запись принимается.
    partial = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.add_receipt(_receipt(invoice_number=partial.number, amount=Decimal('100.00')), now=now)
    lari = _receipt(invoice_number=partial.number, amount=Decimal('1350.00'), currency='GEL', rate=Decimal(1))
    store.add_receipt(lari, now=now)
    assert len(store.receipts_for_invoice(partial.number)) == 2


def test__add_receipt__other_currency_after_full_or_paid__refused_without_extra(store, now):
    # Вся сумма уже пришла в долларах — лари после конвертации банком та же оплата.
    full = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.add_receipt(_receipt(invoice_number=full.number), now=now)
    lari = _receipt(amount=Decimal('1627.38'), currency='GEL', rate=Decimal(1), rate_source='gel')
    with pytest.raises(ConfigError, match='та же оплата после конвертации'):
        store.add_receipt(msgspec.structs.replace(lari, invoice_number=full.number), now=now)
    # Оплаченный счёт с частью в лари: сверить без курса нельзя — только явно.
    mixed = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.pay_invoice(mixed.number, msgspec.structs.replace(lari, amount=Decimal('500.00')), now=now)
    remainder = _receipt(invoice_number=mixed.number, amount=Decimal('400.00'))
    with pytest.raises(ConfigError, match='уже оплачен, а часть поступлений по нему не в USD'):
        store.add_receipt(remainder, now=now)
    store.add_receipt(remainder, now=now, extra=True)
    assert len(store.receipts_for_invoice(mixed.number)) == 2


def test__pay__top_up_beyond_invoice_total__refused_without_extra(store, now):
    # ledger add записал всю сумму, затем invoice paid --amount на неё же — та же оплата второй раз.
    invoice = store.issue_invoice(make_draft(), prefix='AS-', now=now)
    store.add_receipt(_receipt(invoice_number=invoice.number, received_on=dt.date(2026, 10, 10)), now=now)
    with pytest.raises(ConfigError, match='больше суммы счёта'):
        store.pay_invoice(invoice.number, _receipt(), now=now, top_up=True)
    assert store.get_invoice(invoice.number).status == 'issued'
    record, _ = store.pay_invoice(invoice.number, _receipt(), now=now, top_up=True, extra=True)
    assert record.status == 'paid' and len(store.receipts_for_invoice(invoice.number)) == 2


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

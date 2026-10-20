import csv
import datetime as dt
import io
from decimal import Decimal

import pytest

from autostudio.office.ledger import (
    CSV_HEADER,
    Receipt,
    build_month_report,
    declaration_deadline,
    month_end,
    peak_vat_window,
    receipts_csv,
    render_month_report,
    tax_estimate,
    twelve_month_start,
)


def _r(
    day: str,
    amount: str,
    currency: str = 'USD',
    rate: str = '2.70',
    country: str = 'US',
    platform_fee: Decimal | None = None,
) -> Receipt:
    return Receipt(
        received_on=dt.date.fromisoformat(day),
        amount=Decimal(amount),
        currency=currency,
        rate=Decimal(rate),
        rate_source='gel' if currency == 'GEL' else 'nbg',
        channel='bank',
        counterparty='Example',
        client_country=country,
        platform_fee=platform_fee,
    )


# Книга оператора: прошлый год, начало года и октябрь 2026.
LEDGER = [
    _r('2025-10-03', '20000', 'GEL', '1', 'GE'),  # в окне на 1 октября ещё есть, на 20 октября — уже нет
    _r('2025-12-20', '1000'),  # прошлый год: не в YTD
    _r('2026-01-15', '1000', rate='2.6871'),  # 2687.10
    _r('2026-03-02', '30000', 'GEL', '1', 'GE'),
    _r('2026-10-05', '1000.00', rate='2.7123', platform_fee=Decimal('100')),  # 2712.30, Upwork до комиссии
    _r('2026-10-14', '450', 'EUR', '2.9555', 'DE'),  # 1329.975 → 1329.98
    _r('2026-10-20', '33333.33', 'GEL', '1', 'GE'),
    _r('2026-11-01', '999', 'GEL', '1', 'GE'),  # следующий месяц: в октябрь не входит
]


def test__gel_amount__per_receipt__rounded_half_up_to_tetri():
    assert _r('2026-10-14', '450', 'EUR', '2.9555').gel_amount == Decimal('1329.98')
    assert _r('2026-10-14', '0.5', 'USD', '0.01').gel_amount == Decimal('0.01')  # 0.005 → 0.01


def test__month_report__fixture_ledger__numbers():
    report = build_month_report(LEDGER, dt.date(2026, 10, 1))
    assert [r.received_on.day for r in report.receipts] == [5, 14, 20]
    assert report.turnover_gel == Decimal('2712.30') + Decimal('1329.98') + Decimal('33333.33')
    assert report.turnover_gel == Decimal('37375.61')
    assert report.tax_estimate_gel == Decimal('373.76')  # 373.7561 → 373.76
    assert report.ytd_turnover_gel == Decimal('2687.10') + Decimal('30000') + Decimal('37375.61')
    assert report.ytd_tax_estimate_gel == tax_estimate(report.ytd_turnover_gel)
    assert report.deadline == dt.date(2026, 11, 15)
    assert report.fees == {'USD': Decimal('100')}
    assert all(isinstance(value, Decimal) for value in (report.turnover_gel, report.tax_estimate_gel))


def test__tax_estimate__half_up_to_tetri():
    assert tax_estimate(Decimal('12.50')) == Decimal('0.13')  # 0.125 → 0.13
    assert tax_estimate(Decimal('0')) == Decimal('0.00')


def test__deadline__december__fifteenth_of_january():
    assert declaration_deadline(dt.date(2026, 12, 31)) == dt.date(2027, 1, 15)
    assert month_end(dt.date(2028, 2, 10)) == dt.date(2028, 2, 29)


def test__twelve_month_window__inclusive_bounds():
    assert twelve_month_start(dt.date(2026, 10, 5)) == dt.date(2025, 10, 6)
    assert twelve_month_start(dt.date(2026, 2, 28)) == dt.date(2025, 3, 1)


def test__vat_window__georgian_clients_only__peak_inside_month():
    window = peak_vat_window(LEDGER, dt.date(2026, 10, 1))
    # 30 000 (март) + 33 333.33 (20 октября); 20 000 от 2025-10-03 уже вне окна, иностранные не считаются.
    assert window.georgian_turnover_gel == Decimal('63333.33')
    assert (window.start, window.end) == (dt.date(2025, 10, 21), dt.date(2026, 10, 20))


def test__vat_window__mid_month_peak_beats_month_end():
    # Окно, кончающееся 5 октября, захватывает 6 октября прошлого года; окно на конец месяца — уже нет.
    receipts = [_r('2025-10-06', '60000', 'GEL', '1', 'GE'), _r('2026-10-05', '45000', 'GEL', '1', 'GE')]
    window = peak_vat_window(receipts, dt.date(2026, 10, 1))
    assert window.georgian_turnover_gel == Decimal(105000)
    assert window.end == dt.date(2026, 10, 5)
    month_end_total = sum(
        (r.gel_amount for r in receipts if twelve_month_start(dt.date(2026, 10, 31)) <= r.received_on), Decimal(0)
    )
    assert month_end_total == Decimal(45000)


def test__vat_window__no_receipts_in_month__window_from_first_day():
    window = peak_vat_window([_r('2026-02-01', '5000', 'GEL', '1', 'GE')], dt.date(2026, 10, 1))
    assert window.end == dt.date(2026, 10, 1)
    assert window.georgian_turnover_gel == Decimal(5000)


@pytest.mark.parametrize(
    ('georgian', 'expected'),
    [('79999.99', None), ('80000', 'Порог НДС близко'), ('100000', 'Порог НДС близко'), ('100000.01', 'ПОРОГ НДС')],
)
def test__vat_warning__at_80_percent_and_over_threshold(georgian, expected):
    report = build_month_report([_r('2026-10-10', georgian, 'GEL', '1', 'GE')], dt.date(2026, 10, 1))
    warnings = report.warnings()
    if expected is None:
        assert not any('НДС' in w for w in warnings)
    else:
        assert any(expected in w for w in warnings)


def test__vat_warning__foreign_clients__do_not_count():
    report = build_month_report([_r('2026-10-10', '150000', 'GEL', '1', 'US')], dt.date(2026, 10, 1))
    assert report.vat_window.georgian_turnover_gel == 0
    assert not any('НДС' in w for w in report.warnings())


def test__ceiling__near_and_over_500k__warnings():
    near = build_month_report([_r('2026-10-10', '400000', 'GEL', '1', 'US')], dt.date(2026, 10, 1))
    assert any('80.0% от потолка' in w for w in near.warnings())
    over = build_month_report([_r('2026-10-10', '500000.01', 'GEL', '1', 'US')], dt.date(2026, 10, 1))
    assert any('ПОТОЛОК РЕЖИМА ПРЕВЫШЕН' in w for w in over.warnings())


def test__render_month_report__key_lines_and_disclaimer():
    text = render_month_report(build_month_report(LEDGER, dt.date(2026, 10, 1)))
    assert 'Поступления и налог за 2026-10' in text
    assert 'не налоговая консультация' in text
    assert 'Оборот месяца: **37 375.61 GEL**' in text
    assert 'Налог 1% (оценка): **373.76 GEL**' in text
    assert 'до 2026-11-15' in text
    assert '100 USD' in text
    assert 'Порог НДС: 63 333.33 GEL' in text


def test__render_month_report__empty_month__zero_declaration_reminder():
    text = render_month_report(build_month_report([], dt.date(2026, 8, 1)))
    assert 'Нулевую декларацию' in text
    assert 'Оборот месяца: **0.00 GEL**' in text


def test__render_month_report__manual_rate_and_hostile_name__flagged_and_escaped():
    receipt = Receipt(
        received_on=dt.date(2026, 10, 3),
        amount=Decimal('10'),
        currency='USD',
        rate=Decimal('2.7'),
        rate_source='manual',
        channel='paypal',
        counterparty='<img src=x> | Co',
        client_country='US',
    )
    text = render_month_report(build_month_report([receipt], dt.date(2026, 10, 1)))
    assert '(вручную)' in text and 'сверить' in text
    assert '<img' not in text and '\\| Co' in text


def test__receipts_csv__header_values_and_formula_neutralized():
    receipt = Receipt(
        received_on=dt.date(2026, 10, 3),
        amount=Decimal('1000.00'),
        currency='USD',
        rate=Decimal('2.7123'),
        rate_source='nbg',
        channel='upwork',
        counterparty='=HYPERLINK("http://evil")',
        client_country='US',
        invoice_number='AS-2026-001',
        platform_fee=Decimal('100.00'),
        note='+cmd',
    )
    rows = list(csv.reader(io.StringIO(receipts_csv([receipt]))))
    assert tuple(rows[0]) == CSV_HEADER
    assert rows[1][:7] == ['2026-10-03', '1000.00', 'USD', '2.7123', 'nbg', '2712.30', 'upwork']
    assert rows[1][7] == '\'=HYPERLINK("http://evil")'
    assert rows[1][9:] == ['AS-2026-001', '100.00', "'+cmd"]

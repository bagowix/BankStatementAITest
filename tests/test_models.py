from datetime import date
from decimal import Decimal

from bankstatement.models import (
    Account,
    ExtractionResult,
    Guarantee,
    Losses,
    Opinion,
    Period,
    Reconciliation,
    ReconStatus,
    Source,
    StatementPeriod,
    Summary,
    Transaction,
    UnplacedRow,
    money_out,
)


def test__money_out__quantised_to_cents():
    assert money_out(Decimal('1.005')) == 1.0
    assert money_out(Decimal('1234.5')) == 1234.5
    assert money_out(None) is None


def test__transaction__signed_amount__deposit_positive_withdrawal_negative():
    deposit = Transaction(date=date(2025, 4, 1), description='X', deposit=Decimal('10.00'))
    withdrawal = Transaction(date=date(2025, 4, 1), description='X', withdrawal=Decimal('10.00'))

    assert deposit.signed_amount == Decimal('10.00')
    assert withdrawal.signed_amount == Decimal('-10.00')


def test__summary__self_check__none_when_incomplete():
    assert Summary(beginning_balance=Decimal('1.00')).self_check() is None


def test__extraction_result__payload__flat_contract_plus_periods():
    """Плоские поля повторяют первый период — контракт задания выполняется буквально."""
    period = StatementPeriod(
        account=Account(bank='B', account_last4='1234', period=Period(start=date(2025, 4, 1), end=date(2025, 4, 30))),
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('100.00')),
        transactions=[
            Transaction(
                date=date(2025, 4, 1),
                description='X',
                deposit=Decimal('10.00'),
                balance=Decimal('110.00'),
                source=Source(page=1, engine='test'),
            )
        ],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.CHAIN,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('10.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
        pages=[1],
    )
    payload = ExtractionResult(periods=[period], source_file='x.pdf').to_payload()

    assert payload['account']['account_last4'] == '1234'
    assert payload['transactions'] == payload['periods'][0]['transactions']
    assert payload['meta']['periods_reconciled'] == 1
    assert payload['periods'][0]['reconciliation']['guarantee'] == 'A'


def test__extraction_result__no_periods__payload_still_valid():
    payload = ExtractionResult(periods=[]).to_payload()

    assert payload['account'] is None
    assert payload['transactions'] == []
    assert payload['meta']['periods_total'] == 0


def make_period(delta='0.00', status=ReconStatus.RECONCILED, start=1, transactions=0):
    return StatementPeriod(
        account=Account(period=Period(start=date(2025, start, 1), end=date(2025, start, 28))),
        summary=Summary(),
        transactions=[
            Transaction(date=date(2025, start, 2), description='X', deposit=Decimal('1.00'))
            for _ in range(transactions)
        ],
        reconciliation=Reconciliation(
            status=status,
            guarantee=Guarantee.CHAIN if status is ReconStatus.RECONCILED else Guarantee.NONE,
            delta=Decimal(delta),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
        pages=[start],
    )


def test__source__repaired_and_printed_in_payload():
    """Source.to_payload отдаёт repaired и printed."""
    source = Source(page=1, engine='tesseract', repaired=True, printed='1150.00')

    payload = source.to_payload()

    assert payload['repaired'] is True
    assert payload['printed'] == '1150.00'


def test__source__repaired_defaults_to_false():
    """Source.repaired по умолчанию False."""
    source = Source(page=1)

    assert source.repaired is False
    assert source.printed is None


def test__summary__sources_provenance_in_payload():
    """Summary.sources переходит в payload как словарь поле -> Source."""
    beginning_source = Source(page=1, engine='textlayer')
    ending_source = Source(page=2, engine='tesseract', repaired=True, printed='1200.50')

    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('120.00'),
        sources={'beginning_balance': beginning_source, 'ending_balance': ending_source},
    )

    payload = summary.to_payload()

    assert 'sources' in payload
    assert 'beginning_balance' in payload['sources']
    assert payload['sources']['beginning_balance']['engine'] == 'textlayer'
    assert payload['sources']['ending_balance']['repaired'] is True


def test__unplaced_row_to_payload():
    """UnplacedRow сериализуется корректно."""
    row = UnplacedRow(
        date=date(2025, 4, 15),
        description='Test entry',
        amounts=[Decimal('100.00'), Decimal('50.00')],
        reason='no_balance',
        kind='lost',
        source=Source(page=1, engine='tesseract'),
    )

    payload = row.to_payload()

    assert payload['date'] == '2025-04-15'
    assert payload['description'] == 'Test entry'
    assert payload['amounts'] == [100.0, 50.0]
    assert payload['reason'] == 'no_balance'
    assert payload['kind'] == 'lost'
    assert payload['source']['page'] == 1


def test__unplaced_row_none_date():
    """UnplacedRow с date=None сериализуется."""
    row = UnplacedRow(date=None, description='No date', amounts=[], reason='unreadable')

    payload = row.to_payload()

    assert payload['date'] is None


def test__losses_to_payload():
    """Losses сериализуется с unplaced строками."""
    unplaced = UnplacedRow(
        date=date(2025, 4, 1),
        description='Lost',
        amounts=[Decimal('50.00')],
        reason='no_sign',
        kind='marker',
    )

    losses = Losses(placed=10, repaired=2, unplaced=[unplaced])

    payload = losses.to_payload()

    assert payload['placed'] == 10
    assert payload['repaired'] == 2
    assert payload['proven'] is False
    assert len(payload['unplaced']) == 1
    assert payload['unplaced'][0]['kind'] == 'marker'


def test__statement_period__losses_in_payload():
    """StatementPeriod.losses переходит в payload."""
    period = StatementPeriod(
        account=Account(),
        summary=Summary(),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.CHAIN,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
        losses=Losses(placed=5, repaired=1),
    )

    payload = period.to_payload()

    assert 'losses' in payload
    assert payload['losses']['placed'] == 5


def test__statement_period__losses_omitted_if_empty():
    """StatementPeriod без потерь не добавляет losses в payload."""
    period = StatementPeriod(
        account=Account(),
        summary=Summary(),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.CHAIN,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
    )

    payload = period.to_payload()

    # losses не должно быть в payload если оно пустое (default_factory)
    assert 'losses' not in payload


def test__opinion__every_period_reconciled__clean():
    result = ExtractionResult(periods=[make_period(), make_period(start=2)])

    assert result.opinion is Opinion.CLEAN


def test__opinion__all_reconciled_but_has_lost_rows__not_clean():
    """Если есть потеря kind='lost', opinion не CLEAN даже если все периоды сведены."""
    lost_row = UnplacedRow(
        date=date(2025, 4, 1),
        description='Lost tx',
        amounts=[Decimal('100.00')],
        reason='no_balance',
        kind='lost',
    )
    period = StatementPeriod(
        account=Account(),
        summary=Summary(),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.AGGREGATE,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
        losses=Losses(unplaced=[lost_row]),
    )

    result = ExtractionResult(periods=[period])

    assert result.opinion is Opinion.QUALIFIED


def test__opinion__all_reconciled_only_marker_losses__clean():
    """Потери kind='marker' и kind='duplicate' не блокируют CLEAN."""
    marker_row = UnplacedRow(
        date=date(2025, 3, 31),
        description='ENDING BALANCE FROM PRIOR STATEMENT',
        amounts=[Decimal('1908989.60')],
        reason='no_balance',
        kind='marker',
    )
    duplicate_row = UnplacedRow(
        date=date(2025, 4, 9),
        description='Check 25243',
        amounts=[Decimal('219.36')],
        reason='no_balance',
        kind='duplicate',
    )
    period = StatementPeriod(
        account=Account(),
        summary=Summary(),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.AGGREGATE,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
        losses=Losses(unplaced=[marker_row, duplicate_row]),
    )

    result = ExtractionResult(periods=[period])

    assert result.opinion is Opinion.CLEAN


def test__opinion__some_periods_failed__qualified():
    result = ExtractionResult(periods=[make_period(), make_period('5.00', ReconStatus.FAILED, start=2)])

    assert result.opinion is Opinion.QUALIFIED


def test__opinion__nothing_reconciled__adverse():
    result = ExtractionResult(periods=[make_period('5.00', ReconStatus.FAILED)])

    assert result.opinion is Opinion.ADVERSE


def test__opinion__no_periods_at_all__disclaimer():
    """Пустой результат — не «всё хорошо»: сверять было нечего."""
    assert ExtractionResult(periods=[]).opinion is Opinion.DISCLAIMER


def test__failures__ordered_by_size_not_by_position():
    """Копейка и двенадцать тысяч требуют разного внимания — крупное идёт первым."""
    small = make_period('0.10', ReconStatus.FAILED, start=1)
    big = make_period('-11798.31', ReconStatus.FAILED, start=2)
    middle = make_period('870.93', ReconStatus.FAILED, start=3)

    result = ExtractionResult(periods=[small, big, middle])

    assert [p.reconciliation.delta for p in result.failures] == [
        Decimal('-11798.31'),
        Decimal('870.93'),
        Decimal('0.10'),
    ]


def test__unexplained__sums_absolute_deltas_of_failed_periods_only():
    result = ExtractionResult(
        periods=[
            make_period(),
            make_period('-100.00', ReconStatus.FAILED, start=2),
            make_period('40.00', ReconStatus.FAILED, start=3),
        ]
    )

    assert result.unexplained == Decimal('140.00')


def test__scope__pages_and_transactions_counted_across_periods():
    result = ExtractionResult(
        periods=[make_period(transactions=2), make_period(start=2, transactions=3)],
        engine_stats={'textlayer': 4, 'tesseract': 6},
    )

    assert (result.pages_total, result.transactions_total) == (10, 5)


def test__payload__meta_carries_the_verdict():
    """Машинному потребителю итог нужен так же, как человеку."""
    result = ExtractionResult(periods=[make_period(), make_period('-2.50', ReconStatus.FAILED, start=2)])

    meta = result.to_payload()['meta']

    assert meta['opinion'] == 'qualified'
    assert meta['unexplained'] == 2.5


def test__unexplained__ending_matched_but_totals_off__residual_not_zero():
    """«Нераскрыто» считает полную невязку: нулевая дельта по остатку расхождение не прячет."""
    period = StatementPeriod(
        account=Account(),
        summary=Summary(),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.FAILED,
            guarantee=Guarantee.NONE,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('60.00'),
            computed_withdrawals=Decimal('60.00'),
            delta_deposits=Decimal('10.00'),
            delta_withdrawals=Decimal('10.00'),
        ),
    )

    assert ExtractionResult(periods=[period]).unexplained == Decimal('20.00')


def test__opinion__truncated_result__never_clean():
    period = StatementPeriod(
        account=Account(),
        summary=Summary(),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.CHAIN,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
    )

    assert ExtractionResult(periods=[period]).opinion is Opinion.CLEAN
    assert ExtractionResult(periods=[period], truncated=True).opinion is Opinion.QUALIFIED


def test__pages_total__page_count_known__not_the_sum_over_engines():
    """Страница, перечитанная моделью, учтена у обоих движков, а в документе она одна."""
    result = ExtractionResult(
        periods=[], engine_stats={'tesseract': 10, 'vision-openai/gpt-5.4-mini': 4}, page_count=10
    )

    assert result.pages_total == 10

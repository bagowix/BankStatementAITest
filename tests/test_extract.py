from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bankstatement.extract import _canonical_banks, choose, extract, extract_result
from bankstatement.models import Account, Guarantee, Period, ReconStatus, Summary
from bankstatement.segment import Segment
from tests.conftest import page

SAMPLES = Path('samples')


def make_segment(*texts, summary, period=(date(2025, 4, 1), date(2025, 4, 30))):
    lines = page(*texts)

    return Segment(
        pages=(0,),
        summary=summary,
        account=Account(period=Period(start=period[0], end=period[1])),
        lines=tuple(lines),
    )


def test__choose__reconciled_hypothesis__wins():
    part = make_segment(
        '04/01 DEPOSIT 50.00 150.00',
        '04/02 PAYMENT 30.00 120.00',
        summary=Summary(
            beginning_balance=Decimal('100.00'),
            ending_balance=Decimal('120.00'),
            deposits_total=Decimal('50.00'),
            withdrawals_total=Decimal('30.00'),
        ),
    )
    guess, verdict = choose(part)

    assert guess.name == 'chain'
    assert verdict.status is ReconStatus.RECONCILED
    assert verdict.guarantee is Guarantee.CHAIN


def test__choose__empty_parse__does_not_win_on_trivial_zero():
    """Пустой разбор сходится по остатку сам собой — и не должен побеждать.

    Начальный и конечный остаток равны, поэтому «ничего не нашли» даёт нулевую
    дельту. Полная невязка учитывает и обороты, и потому такой разбор проигрывает.
    """
    part = make_segment(
        'CREDITS',
        '04/01 TRANSFER 32,537.69',
        'OTHER DEBITS',
        '04/02 WITHDRAWAL 32,537.69',
        summary=Summary(
            beginning_balance=Decimal('-10.00'),
            ending_balance=Decimal('-10.00'),
            deposits_total=Decimal('32537.69'),
            withdrawals_total=Decimal('32537.69'),
        ),
    )
    guess, verdict = choose(part)

    assert guess.name == 'sections'
    assert verdict.status is ReconStatus.RECONCILED
    assert verdict.guarantee is Guarantee.AGGREGATE


def test__choose__empty_reading_closer_by_residual__non_empty_still_wins():
    """Когда напечатаны только остатки, пустое прочтение сходится на их разность даром."""
    part = make_segment(
        '04/01 DEPOSIT 50.00 150.00',
        '04/02 UNKNOWN 999.00 1149.00',
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00')),
    )
    guess, verdict = choose(part)

    assert guess.transactions
    assert verdict.status is ReconStatus.FAILED


def test__choose__nothing_reconciles__closest_returned_and_marked_failed():
    part = make_segment(
        '04/01 DEPOSIT 50.00 150.00',
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('999.00')),
    )
    _, verdict = choose(part)

    assert verdict.status is ReconStatus.FAILED
    assert verdict.diagnoses


@pytest.mark.samples
@pytest.mark.parametrize(
    ('name', 'periods', 'transactions', 'guarantee'),
    [
        ('Great Lakes Commerce Bank 2025.pdf', 1, 292, Guarantee.CHAIN),
        pytest.param('Image Statement Jan 31 2025.pdf', 1, 10, Guarantee.AGGREGATE, marks=pytest.mark.ocr),
    ],
)
def test__extract_result__sample_document__reconciled(name, periods, transactions, guarantee):
    path = SAMPLES / name

    if not path.exists():
        pytest.skip(f'нет образца {name}')

    result = extract_result(path)

    assert len(result.periods) == periods
    assert result.reconciled_periods == periods
    assert sum(len(p.transactions) for p in result.periods) == transactions
    assert result.periods[0].reconciliation.guarantee is guarantee


@pytest.mark.samples
def test__extract_result__observer_given__periods_arrive_as_they_finish():
    """Держать сведённую выписку до конца разбора значит заставлять человека ждать
    данные, которые уже есть."""
    path = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    seen = []

    result = extract_result(path, on_period=seen.append)

    assert [p.summary for p in seen] == [p.summary for p in result.periods]


@pytest.mark.samples
def test__extract__contract_shape__matches_task():
    """Контракт задания: путь к PDF -> словарь с account, summary, transactions."""
    path = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    payload = extract(str(path))

    assert set(payload) >= {'account', 'summary', 'transactions', 'periods', 'meta'}
    assert payload['summary']['beginning_balance'] == 2014882.47
    first = payload['transactions'][0]
    assert set(first) >= {'date', 'description', 'deposit', 'withdrawal', 'balance'}
    assert first['source']['page'] == 1


class Rung:
    """Ступень лестницы без движка: эскалации нужно знать только, доступна ли она."""

    name = 'fake'

    def __init__(self, available=True):
        self._available = available

    def available_for(self, page_index):
        return self._available


class LadderIngestion:
    """Ingestion с двумя ступенями: вторая читает страницу лучше первой."""

    def __init__(self, levels, ladder=None):
        self._levels = levels
        self.ladder = ladder if ladder is not None else [Rung() for _ in levels]

    def prefetch(self, pages, *, min_level=0):
        """Настоящий Ingestion греет кэш параллельно; здесь греть нечего."""

    def lines(self, index, *, min_level=0):
        return self._levels[min_level][index]


def test__build_period__failed_at_first_level__escalates_and_reconciles():
    """Забракованный период переспрашивается на следующей ступени, целый — нет."""
    from bankstatement.extract import _build_period

    broken = [page('Beginning balance 100.00', 'Ending balance 120.00', '04/01 DEPOSIT 50.00 999.99')]
    good = [page('Beginning balance 100.00', 'Ending balance 120.00', '04/01 DEPOSIT 20.00 120.00')]
    ingestion = LadderIngestion([broken, good, good])
    part = make_segment(
        'Beginning balance 100.00',
        'Ending balance 120.00',
        '04/01 DEPOSIT 50.00 999.99',
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00')),
    )

    period = _build_period(ingestion, part)

    assert period.reconciliation.status is ReconStatus.RECONCILED
    assert period.transactions[0].balance == Decimal('120.00')


def test__build_period__escalation_makes_it_worse__original_kept():
    """Переспросить можно и хуже — такой ответ принимать нельзя."""
    from bankstatement.extract import _build_period

    close = [page('Beginning balance 100.00', 'Ending balance 120.00', '04/01 DEPOSIT 25.00 125.00')]
    worse = [page('Beginning balance 100.00', 'Ending balance 120.00', '04/01 DEPOSIT 900.00 1000.00')]
    ingestion = LadderIngestion([close, worse])
    part = make_segment(
        'Beginning balance 100.00',
        'Ending balance 120.00',
        '04/01 DEPOSIT 25.00 125.00',
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00')),
    )

    period = _build_period(ingestion, part)

    assert period.transactions[0].balance == Decimal('125.00')


def test__build_period__reread_sees_nothing__sighted_result_kept():
    """Слепой перечит — без шапки и строк — сходится даром и заменять ничего не должен."""
    from bankstatement.extract import _build_period

    close = [page('Beginning balance 100.00', 'Ending balance 120.00', '04/01 DEPOSIT 25.00 125.00')]
    blind = [page('SCAN NOISE', 'MORE NOISE')]
    ingestion = LadderIngestion([close, blind])
    part = make_segment(
        'Beginning balance 100.00',
        'Ending balance 120.00',
        '04/01 DEPOSIT 25.00 125.00',
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00')),
    )

    period = _build_period(ingestion, part)

    assert period.reconciliation.status is ReconStatus.FAILED
    assert [t.deposit for t in period.transactions] == [Decimal('25.00')]


def test__build_period__rung_out_of_budget__skipped_without_reread():
    """Ступень, которой нечего читать, не переспрашивается, даже если читала бы лучше."""
    from bankstatement.extract import _build_period

    broken = [page('Beginning balance 100.00', 'Ending balance 120.00', '04/01 DEPOSIT 50.00 999.99')]
    good = [page('Beginning balance 100.00', 'Ending balance 120.00', '04/01 DEPOSIT 20.00 120.00')]
    ingestion = LadderIngestion([broken, good], ladder=[Rung(), Rung(available=False)])
    part = make_segment(
        'Beginning balance 100.00',
        'Ending balance 120.00',
        '04/01 DEPOSIT 50.00 999.99',
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00')),
    )

    period = _build_period(ingestion, part)

    assert period.reconciliation.status is ReconStatus.FAILED


def test__build_period__counts_not_printed__computed_for_a_proven_period():
    """Счётчик у доказанного периода выведен той же арифметикой, что и состав."""
    from bankstatement.extract import _build_period

    part = make_segment(
        '04/01 DEPOSIT 50.00 150.00',
        '04/02 PAYMENT 30.00 120.00',
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00')),
    )

    period = _build_period(LadderIngestion([[]]), part)

    assert period.reconciliation.status is ReconStatus.RECONCILED
    assert (period.summary.deposits_count, period.summary.withdrawals_count) == (1, 1)
    assert period.summary.sources['deposits_count'].engine == 'computed'


def test__build_period__counts_not_printed__left_empty_when_not_proven():
    from bankstatement.extract import _build_period

    part = make_segment(
        '04/01 DEPOSIT 50.00 150.00',
        summary=Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00')),
    )

    period = _build_period(LadderIngestion([[]]), part)

    assert period.reconciliation.status is ReconStatus.FAILED
    assert (period.summary.deposits_count, period.summary.withdrawals_count) == (None, None)


def test__canonical_banks__document_majority__fills_gaps_and_edge_words():
    """Логотип один на всю подшивку: большинство выписок называет банк за остальных."""
    from bankstatement.models import Losses, Reconciliation, StatementPeriod

    def period(bank, last4='4664'):
        verdict = Reconciliation(
            status=ReconStatus.FAILED,
            guarantee=Guarantee.NONE,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        )

        return StatementPeriod(
            account=Account(bank=bank, account_last4=last4),
            summary=Summary(),
            transactions=[],
            reconciliation=verdict,
            losses=Losses(),
        )

    periods = [
        period('Ixonia Bank'),
        period('Onla Ixonia Bank'),
        period(None, last4='1234'),
        period('Ixonia Bank'),
        period('ESIxonia Bank'),
        period('Lxonia Bank'),
        period('Ban Bank', last4='1234'),
    ]

    fixed = _canonical_banks(periods)

    assert [p.account.bank for p in fixed] == ['Ixonia Bank'] * 6 + ['Ban Bank']
    assert [p.account.account_last4 for p in fixed][:3] == ['4664', '4664', '1234']
    assert _canonical_banks([period(None)]) == [period(None)]


def _reconciled(guarantee):
    from bankstatement.models import Reconciliation

    return Reconciliation(
        status=ReconStatus.RECONCILED,
        guarantee=guarantee,
        delta=Decimal('0.00'),
        computed_deposits=Decimal('0.00'),
        computed_withdrawals=Decimal('0.00'),
    )


def _unplaced(kind):
    from bankstatement.models import UnplacedRow

    return UnplacedRow(
        date=date(2025, 4, 3), description='X', amounts=[Decimal('7.00')], reason='no_balance', kind=kind
    )


def test__proven__aggregate_with_unexplained_row__not_proven():
    """Класс B подтверждает суммы, но не состав: необъяснённая строка держит период недоказанным."""
    from bankstatement.extract import _proven

    both = Summary(deposits_total=Decimal('50.00'), withdrawals_total=Decimal('30.00'))

    assert not _proven(_reconciled(Guarantee.AGGREGATE), both, [_unplaced('lost')])
    assert _proven(_reconciled(Guarantee.AGGREGATE), both, [_unplaced('marker'), _unplaced('duplicate')])
    assert _proven(_reconciled(Guarantee.CHAIN), Summary(), [_unplaced('lost')])


def test__proven__aggregate_without_both_totals__not_proven():
    from bankstatement.extract import _proven

    assert not _proven(_reconciled(Guarantee.AGGREGATE), Summary(deposits_total=Decimal('50.00')), [])


@pytest.mark.samples
def test__extract_result__deadline_passed__partial_result_marked_truncated():
    """По истечении времени отдаётся то, что готово, и результат не притворяется полным."""
    from bankstatement.models import Opinion

    result = extract_result(Path('samples/Great Lakes Commerce Bank 2025.pdf'), deadline=0.0)

    assert result.truncated
    assert result.periods == []
    assert result.opinion is Opinion.DISCLAIMER
    assert result.to_payload()['meta']['truncated'] is True


@pytest.mark.samples
def test__extract_result__too_many_pages__refused_before_reading():
    from bankstatement.ingest import TooManyPagesError

    with pytest.raises(TooManyPagesError):
        extract_result(Path('samples/Great Lakes Commerce Bank 2025.pdf'), max_pages=1)


def _period_of(bank, last4):
    from bankstatement.models import Losses, Reconciliation, StatementPeriod

    return StatementPeriod(
        account=Account(bank=bank, account_last4=last4),
        summary=Summary(),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.FAILED,
            guarantee=Guarantee.NONE,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
        losses=Losses(),
    )


def test__canonical_banks__same_account_as_the_majority__garbled_name_replaced():
    """Логотип, прочитанный как «Ban Bank», не делает выписку по тому же счёту чужим банком."""
    periods = [_period_of('Ixonia Bank', '4664'), _period_of('Ixonia Bank', '4664'), _period_of('Ban Bank', '4664')]

    assert [p.account.bank for p in _canonical_banks(periods)] == ['Ixonia Bank'] * 3


def test__canonical_banks__other_account_with_its_own_bank__kept():
    periods = [_period_of('Ixonia Bank', '4664'), _period_of('Ixonia Bank', '4664'), _period_of('Chase Bank', '1111')]

    assert [p.account.bank for p in _canonical_banks(periods)][-1] == 'Chase Bank'


def test__extract_result__synthetic_text_layer_pdf__reconciled_class_a(sample_pdf):
    """Синтетическая выписка из conftest проходит весь конвейер: на ней держатся тесты стенда."""
    result = extract_result(sample_pdf)
    period = result.periods[0]

    assert period.reconciliation.status is ReconStatus.RECONCILED
    assert period.reconciliation.guarantee is Guarantee.CHAIN
    assert (period.account.bank, period.account.account_last4) == ('Sample Bank', '1234')
    assert [t.balance for t in period.transactions] == [Decimal('150.00'), Decimal('120.00')]

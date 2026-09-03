from datetime import date
from decimal import Decimal

from bankstatement.models import Guarantee, ReconStatus, Summary, Transaction
from bankstatement.reconcile import chain_breaks, reconcile


def tx(day, *, dep=None, wdr=None, bal=None, desc='X'):
    return Transaction(
        date=date(2025, 4, day),
        description=desc,
        deposit=Decimal(dep) if dep else None,
        withdrawal=Decimal(wdr) if wdr else None,
        balance=Decimal(bal) if bal else None,
    )


def test__reconcile__clean_statement__chain_guarantee():
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('120.00'),
        deposits_total=Decimal('50.00'),
        deposits_count=1,
        withdrawals_total=Decimal('30.00'),
        withdrawals_count=1,
    )
    rows = [tx(1, dep='50.00', bal='150.00'), tx(2, wdr='30.00', bal='120.00')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.CHAIN
    assert result.delta == Decimal('0.00')
    assert result.chain_breaks == 0
    assert result.residual == Decimal('0.00')


def test__reconcile__no_balance_column__aggregate_guarantee():
    """Renasant: колонки баланса нет, подтверждается лишь сумма."""
    summary = Summary(
        beginning_balance=Decimal('-10.00'),
        ending_balance=Decimal('-10.00'),
        deposits_total=Decimal('32537.69'),
        withdrawals_total=Decimal('32537.69'),
    )
    rows = [tx(1, dep='32537.69'), tx(2, wdr='32537.69')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.AGGREGATE


def test__reconcile__empty_parse__residual_shows_the_gap():
    """Дельта по остатку нулевая, но обороты не найдены — это не успех."""
    summary = Summary(
        beginning_balance=Decimal('-10.00'),
        ending_balance=Decimal('-10.00'),
        deposits_total=Decimal('32537.69'),
        withdrawals_total=Decimal('32537.69'),
    )
    result = reconcile(summary, [])

    assert result.status is ReconStatus.FAILED
    assert result.delta == Decimal('0.00')
    assert result.residual == Decimal('65075.38')


def test__reconcile__duplicated_row__diagnosed_with_candidate():
    """Ловушка Fulton: Check Summary повторяет строки активности."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('70.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, wdr='30.00', desc='CHECK #25205'), tx(1, wdr='30.00', desc='CHECK #25205')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED
    kinds = {d.kind for d in result.diagnoses}
    assert 'duplicate_row' in kinds
    assert any('CHECK #25205' in c for d in result.diagnoses for c in d.candidates)


def test__reconcile__row_in_wrong_column__diagnosed():
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('90.00'),
        withdrawals_total=Decimal('10.00'),
    )
    rows = [tx(1, dep='10.00')]  # должна была быть списанием

    result = reconcile(summary, rows)

    assert [d.kind for d in result.diagnoses] == ['wrong_column']


def test__reconcile__digit_transposition__flagged():
    summary = Summary(
        beginning_balance=Decimal('0.00'),
        ending_balance=Decimal('12.34'),
        deposits_total=Decimal('12.34'),
    )
    rows = [tx(1, dep='21.34')]  # переставлены первые две цифры

    result = reconcile(summary, rows)

    assert [d.kind for d in result.diagnoses] == ['digit_transposition']


def test__reconcile__totals_match_counts_do_not__count_only():
    """Самый коварный случай: суммы сошлись, а счётчик — нет."""
    summary = Summary(
        beginning_balance=Decimal('0.00'),
        ending_balance=Decimal('100.00'),
        deposits_total=Decimal('100.00'),
        deposits_count=1,
    )
    rows = [tx(1, dep='60.00'), tx(2, dep='40.00')]  # одна строка разбита надвое

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED
    assert result.count_delta_deposits == 1
    assert [d.kind for d in result.diagnoses] == ['count_only']


def test__reconcile__unexplained_gap__reported_as_such():
    summary = Summary(beginning_balance=Decimal('0.00'), ending_balance=Decimal('1000.00'))
    result = reconcile(summary, [tx(1, dep='7.77')])

    assert [d.kind for d in result.diagnoses] == ['unexplained']


def test__reconcile__no_summary_at_all__never_reconciled():
    """Без итогов сверять не с чем — «сведено» объявить нельзя.

    Формально дельта здесь нулевая, и без явной проверки пустая шапка
    подтверждала бы любой набор строк.
    """
    result = reconcile(Summary(), [tx(1, dep='10.00')])

    assert result.status is ReconStatus.FAILED
    assert result.computed_ending is None
    assert [d.kind for d in result.diagnoses] == ['no_reference']


def test__chain_breaks__broken_row__localised():
    rows = [
        tx(1, dep='50.00', bal='150.00'),
        tx(2, wdr='30.00', bal='125.00'),  # должно быть 120.00
        tx(3, dep='10.00', bal='135.00'),
    ]
    breaks = chain_breaks(rows, Decimal('100.00'))

    assert [i for i, _ in breaks] == [1]
    assert breaks[0][1] == Decimal('5.00')


def test__chain_breaks__no_opening_balance__nothing_to_compare():
    assert chain_breaks([tx(1, dep='50.00', bal='150.00')], None) == []


def test__diagnose__points_at_the_rows_it_talks_about():
    """Диагноз без адреса заставляет искать строку глазами по всему списку."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('70.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, wdr='30.00', desc='CHECK #1'), tx(1, wdr='30.00', desc='CHECK #1')]

    result = reconcile(summary, rows)
    duplicate = next(d for d in result.diagnoses if d.kind == 'duplicate_row')

    assert duplicate.rows == [0, 1]


def test__reconcile__balance_chain_broken__diagnosis_points_at_the_rows():
    """Разрыв цепочки — самая частая причина в реальных документах.

    Диагноз обязан назвать конкретные строки: аудитор идёт в них смотреть, а не
    ищет расхождение заново по всей выписке.
    """
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('120.00'),
        deposits_total=Decimal('50.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, dep='50.00', bal='150.00'), tx(2, wdr='30.00', bal='900.00')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED
    assert result.chain_breaks == 1
    chain = next(d for d in result.diagnoses if d.kind == 'chain_break')
    assert chain.rows == [1]
    assert 'строка 2' in chain.detail


def test__chain_breaks__row_without_a_balance__skipped_not_counted_as_a_break():
    """Проверка идёт по напечатанным остаткам, и строка без остатка в неё не входит.

    Обе гипотезы дают однородный список — с остатком у каждой строки либо ни у
    одной, — поэтому смешанный вход сюда не приходит. Ветка остаётся страховкой,
    и важно, что страховка молчит, а не выдумывает разрыв на пустом месте.
    """
    rows = [tx(1, dep='50.00', bal='150.00'), tx(2, wdr='30.00')]

    assert chain_breaks(rows, Decimal('100.00')) == []


def test__reconcile__ending_balance_not_printed__deposits_total_becomes_the_reference():
    """Когда итогового остатка в шапке нет, эталоном служит напечатанная сумма поступлений."""
    summary = Summary(beginning_balance=Decimal('100.00'), deposits_total=Decimal('70.00'))
    rows = [tx(1, dep='50.00', bal='150.00')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED
    assert result.delta == Decimal('-20.00')


# === Новые тесты для исправления дефектов ===


def test__reconcile__only_ending_no_chain__failed():
    """Дельта на сумме конца нулевая по построению, но это не сведение.

    Без beginning нельзя считать конец, а без цепи нельзя проверить каждую строку.
    Только ending_balance без beginning — это пустая привязка.
    """
    summary = Summary(ending_balance=Decimal('1200.00'))
    rows = []

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED
    assert result.delta == Decimal('0.00')


def test__reconcile__only_ending_with_complete_chain__reconciled_a():
    """Ending + полная цепь (все операции с балансом) → сведено с гарантией A."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('120.00'),
        deposits_total=Decimal('50.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, dep='50.00', bal='150.00'), tx(2, wdr='30.00', bal='120.00')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.CHAIN


def test__reconcile__ending_broken_chain__failed():
    """Ending + разорванная цепь → не сведено."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('120.00'),
        deposits_total=Decimal('50.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, dep='50.00', bal='150.00'), tx(2, wdr='30.00', bal='900.00')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED


def test__reconcile__ending_chain_last_balance_mismatch__failed():
    """Ending + цепь, но последний баланс не совпадает → не сведено."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('200.00'),  # неправильное значение (должно быть 150)
        deposits_total=Decimal('50.00'),
        withdrawals_total=Decimal('0.00'),
    )
    rows = [tx(1, dep='50.00', bal='150.00')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED


def test__reconcile__chain_partial_no_all_balances__aggregate_not_chain():
    """Частичная цепь (не у всех операций баланс) → reconciled но AGGREGATE, не CHAIN.

    CHAIN гарантия требует замкнутой цепи — каждая операция должна подтверждена.
    Если хоть у одной нет баланса, гарантия слабеет до AGGREGATE (если итоги совпадают).
    """
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('120.00'),
        deposits_total=Decimal('50.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, dep='50.00', bal='150.00'), tx(2, wdr='30.00')]  # вторая без баланса

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.AGGREGATE  # не CHAIN, потому не все имеют баланс


def test__reconcile__b_plus_e_plus_dp_plus_dw__reconciled_b():
    """Полная шапка (B+E+DP+DW) с совпадающими операциями → сведено с гарантией B."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('120.00'),
        deposits_total=Decimal('50.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, dep='25.00'), tx(2, dep='25.00'), tx(3, wdr='30.00')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.AGGREGATE


def test__reconcile__empty_with_zero_totals__reconciled():
    """Пустой список операций + явная нулевая сумма в шапке → сведено."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('100.00'),
        deposits_total=Decimal('0.00'),
        withdrawals_total=Decimal('0.00'),
    )
    rows = []

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.AGGREGATE


def test__reconcile__empty_with_negative_balance_unchanged__reconciled():
    """Пустой список + same beginning и ending (оба отрицательные) + нулевые обороты."""
    summary = Summary(
        beginning_balance=Decimal('-10.00'),
        ending_balance=Decimal('-10.00'),
        deposits_total=Decimal('0.00'),
        withdrawals_total=Decimal('0.00'),
    )
    rows = []

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.AGGREGATE


def test__reconcile__dp_plus_dw_only__reconciled_b():
    """Только DP и DW (без начала и конца) + совпадающие операции → сведено B."""
    summary = Summary(
        deposits_total=Decimal('100.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, dep='100.00'), tx(2, wdr='30.00')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.AGGREGATE


def test__reconcile__duplicate_vs_lost_by_sign__duplicate():
    """Дельта имеет тот же знак что найденная строка → дубль."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('70.00'),
        withdrawals_total=Decimal('30.00'),
    )
    rows = [tx(1, wdr='30.00', desc='CHECK #1'), tx(1, wdr='30.00', desc='CHECK #1')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED
    assert [d.kind for d in result.diagnoses] == ['duplicate_row']


def test__reconcile__lost_vs_duplicate_by_sign__lost():
    """Дельта обратна по знаку найденной строке — строка потеряна, а не задвоена.

    Различить эти два случая можно только по знаку, и различить их обязательно:
    при дубле лишнюю строку убирают, при потере — ищут недостающую. Здесь шапка
    обещает 100.00 поступлений и ноль списаний, а найдены и поступление, и
    списание по 100.00: обороты взаимно погасились, дельта -100.00 смотрит
    против найденного поступления.
    """
    summary = Summary(
        beginning_balance=Decimal('0.00'),
        ending_balance=Decimal('100.00'),
        deposits_total=Decimal('100.00'),
        withdrawals_total=Decimal('0.00'),
    )
    rows = [tx(1, dep='100.00'), tx(2, wdr='100.00', desc='WITHDRAWAL')]

    result = reconcile(summary, rows)

    assert result.status is ReconStatus.FAILED
    assert result.delta == Decimal('-100.00')
    assert [d.kind for d in result.diagnoses] == ['lost_row']


def test__reconcile__no_rows_parsed__not_reconciled():
    """Ни одной строки не извлечено — сводить нечего, сколько бы ни было нулей.

    Ловушка настоящая: у выписки, где обороты взаимно погасились, начало и конец
    совпадают. Пустой разбор даёт ту же нулевую дельту, и без этой проверки
    период уходил в отчёт подтверждённым и без единой операции — ровно та
    молчаливо неверная цифра, ради которой оракул и написан.
    """
    summary = Summary(beginning_balance=Decimal('1000.00'), ending_balance=Decimal('1000.00'))

    result = reconcile(summary, [])

    assert result.status is ReconStatus.FAILED
    assert result.guarantee is Guarantee.NONE
    assert [d.kind for d in result.diagnoses] == ['no_rows']


def test__reconcile__header_prints_zero_turnover__empty_period_reconciles():
    """Месяц без движения — законный случай, и объявляет его сама шапка."""
    summary = Summary(
        beginning_balance=Decimal('1000.00'),
        ending_balance=Decimal('1000.00'),
        deposits_total=Decimal('0.00'),
        withdrawals_total=Decimal('0.00'),
    )

    result = reconcile(summary, [])

    assert result.status is ReconStatus.RECONCILED
    assert result.guarantee is Guarantee.AGGREGATE


def _row(day, deposit=None, withdrawal=None):
    return Transaction(
        date=date(2025, 4, day),
        description='X',
        deposit=Decimal(deposit) if deposit else None,
        withdrawal=Decimal(withdrawal) if withdrawal else None,
    )


def test__reconcile__no_balances_and_withdrawals_off__delta_shows_the_gap():
    """Без остатков дельта — влияние расхождений по оборотам; одних поступлений мало."""
    summary = Summary(deposits_total=Decimal('100.00'), withdrawals_total=Decimal('50.00'))

    verdict = reconcile(summary, [_row(1, deposit='100.00'), _row(2, withdrawal='40.00')])

    assert verdict.status is ReconStatus.FAILED
    assert verdict.delta == Decimal('10.00')
    assert verdict.residual == Decimal('20.00')
    assert verdict.diagnoses


def test__reconcile__ending_matches_but_both_totals_off__totals_mismatch_diagnosed():
    """Остаток сошёлся, обороты нет: период не сведён и обязан назвать причину."""
    summary = Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('100.00'),
        deposits_total=Decimal('50.00'),
        withdrawals_total=Decimal('50.00'),
    )

    verdict = reconcile(summary, [_row(1, deposit='60.00'), _row(2, withdrawal='60.00')])

    assert verdict.status is ReconStatus.FAILED
    assert verdict.delta == Decimal('0.00')
    assert verdict.residual == Decimal('20.00')
    assert 'totals_mismatch' in {d.kind for d in verdict.diagnoses}

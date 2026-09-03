"""Оракул: единственное место, где решается, верен результат или нет.

Проверка идёт от сильной к слабой:
  1. цепочка running balance — подтверждает каждую строку по отдельности;
  2. итоги — подтверждают сумму, но не состав;
  3. счётчики — ловят лишнюю или потерянную строку при совпавших суммах.

Оракул детерминирован и ничего не чинит. Он только выносит вердикт и, когда
вердикт отрицательный, объясняет форму расхождения — по ней видно причину.
"""

from dataclasses import dataclass
from decimal import Decimal

from bankstatement.models import (
    DeltaDiagnosis,
    Guarantee,
    Reconciliation,
    ReconStatus,
    Summary,
    Transaction,
)

ZERO = Decimal('0.00')


def has_reference(summary: Summary) -> bool:
    """Есть ли в шапке достаточно информации для проверки.

    Требуется либо (B + E) для расчёта и проверки конца, либо оба оборота (DP + DW).
    Одного ending_balance без начала недостаточно — это не привязка, а просто число.
    Без достаточной информации период нельзя объявить верным — нечего проверять.
    """
    has_b_and_e = summary.beginning_balance is not None and summary.ending_balance is not None
    has_both_totals = summary.deposits_total is not None and summary.withdrawals_total is not None

    return has_b_and_e or has_both_totals


def asserts_no_activity(summary: Summary) -> bool:
    """Шапка сама объявляет период пустым: оба оборота напечатаны нулями."""
    return summary.deposits_total == ZERO and summary.withdrawals_total == ZERO


def chain_breaks(transactions: list[Transaction], beginning: Decimal | None) -> list[tuple[int, Decimal]]:
    """Разрывы цепочки баланса: (индекс строки, требуемая правка).

    Возвращаемая дельта — это то, чего строке не хватает, чтобы связать
    предыдущий баланс со своим. По ней ремонт работает прицельно.
    """
    breaks: list[tuple[int, Decimal]] = []
    previous = beginning

    for i, tx in enumerate(transactions):
        if tx.balance is None:
            continue

        if previous is not None:
            expected = previous + tx.signed_amount

            if expected != tx.balance:
                breaks.append((i, tx.balance - expected))

        previous = tx.balance

    return breaks


@dataclass(frozen=True, slots=True)
class _Totals:
    """Посчитанное по извлечённым строкам — против напечатанного в шапке."""

    deposits: Decimal
    withdrawals: Decimal
    ending: Decimal | None
    delta: Decimal
    delta_deposits: Decimal | None
    delta_withdrawals: Decimal | None
    count_deposits: int | None
    count_withdrawals: int | None


def _totals(summary: Summary, transactions: list[Transaction]) -> _Totals:
    deposits = [t.deposit for t in transactions if t.deposit is not None]
    withdrawals = [t.withdrawal for t in transactions if t.withdrawal is not None]
    computed_deposits = sum(deposits, ZERO)
    computed_withdrawals = sum(withdrawals, ZERO)

    beginning = summary.beginning_balance
    ending = beginning + computed_deposits - computed_withdrawals if beginning is not None else None

    delta_deposits = computed_deposits - summary.deposits_total if summary.deposits_total is not None else None
    delta_withdrawals = (
        computed_withdrawals - summary.withdrawals_total if summary.withdrawals_total is not None else None
    )
    delta = ZERO

    if ending is not None and summary.ending_balance is not None:
        delta = ending - summary.ending_balance
    else:
        # Без остатков уравнение не проверить, и дельта — это чистое влияние
        # расхождений по оборотам на конец периода. Одних поступлений мало:
        # расход, разошедшийся на десять, давал бы «сошлось».
        delta = (delta_deposits or ZERO) - (delta_withdrawals or ZERO)

    return _Totals(
        deposits=computed_deposits,
        withdrawals=computed_withdrawals,
        ending=ending,
        delta=delta,
        delta_deposits=delta_deposits,
        delta_withdrawals=delta_withdrawals,
        count_deposits=(len(deposits) - summary.deposits_count if summary.deposits_count is not None else None),
        count_withdrawals=(
            len(withdrawals) - summary.withdrawals_count if summary.withdrawals_count is not None else None
        ),
    )


def _chain_closes_with_ending(transactions: list[Transaction], ending_balance: Decimal | None) -> bool:
    """Цепь замыкается на ending_balance — последний баланс совпадает."""
    if ending_balance is None or not transactions:
        return False

    last_balance = None

    for tx in reversed(transactions):
        if tx.balance is not None:
            last_balance = tx.balance
            break

    return last_balance == ending_balance if last_balance is not None else False


def _has_chain_verification(
    summary: Summary, transactions: list[Transaction], breaks: list[tuple[int, Decimal]]
) -> bool:
    """Проверяет ли цепь результат.

    Цепь проверяет если:
    1. есть ending_balance И
    2. последний баланс совпадает с ending_balance И
    3. нет разрывов цепи И
    4. все операции имеют баланс (замкнутая цепь)
    """
    if summary.ending_balance is None or not transactions:
        return False

    if breaks:
        return False

    all_have_balance = all(t.balance is not None for t in transactions)

    if not all_have_balance:
        return False

    return _chain_closes_with_ending(transactions, summary.ending_balance)


def _has_totals_verification(summary: Summary, totals: _Totals, transactions: list[Transaction]) -> bool:
    """Проверяет ли сумма итогов результат.

    AGGREGATE гарантия допустима если:
    - есть (B+E) и вычисленный конец совпадает, ИЛИ
    - есть (DP и DW) и оба совпадают.

    Пустой список операций итогами не подтверждается. Равенство начала и конца
    при нуле строк означает не «оборотов не было», а «ни одна строка не
    прочиталась»: у выписки, где сотня операций взаимно погасилась, дельта тоже
    нулевая, и период ушёл бы в отчёт подтверждённым и пустым. Объявить период
    пустым вправе только сама шапка — напечатанными нулевыми оборотами.
    """
    if not transactions and not asserts_no_activity(summary):
        return False

    # Вариант 1: B + E (и вычисленный конец совпадает)
    if summary.beginning_balance is not None and summary.ending_balance is not None:
        return totals.delta == ZERO

    # Вариант 2: DP и DW оба есть и совпадают
    if summary.deposits_total is not None and summary.withdrawals_total is not None:
        return totals.delta_deposits == ZERO and totals.delta_withdrawals == ZERO

    return False


def reconcile(summary: Summary, transactions: list[Transaction]) -> Reconciliation:
    """Свести извлечённые строки с напечатанными итогами.

    Оракул видит ровно две вещи: что напечатано в шапке и что извлечено. Ни о
    гипотезах, ни о ремонте, ни о потерях он не знает — иначе вердикт перестал
    бы быть функцией от документа. Учёт потерь ведёт `extract`.

    Args:
        summary: Итоги из шапки выписки.
        transactions: Размещённые операции.

    Returns:
        Вердикт с дельтой, гарантией и, если не сошлось, диагнозом.
    """
    totals = _totals(summary, transactions)

    has_balances = any(t.balance is not None for t in transactions)
    breaks = chain_breaks(transactions, summary.beginning_balance) if has_balances else []
    chain_rows = sum(1 for t in transactions if t.balance is not None)

    counts_ok = not (totals.count_deposits or totals.count_withdrawals)
    totals_match = _totals_match(summary, totals.deposits, totals.withdrawals)

    # Базовые условия
    ok = has_reference(summary) and totals.delta == ZERO and not breaks and totals_match and counts_ok

    # Дополнительное условие: должна быть действительная привязка (цепь или итоги)
    if ok:
        has_chain = _has_chain_verification(summary, transactions, breaks)
        has_totals = _has_totals_verification(summary, totals, transactions)

        if not (has_chain or has_totals):
            ok = False

    guarantee = Guarantee.NONE
    status = ReconStatus.FAILED

    if ok:
        # Выбираем гарантию
        has_chain = _has_chain_verification(summary, transactions, breaks)
        has_totals = _has_totals_verification(summary, totals, transactions)

        if has_chain:
            guarantee = Guarantee.CHAIN
        elif has_totals:
            guarantee = Guarantee.AGGREGATE
        else:
            guarantee = Guarantee.NONE

        status = ReconStatus.RECONCILED

    diagnoses = _explain(summary, transactions, totals, breaks) if not ok else []

    return Reconciliation(
        status=status,
        guarantee=guarantee,
        delta=totals.delta,
        computed_deposits=totals.deposits,
        computed_withdrawals=totals.withdrawals,
        computed_ending=totals.ending,
        delta_deposits=totals.delta_deposits,
        delta_withdrawals=totals.delta_withdrawals,
        chain_rows=chain_rows,
        chain_breaks=len(breaks),
        count_delta_deposits=totals.count_deposits,
        count_delta_withdrawals=totals.count_withdrawals,
        diagnoses=diagnoses,
    )


def _explain(
    summary: Summary,
    transactions: list[Transaction],
    totals: _Totals,
    breaks: list[tuple[int, Decimal]],
) -> list[DeltaDiagnosis]:
    """Объяснить, почему период не сошёлся."""
    found = diagnose(totals.delta, transactions, breaks, totals.count_deposits, totals.count_withdrawals)

    if totals.delta == ZERO and (totals.delta_deposits or totals.delta_withdrawals):
        # Остаток сошёлся, а обороты нет: одна и та же сумма попала в обе
        # колонки либо шапка прочитана с ошибкой. Без этого диагноза период
        # выглядел бы несведённым без причины и с нулевой дельтой.
        found = [
            DeltaDiagnosis(
                kind='totals_mismatch',
                detail=(
                    f'остаток сошёлся, а обороты нет: приход {totals.delta_deposits or ZERO}, '
                    f'расход {totals.delta_withdrawals or ZERO}'
                ),
            ),
            *found,
        ]

    if not transactions:
        found = [
            DeltaDiagnosis(
                kind='no_rows',
                detail='не извлечено ни одной операции, а шапка не объявляет период пустым',
            ),
            *found,
        ]

    if has_reference(summary):
        return found

    return [
        DeltaDiagnosis(
            kind='no_reference',
            detail='в шапке не найдено ни одного итога — сверять транзакции не с чем',
        ),
        *found,
    ]


def _totals_match(summary: Summary, computed_deposits: Decimal, computed_withdrawals: Decimal) -> bool:
    if summary.deposits_total is not None and computed_deposits != summary.deposits_total:
        return False

    return not (summary.withdrawals_total is not None and computed_withdrawals != summary.withdrawals_total)


def diagnose(
    delta: Decimal,
    transactions: list[Transaction],
    breaks: list[tuple[int, Decimal]],
    count_delta_deposits: int | None,
    count_delta_withdrawals: int | None,
) -> list[DeltaDiagnosis]:
    """Форма расхождения указывает на причину — это и есть содержание отчёта."""
    out: list[DeltaDiagnosis] = []

    if breaks:
        out.append(_broken_chain(transactions, breaks))

    if delta == ZERO:
        if count_delta_deposits or count_delta_withdrawals:
            out.append(
                DeltaDiagnosis(
                    kind='count_only',
                    detail='суммы сошлись, а счётчики нет: учтена служебная строка или одна транзакция разбита надвое',
                )
            )

        return out

    found = [check(delta, transactions) for check in (_duplicate, _wrong_column, _transposition)]
    out.extend(d for d in found if d is not None)

    if not out:
        out.append(
            DeltaDiagnosis(
                kind='unexplained',
                detail=f'дельта {delta} не объясняется одной строкой — вероятно потеряно несколько',
            )
        )

    return out


def _broken_chain(transactions: list[Transaction], breaks: list[tuple[int, Decimal]]) -> DeltaDiagnosis:
    head = ', '.join(f'строка {i + 1} на {d}' for i, d in breaks[:5])

    return DeltaDiagnosis(
        kind='chain_break',
        detail=f'разрывов цепочки баланса: {len(breaks)} ({head})',
        candidates=[_describe(transactions[i]) for i, _ in breaks[:5]],
        rows=[i for i, _ in breaks[:5]],
    )


def _duplicate(delta: Decimal, transactions: list[Transaction]) -> DeltaDiagnosis | None:
    """Дельта равна сумме одной строки.

    Используем знак дельты чтобы различить:
    - дубль (строка посчитана дважды): дельта имеет тот же знак что строка
    - потеря (строка не учтена): дельта имеет противоположный знак
    """
    hits = [(i, t) for i, t in enumerate(transactions) if abs(t.signed_amount) == abs(delta)]

    if not hits:
        return None

    # Определяем тип ошибки по знаку
    first_hit_amount = hits[0][1].signed_amount

    if (first_hit_amount > 0 and delta > 0) or (first_hit_amount < 0 and delta < 0):
        kind = 'duplicate_row'
        detail = f'дельта {delta} равна сумме найденной строки — вероятно задвоение'
    else:
        kind = 'lost_row'
        detail = f'дельта {delta} обратна сумме найденной строки — вероятно потеря строки'

    return DeltaDiagnosis(
        kind=kind,
        detail=detail,
        candidates=[_describe(t) for _, t in hits[:5]],
        rows=[i for i, _ in hits[:5]],
    )


def _wrong_column(delta: Decimal, transactions: list[Transaction]) -> DeltaDiagnosis | None:
    """Дельта вдвое больше суммы строки — у строки перепутан знак."""
    hits = [(i, t) for i, t in enumerate(transactions) if abs(t.signed_amount) * 2 == abs(delta)]

    if not hits:
        return None

    return DeltaDiagnosis(
        kind='wrong_column',
        detail=f'дельта {delta} равна удвоенной сумме строки — строка попала не в ту колонку',
        candidates=[_describe(t) for _, t in hits[:5]],
        rows=[i for i, _ in hits[:5]],
    )


def _transposition(delta: Decimal, transactions: list[Transaction]) -> DeltaDiagnosis | None:
    """Дельта кратна девяти — классический признак перестановки цифр."""
    cents = int((abs(delta) * 100).to_integral_value())

    if not cents or cents % 9:
        return None

    return DeltaDiagnosis(
        kind='digit_transposition',
        detail=f'дельта {delta} кратна девяти — перестановка цифр или сдвиг разряда в одном числе',
    )


def _describe(tx: Transaction) -> str:
    return f'{tx.date.isoformat()} {tx.description[:48]} {tx.signed_amount}'

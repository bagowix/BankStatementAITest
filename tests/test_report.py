from datetime import date
from decimal import Decimal

from bankstatement.models import (
    Account,
    ExtractionResult,
    Guarantee,
    Period,
    Reconciliation,
    ReconStatus,
    StatementPeriod,
    Summary,
)
from bankstatement.report import render


def make_result(status=ReconStatus.RECONCILED, diagnoses=()):
    period = StatementPeriod(
        account=Account(
            bank='TEST BANK', account_last4='4071', period=Period(start=date(2025, 1, 1), end=date(2025, 1, 28))
        ),
        summary=Summary(
            beginning_balance=Decimal('100.00'),
            ending_balance=Decimal('120.00'),
            deposits_total=Decimal('50.00'),
            withdrawals_total=Decimal('30.00'),
        ),
        transactions=[],
        reconciliation=Reconciliation(
            status=status,
            guarantee=Guarantee.CHAIN if status is ReconStatus.RECONCILED else Guarantee.NONE,
            delta=Decimal('0.00') if status is ReconStatus.RECONCILED else Decimal('5.00'),
            computed_deposits=Decimal('50.00'),
            computed_withdrawals=Decimal('30.00'),
            diagnoses=list(diagnoses),
        ),
        pages=[1, 2],
    )

    return ExtractionResult(periods=[period], source_file='x.pdf', engine_stats={'tesseract': 2})


def test__render__reconciled_period__marked_as_passed():
    text = render(make_result())

    assert '✓' in text
    assert 'TEST BANK' in text
    assert 'tesseract: 2 стр.' in text


def test__render__failed_period__delta_and_diagnosis_visible():
    """Несведённый период обязан показать расхождение, а не спрятать его."""
    from bankstatement.models import DeltaDiagnosis

    text = render(make_result(ReconStatus.FAILED, [DeltaDiagnosis(kind='duplicate_row', detail='задвоение')]))

    assert 'НЕ СВЕДЕНО' in text
    assert 'duplicate_row' in text
    assert 'задвоение' in text


def test__render__no_periods__disclaims_instead_of_claiming_success():
    """Пустой документ — не «всё сошлось»: сверять было нечего, и это разные вещи."""
    text = render(ExtractionResult(periods=[], source_file='empty.pdf'))

    assert text.startswith('ЗАКЛЮЧЕНИЕ НЕВОЗМОЖНО')
    assert 'выписок 0' in text
    assert 'нет данных' in text


def test__render__opinion_comes_first():
    """Читателю, которому нужен один ответ, он достаётся первой строкой."""
    assert render(make_result()).startswith('ПОДТВЕРЖДЕНО —')


def test__render__scope_states_what_was_examined():
    """Заключение без объёма проверки ничего не стоит: непонятно, о чём оно."""
    text = render(make_result())

    assert 'проверено: выписок 1, страниц 2, операций 0' in text
    assert 'сведено: 1 из 1' in text


def test__render__failures__summarised_before_the_details():
    """Сводка расхождений идёт до периодов — иначе итог собирают глазами по блокам."""
    from bankstatement.models import DeltaDiagnosis

    text = render(make_result(ReconStatus.FAILED, [DeltaDiagnosis(kind='duplicate_row', detail='задвоение')]))
    summary_at = text.index('ТРЕБУЮТ ПРОВЕРКИ')
    details_at = text.index('НЕ СВЕДЕНО')

    assert summary_at < details_at
    assert 'нераскрыто 5.00' in text
    assert text.startswith('НЕ ПОДТВЕРЖДЕНО')  # период здесь один, и он не сошёлся

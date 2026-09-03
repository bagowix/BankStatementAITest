from calendar import monthrange
from datetime import date
from decimal import Decimal

import pytest

from bankstatement.evaluate import (
    Evaluation,
    FileScore,
    continuity,
    render_markdown,
    render_notes,
    render_table,
    update_readme,
)
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


def make_period(last4, beginning, ending, month):
    last_day = monthrange(2025, month)[1]

    return StatementPeriod(
        account=Account(
            account_last4=last4, period=Period(start=date(2025, month, 1), end=date(2025, month, last_day))
        ),
        summary=Summary(beginning_balance=Decimal(beginning), ending_balance=Decimal(ending)),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.CHAIN,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
    )


def test__continuity__consecutive_statements__ends_match_next_beginnings():
    """Внешняя проверка: конец одного периода обязан быть началом следующего."""
    result = ExtractionResult(
        periods=[make_period('4664', '100.00', '120.00', 1), make_period('4664', '120.00', '130.00', 2)]
    )

    assert continuity(result) == (1, 1)


def test__continuity__statements_out_of_order__sorted_before_comparing():
    """В подшивке выписки лежат не по хронологии."""
    result = ExtractionResult(
        periods=[make_period('4664', '120.00', '130.00', 2), make_period('4664', '100.00', '120.00', 1)]
    )

    assert continuity(result) == (1, 1)


def test__continuity__gap_between_periods__not_a_seam():
    """Между январём и июнем выписок просто нет — стыковать нечего."""
    result = ExtractionResult(
        periods=[make_period('4664', '100.00', '120.00', 1), make_period('4664', '999.00', '130.00', 6)]
    )

    assert continuity(result) == (0, 0)


def test__continuity__gap_between_statements__reported():
    result = ExtractionResult(
        periods=[make_period('4664', '100.00', '120.00', 1), make_period('4664', '999.00', '130.00', 2)]
    )

    assert continuity(result) == (0, 1)


def test__continuity__different_accounts__not_compared():
    """В подшивке периоды разных счетов идут вперемешку — стыковать их нельзя."""
    result = ExtractionResult(
        periods=[make_period('4664', '100.00', '120.00', 1), make_period('4623', '999.00', '130.00', 2)]
    )

    assert continuity(result) == (0, 0)


def test__render_table__failures__listed_below_the_table():
    score = FileScore(
        name='statement.pdf',
        pages=4,
        periods=2,
        reconciled=1,
        transactions=10,
        chain_periods=1,
        aggregate_periods=0,
        seconds=1.5,
        engines={'tesseract': 4},
        continuity_ok=0,
        continuity_total=1,
        failures=['2025-01-01: дельта 5.00 (duplicate_row)'],
    )
    text = render_table(Evaluation(files=[score]))

    assert 'statement.pdf' in text
    assert 'не сведено:' in text
    assert 'duplicate_row' in text
    assert not score.clean


def test__evaluation__totals__summed_over_files():
    score = FileScore(
        name='a.pdf',
        pages=1,
        periods=1,
        reconciled=1,
        transactions=7,
        chain_periods=1,
        aggregate_periods=0,
        seconds=0.1,
        engines={},
        continuity_ok=0,
        continuity_total=0,
    )
    report = Evaluation(files=[score, score])

    assert (report.periods, report.reconciled, report.transactions) == (2, 2, 14)
    assert report.to_payload()['totals']['transactions'] == 14
    assert score.clean


def make_score(name='a.pdf', failures=()):
    return FileScore(
        name=name,
        pages=2,
        periods=2,
        reconciled=2 - len(failures),
        transactions=5,
        chain_periods=1,
        aggregate_periods=1,
        seconds=0.2,
        engines={'textlayer': 2},
        continuity_ok=1,
        continuity_total=1,
        failures=list(failures),
    )


def test__render_markdown__table__has_row_per_file_and_totals():
    text = render_markdown(Evaluation(files=[make_score()]))

    assert '| a.pdf | 2 | 2 | 2 | 1/1 | 5 | 0 | — | 1/1 | 0.2 |' in text
    assert '**итого**' in text


def test__update_readme__markers__replaced_in_place(tmp_path):
    """README собирается из замера: таблица в нём не пишется руками."""
    readme = tmp_path / 'README.md'
    readme.write_text(
        'до\n<!-- EVAL_TABLE -->\nстарое\n<!-- EVAL_TABLE -->\nсередина\n<!-- EVAL_NOTES -->\nx\n<!-- EVAL_NOTES -->\nпосле\n'
    )

    update_readme(readme, Evaluation(files=[make_score(failures=['2025-01-01: дельта 5.00 (duplicate_row)'])]))
    text = readme.read_text()

    assert 'старое' not in text
    assert 'a.pdf' in text
    assert 'duplicate_row' in text
    assert text.startswith('до\n') and text.endswith('после\n')


def test__update_readme__marker_at_end_of_file__keeps_trailing_newline(tmp_path):
    """Замер прогоняется регулярно: срезанный перевод строки лез бы в каждый diff."""
    readme = tmp_path / 'README.md'
    readme.write_text('<!-- EVAL_TABLE -->\nx\n<!-- EVAL_TABLE -->\n<!-- EVAL_NOTES -->\ny\n<!-- EVAL_NOTES -->')

    update_readme(readme, Evaluation(files=[make_score()]))

    assert readme.read_text().endswith('\n')


def test__render_notes__everything_clean__says_so():
    assert 'Полностью сведены: a.pdf' in render_notes(Evaluation(files=[make_score()]))


def test__update_readme__unpaired_marker__refuses_to_write(tmp_path):
    """Без закрывающего маркера подстановка съела бы остаток файла."""
    readme = tmp_path / 'README.md'
    readme.write_text('<!-- EVAL_TABLE -->\nхвост\n')

    with pytest.raises(ValueError, match='два маркера'):
        update_readme(readme, Evaluation(files=[make_score()]))

    assert 'хвост' in readme.read_text()


def test__file_score__no_periods__not_clean():
    """Ноль сведённых из нуля — пустой файл, а не успех."""
    score = FileScore(
        name='empty.pdf',
        pages=1,
        periods=0,
        reconciled=0,
        transactions=0,
        chain_periods=0,
        aggregate_periods=0,
        seconds=0.1,
        engines={},
        continuity_ok=0,
        continuity_total=0,
    )

    assert not score.clean


def _extracted(start, end, last4, ending, count=None):
    from bankstatement.models import Account, Losses, Period, Reconciliation, StatementPeriod, Summary

    return StatementPeriod(
        account=Account(bank='Ixonia Bank', account_last4=last4, period=Period(start=start, end=end)),
        summary=Summary(
            beginning_balance=Decimal('1.00'),
            deposits_total=Decimal('2.00'),
            withdrawals_total=Decimal('3.00'),
            ending_balance=Decimal(ending),
            deposits_count=count,
        ),
        transactions=[],
        reconciliation=Reconciliation(
            status=ReconStatus.RECONCILED,
            guarantee=Guarantee.CHAIN,
            delta=Decimal('0.00'),
            computed_deposits=Decimal('2.00'),
            computed_withdrawals=Decimal('3.00'),
        ),
        losses=Losses(),
    )


def _truth(start, end, last4, ending, count=None):
    return {
        'start': start,
        'end': end,
        'bank': 'Ixonia Bank',
        'account_last4': last4,
        'beginning_balance': 1.0,
        'deposits_total': 2.0,
        'deposits_count': count,
        'withdrawals_total': 3.0,
        'withdrawals_count': None,
        'ending_balance': ending,
    }


def test__header_score__one_field_off__counted_and_named():
    """Точное совпадение по полям; поле, которого банк не печатает, не считается."""
    from bankstatement.evaluate import header_score
    from bankstatement.models import ExtractionResult

    result = ExtractionResult(periods=[_extracted(date(2024, 9, 1), date(2024, 9, 30), '4664', '0.00', count=5)])
    truth = [_truth('2024-09-01', '2024-09-30', '4664', 5.0, count=5)]

    exact, total, misses = header_score(result, truth)

    assert (exact, total) == (8, 9)
    assert misses == ['2024-09-01: ending_balance = 0.00, эталон 5.0']


def test__header_score__two_accounts_same_month__paired_by_account_when_counts_differ():
    """Подшивка: две выписки за один месяц по разным счетам, одна из них не найдена."""
    from bankstatement.evaluate import header_score
    from bankstatement.models import ExtractionResult

    found = _extracted(date(2024, 9, 1), date(2024, 9, 30), '4623', '0.00')
    truth = [
        _truth('2024-09-01', '2024-09-30', '4664', 0.0),
        _truth('2024-09-01', '2024-09-30', '4623', 0.0),
    ]

    exact, total, misses = header_score(ExtractionResult(periods=[found]), truth)

    assert (exact, total) == (8, 16)
    assert all(miss.startswith('2024-09-01') for miss in misses)


def test__load_truth__no_file__none(tmp_path):
    from bankstatement.evaluate import load_truth

    assert load_truth(tmp_path / 'nothing.pdf', tmp_path) is None

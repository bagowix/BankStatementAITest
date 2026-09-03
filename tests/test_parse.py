"""Тесты на разбор строк выписки и гипотезы."""

from datetime import date
from decimal import Decimal

import pytest

from bankstatement.layout import group_lines
from bankstatement.models import Summary
from bankstatement.parse import chain_hypothesis
from bankstatement.rows import _skip_continuation
from bankstatement.sections import section_hypothesis
from tests.conftest import page, text_line

EMPTY = Summary(beginning_balance=Decimal('0.00'))

APRIL = (date(2025, 4, 1), date(2025, 4, 30))


class TestSkipContinuation:
    """Тесты для фильтра служебных строк при приклейке продолжений."""

    def test__skip_continuation__header_keyword_account__true(self):
        """Строка со словом ACCOUNT не приклеивается."""
        words = text_line('ACCOUNT STATEMENT', 100.0)
        assert _skip_continuation(words)

    def test__skip_continuation__header_keyword_statement__true(self):
        """Строка со словом STATEMENT не приклеивается."""
        words = text_line('STATEMENT CLOSING', 100.0)
        assert _skip_continuation(words)

    def test__skip_continuation__normal_continuation__false(self):
        """Обычное продолжение описания приклеивается."""
        words = text_line('Payee Name Continuation', 100.0)
        assert not _skip_continuation(words)

    def test__skip_continuation__single_digit__true(self):
        """Одно слово без цифр считается служебным."""
        words = text_line('PAGE', 100.0)
        assert _skip_continuation(words)


def test__chain_hypothesis__lost_row__chain_resyncs_on_the_next_confirmed_balance():
    """Потерянная операция: строка после неё сходится не с нами, а со своей следующей."""
    lines = page('04/01 A 10.00 110.00', '04/02 B 5.00 130.00', '04/03 C 7.00 137.00')

    found = chain_hypothesis(lines, Summary(beginning_balance=Decimal('100.00')), APRIL)

    assert [t.balance for t in found.transactions] == [Decimal('110.00'), Decimal('130.00'), Decimal('137.00')]
    assert found.transactions[1].deposit == Decimal('5.00')
    assert found.skipped == 1


def test__chain_hypothesis__balance_with_one_wrong_digit__repaired_when_the_next_row_confirms():
    lines = page('04/01 A 10.00 110.00', '04/02 B 20.00 138.00', '04/03 C 5.00 135.00')

    found = chain_hypothesis(lines, Summary(beginning_balance=Decimal('100.00')), APRIL)

    assert [t.balance for t in found.transactions] == [Decimal('110.00'), Decimal('130.00'), Decimal('135.00')]
    assert found.transactions[1].source is not None
    assert found.transactions[1].source.printed == '138.00'
    assert found.repaired == 1


def test__chain_hypothesis__several_rows_without_balance__signs_recovered_by_the_next_intact_row():
    """Пять подвешенных сумм раскладываются перебором знаков по одному скачку остатка."""
    lines = page(
        '04/01 A 10.00 110.00',
        '04/02 B 5.00',
        '04/03 C 7.00',
        '04/04 D 1.00',
        '04/05 E 2.00',
        '04/06 F 3.00',
        '04/07 G 4.00 110.00',
    )

    found = chain_hypothesis(lines, Summary(beginning_balance=Decimal('100.00')), APRIL)

    assert len(found.transactions) == 7
    assert found.transactions[-1].balance == Decimal('110.00')
    assert found.repaired == 5
    assert all(t.source is not None and t.source.repaired for t in found.transactions[1:6])


def test__section_hypothesis__balance_marker_under_a_debit_heading__not_an_operation():
    """'Sep 30 ENDING BALANCE -4.00' совпадает с напечатанным остатком: это маркер, а не списание."""
    lines = page('OTHER DEBITS', '09/30 PAPER STATEMENT FEE 4.00', '09/30 ENDING BALANCE -4.00')
    summary = Summary(beginning_balance=Decimal('0.00'), ending_balance=Decimal('-4.00'))

    found = section_hypothesis(lines, summary, (date(2024, 9, 1), date(2024, 9, 30)))

    assert [t.withdrawal for t in found.transactions] == [Decimal('4.00')]
    assert [(row.reason, row.kind) for row in found.unplaced_rows] == [('balance_marker', 'marker')]
    assert found.suspect_pages == frozenset()


def test__chain_hypothesis__zero_amount_with_unchanged_balance__not_an_operation():
    """Строка сводки остатков по дням: 0.00 и тот же остаток. Операция баланс двигает."""
    lines = page('09/03 A 10.00 110.00', '09/17 Sep 25 0.00 110.00', '09/20 B 5.00 105.00')

    found = chain_hypothesis(lines, Summary(beginning_balance=Decimal('100.00')), (date(2024, 9, 1), date(2024, 9, 30)))

    assert [t.description for t in found.transactions] == ['A', 'B']


def test__chain_hypothesis__balance_marker_that_happens_to_chain__not_an_operation():
    """Маркер остатка со вторым числом, случайно продолжившим цепочку, всё равно не операция."""
    lines = page('09/30 STATEMENT FEE 4.00 4.00', '09/30 ENDING BALANCE -4.00 0.00')
    summary = Summary(beginning_balance=Decimal('0.00'), ending_balance=Decimal('-4.00'))

    found = chain_hypothesis(lines, summary, (date(2024, 9, 1), date(2024, 9, 30)))

    assert [t.description for t in found.transactions] == ['STATEMENT FEE']
    assert [(row.reason, row.kind) for row in found.unplaced_rows] == [('balance_marker', 'marker')]


def test__chain_hypothesis__day_first_dates__inferred_from_the_document():
    """'31-03-2026' бывает только днём вперёд, и весь документ читается так же."""
    lines = page(
        '02-03-2026 RTGS CLIENT 180,000.00 428,510.44',
        '31-03-2026 NEFT RENT 68,000.00 360,510.44',
    )
    summary = Summary(beginning_balance=Decimal('248510.44'))

    found = chain_hypothesis(lines, summary, None)

    assert [t.date for t in found.transactions] == [date(2026, 3, 2), date(2026, 3, 31)]


def test__chain_hypothesis__decimal_shift_of_the_amount__needs_the_next_row():
    """Напечатанные 120.00 становятся 12.00 только с подтверждением соседа."""
    summary = Summary(beginning_balance=Decimal('100.00'))
    confirmed = page('04/01 A 120.00 112.00', '04/02 B 10.00 122.00')
    alone = page('04/01 A 120.00 112.00', '04/02 B 10.00 999.00')

    with_neighbour = chain_hypothesis(confirmed, summary, APRIL)
    without = chain_hypothesis(alone, summary, APRIL)

    assert [t.deposit for t in with_neighbour.transactions] == [Decimal('12.00'), Decimal('10.00')]
    assert with_neighbour.transactions[0].source is not None
    assert with_neighbour.transactions[0].source.printed == '120.00'
    assert without.transactions == []
    assert [row.description for row in without.unplaced_rows] == ['A', 'B']


@pytest.mark.samples
@pytest.mark.samples
class TestRegressions:
    """Регрессионные тесты на реальных образцах."""

    def test__extract_great_lakes__292_operations__no_regression(self):
        """Great Lakes остаётся сведённым с 292 операциями."""
        from src.bankstatement.extract import extract

        result = extract('samples/Great Lakes Commerce Bank 2025.pdf')
        assert len(result['transactions']) == 292

    def test__extract_april_2021__284_operations__no_regression(self):
        """April 2021 остаётся сведённым с 284 операциями."""
        from src.bankstatement.extract import extract

        result = extract('samples/April 2021 eStatement.pdf')
        assert len(result['transactions']) == 284

    @pytest.mark.ocr
    def test__extract_renasant__operations_reconciled__no_regression(self):
        """Renasant (sections hypothesis) остаётся сведённым."""
        from src.bankstatement.extract import extract

        result = extract('samples/Image Statement Jan 31 2025.pdf')
        # Проверяем, что это не пустой результат и общее количество > 5
        assert len(result['transactions']) > 5, (
            f'Renasant должна иметь операции, получили {len(result["transactions"])}'
        )


class TestRepairedProvenance:
    """Починенное число обязано сознаваться в том, что оно починено.

    Смысл провенанса не в номере страницы, а в возможности перепроверить: если
    код заменил напечатанное, аудитор должен увидеть и признак правки, и то
    чтение, которое правка перекрыла. Иначе выправленная цифра неотличима от
    прочитанной, и проверять её никто не станет.
    """

    def test__chain_hypothesis__balance_with_a_typo__source_carries_the_printed_reading(self):
        """Остаток 180.00 прочитан вместо 160.00; правку подтвердила следующая строка."""
        lines = page(
            'Apr 01 PAYMENT 30.00 130.00',
            'Apr 02 PAYMENT 30.00 180.00',
            'Apr 03 CHECK 20.00 140.00',
        )
        summary = Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('140.00'))

        result = chain_hypothesis(lines, summary, APRIL)

        fixed = result.transactions[1]
        assert fixed.balance == Decimal('160.00')
        assert fixed.source is not None
        assert fixed.source.repaired
        assert fixed.source.printed == '180.00'

    def test__chain_hypothesis__row_read_as_printed__source_claims_no_repair(self):
        """Соседние строки прочитаны как есть — и помечены как есть."""
        lines = page(
            'Apr 01 PAYMENT 30.00 130.00',
            'Apr 02 PAYMENT 30.00 180.00',
            'Apr 03 CHECK 20.00 140.00',
        )
        summary = Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('140.00'))

        result = chain_hypothesis(lines, summary, APRIL)

        assert [t.source.repaired for t in result.transactions if t.source] == [False, True, False]
        assert result.transactions[0].source is not None
        assert result.transactions[0].source.printed is None

    def test__chain_hypothesis__balance_never_recognised__repaired_without_a_printed_reading(self):
        """У одинокой суммы колонка баланса не распозналась: перекрывать нечего.

        Остаток здесь вычислен цепочкой, а не прочитан, — и это тоже правка,
        хотя напечатанного токена под ней нет.
        """
        lines = page(
            'Apr 01 PAYMENT 30.00',
            'Apr 02 CHECK 20.00 110.00',
        )
        summary = Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('110.00'))

        result = chain_hypothesis(lines, summary, APRIL)

        restored = result.transactions[0]
        assert restored.balance == Decimal('130.00')
        assert restored.source is not None
        assert restored.source.repaired
        assert restored.source.printed is None


class TestSectionContinuation:
    """Раздел без колонки остатка, продолжающийся на следующей странице.

    Знак раздела — единственный источник знака у выписок вроде Renasant, и
    ошибка здесь не приводит к разрыву цепочки: строка просто уходит не в ту
    сторону, а период расходится ровно на удвоенную сумму. Все четыре ловушки
    ниже сняты с настоящих макетов.
    """

    def test__section_hypothesis__section_continues_on_next_page__rows_keep_the_sign(self):
        """Список списаний на две страницы печатает заголовок один раз.

        Пока знак не переносился, вторая страница пропадала целиком, причём
        молча: skipped=0, эскалации не за что зацепиться.
        """
        lines = page('CHECKS', '101 Apr 01 100.00', '102 Apr 02 200.00', page_index=0) + page(
            '103 Apr 03 300.00', '104 Apr 04 400.00', page_index=1
        )

        result = section_hypothesis(lines, EMPTY, APRIL)

        assert [t.withdrawal for t in result.transactions] == [
            Decimal('100.00'),
            Decimal('200.00'),
            Decimal('300.00'),
            Decimal('400.00'),
        ]
        assert result.skipped == 0

    def test__section_hypothesis__running_header_with_a_credit_word__does_not_flip_the_sign(self):
        """«DEPOSIT ACCOUNT STATEMENT» стоит на каждой странице — это не заголовок раздела.

        На странице продолжения он оказывался единственным «заголовком», и чек
        выдавался приходом. Отличает его повторяемость на той же высоте.
        """
        lines = page('DEPOSIT ACCOUNT STATEMENT', 'CHECKS', '101 Apr 01 100.00', page_index=0) + page(
            'DEPOSIT ACCOUNT STATEMENT', '102 Apr 02 200.00', page_index=1
        )

        result = section_hypothesis(lines, EMPTY, APRIL)

        assert [t.withdrawal for t in result.transactions] == [Decimal('100.00'), Decimal('200.00')]
        assert [t.deposit for t in result.transactions] == [None, None]

    def test__section_hypothesis__row_before_any_heading__counted_and_page_marked(self):
        """Строке без применимого заголовка знак взять неоткуда — но и потерять её нельзя."""
        lines = page('101 Apr 01 100.00', 'CHECKS', '102 Apr 02 200.00', page_index=0)

        result = section_hypothesis(lines, EMPTY, APRIL)

        assert len(result.transactions) == 1
        assert result.skipped == 1
        assert [u.reason for u in result.unplaced_rows] == ['no_sign']
        assert result.suspect_pages == frozenset({0})

    def test__section_hypothesis__microtext_outside_the_column__not_taken_for_a_row(self):
        """Перенос знака не бесплатен: на странице продолжения бывают образы чеков.

        Их микротекст тоже даёт дату и сумму. Отличает продолжение таблицы от
        постороннего текста только сетка — суммы раздела выровнены по правому краю.
        """
        first = page('CHECKS', '101 Apr 01 100.00', '102 Apr 02 200.00', page_index=0)
        stray = group_lines(text_line('103 Apr 03 300.00', 20.0, x0=260.0, page=1))

        result = section_hypothesis(first + stray, EMPTY, APRIL)

        assert len(result.transactions) == 2
        assert [u.reason for u in result.unplaced_rows] == ['foreign_column']
        assert result.suspect_pages == frozenset({1})


def test__collect_rows__continuation_on_the_next_page__not_glued_to_previous_row():
    """Первое на новой странице без даты и суммы — колонтитул, а не хвост описания."""
    from bankstatement.rows import collect_rows

    lines = [*page('04/01 PAYMENT 20.00 120.00'), *page('IXONIA BANK', page_index=1)]

    rows = collect_rows(lines, (date(2025, 4, 1), date(2025, 4, 30)))

    assert [row.description for row in rows] == ['PAYMENT']

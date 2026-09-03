from datetime import date
from decimal import Decimal

from bankstatement.header import (
    Repair,
    _pick_repair,
    build_account,
    build_summary,
    collect_label_hits,
    display_case,
    find_account_last4,
    find_bank,
    find_period,
    is_activity_row,
)
from bankstatement.layout import group_lines
from bankstatement.models import Summary
from tests.conftest import page, text_line, word


def test__build_summary__labels_left_of_amounts__parsed():
    """Макет Great Lakes: подпись слева, сумма справа."""
    lines = page(
        'Beginning balance $2,014,882.47',
        'Deposits and credits $2,180,764.90',
        'Withdrawals and debits $2,154,309.25',
        'Ending balance $2,041,338.12',
    )
    summary = build_summary(lines)

    assert summary.beginning_balance == Decimal('2014882.47')
    assert summary.ending_balance == Decimal('2041338.12')
    assert summary.self_check() == 0


def test__build_summary__captions_above_amounts__matched_by_overlap():
    """Макет Fulton: подписи в одной строке, суммы под ними."""
    captions = [
        word('Prior Statement Balance', 50, 100),
        word('Total Deposits/Credits', 200, 100),
        word('Total Checks/Debits', 330, 100),
        word('Ending Statement Balance', 450, 100),
    ]
    # Каждая подпись — несколько слов, поэтому строим её вручную по координатам.
    captions = [
        *text_line('Prior Statement Balance', 100.0, x0=51.0),
        *text_line('Total Deposits/Credits', 100.0, x0=194.0),
        *text_line('Total Checks/Debits', 100.0, x0=325.0),
        *text_line('Ending Statement Balance', 100.0, x0=449.0),
    ]
    values = [
        word('$1,908,989.60', 71, 120),
        word('$4,351,230.63', 210, 120),
        word('$4,384,606.59', 336, 120),
        word('$1,875,613.64', 474, 120),
    ]
    summary = build_summary(group_lines([*captions, *values]))

    assert summary.beginning_balance == Decimal('1908989.60')
    assert summary.deposits_total == Decimal('4351230.63')
    assert summary.withdrawals_total == Decimal('4384606.59')
    assert summary.self_check() == 0


def test__build_summary__activity_row_looks_like_label__ignored():
    """Fulton печатает '03/31 ENDING BALANCE FROM PRIOR STATEMENT' в теле выписки."""
    lines = page(
        'Beginning balance 100.00',
        'Deposits and credits 50.00',
        'Withdrawals and debits 30.00',
        'Ending balance 120.00',
        '03/31 ENDING BALANCE FROM PRIOR STATEMENT 999.99',
    )
    assert build_summary(lines).ending_balance == Decimal('120.00')


def test__build_summary__counts_printed_in_label__captured():
    lines = page('+ Deposits and Credits (81) $1,214,254.05')

    assert build_summary(lines).deposits_count == 81


def test__build_summary__decimal_point_shifted__repaired_by_invariant():
    """Точка съехала на разряд: '1200.00' вместо '120.00'."""
    lines = page(
        'Beginning balance 100.00',
        'Deposits and credits 50.00',
        'Withdrawals and debits 30.00',
        'Ending balance 1200.00',
    )
    summary = build_summary(lines)

    assert summary.ending_balance == Decimal('120.00')
    assert summary.self_check() == 0


def test__build_summary__every_total__carries_its_source():
    """Шапка — эталон, которым проверяется всё остальное; без провенанса её не перепроверить.

    Рамка берётся у самого денежного токена: в выкладке «подпись над суммой»
    подпись и число стоят на разных строках, и показать пальцем нужно число.
    """
    lines = page(
        'Beginning balance 100.00',
        'Deposits and credits 50.00',
        'Withdrawals and debits 30.00',
        'Ending balance 120.00',
    )

    summary = build_summary(lines)

    assert set(summary.sources) == {
        'beginning_balance',
        'deposits_total',
        'withdrawals_total',
        'ending_balance',
    }
    assert all(source.bbox is not None and source.page == 1 for source in summary.sources.values())
    assert not any(source.repaired for source in summary.sources.values())


def test__build_summary__repaired_total__source_admits_the_repair():
    """Правка в шапке обязана быть видна: её перепроверят первой."""
    lines = page(
        'Beginning balance 100.00',
        'Deposits and credits 50.00',
        'Withdrawals and debits 30.00',
        'Ending balance 1200.00',
    )

    summary = build_summary(lines)

    assert summary.ending_balance == Decimal('120.00')
    assert summary.sources['ending_balance'].repaired
    assert summary.sources['ending_balance'].printed == '1200.00'
    assert not summary.sources['beginning_balance'].repaired


def test__build_summary__ambiguous_repair__left_as_printed():
    """Одну цифру можно поправить и в начальном, и в конечном остатке.

    Обе версии дают ноль, уверенность распознавания одинакова — значит выбирать
    нечем, и период честно уходит с расхождением.
    """
    lines = page(
        'Beginning Balance as of 08/01/2024 $279,107.10',
        '+ Deposits and Credits (83) $1,178,227.39',
        '- Withdrawals and Debits (88) $939,801.97',
        'Ending Balance as of 08/31/2024 $517,532.62',
    )
    summary = build_summary(lines)

    assert summary.ending_balance == Decimal('517532.62')
    assert summary.self_check() != 0


def test__build_summary__unexplainable_gap__left_as_printed():
    """Расхождение не похоже на опечатку — чинить нельзя, надо показать."""
    lines = page(
        'Beginning balance 100.00',
        'Deposits and credits 50.00',
        'Withdrawals and debits 30.00',
        'Ending balance 999.00',
    )
    summary = build_summary(lines)

    assert summary.ending_balance == Decimal('999.00')
    assert summary.self_check() != 0


def test__build_summary__no_labels__empty():
    assert build_summary(page('Nothing here')) == build_summary([])


def test__collect_label_hits__label_without_amount__no_hit():
    assert collect_label_hits(page('Beginning balance')) == []


def test__is_activity_row__leading_date__true():
    assert is_activity_row(page('04/01 CHECK 100.00')[0])
    assert not is_activity_row(page('Ending balance 100.00')[0])


def test__is_activity_row__caption_with_amount__not_an_operation():
    """'Deposits 12,345.67' — подпись итога, а не операция: даты впереди нет."""
    assert not is_activity_row(page('Deposits 12,345.67')[0])
    assert not is_activity_row(page('Total Deposits 12,345.67')[0])


def test__find_period__range_form__parsed():
    period = find_period(page('Statement Date: 04/01/21 through 04/30/21'))

    assert period is not None
    assert (period.start.month, period.end.day) == (4, 30)


def test__find_period__as_of_form__parsed():
    period = find_period(page('Beginning Balance as of 04/01/2025 $1.00', 'Ending Balance as of 04/30/2025 $2.00'))

    assert period is not None
    assert period.start.day == 1


def test__find_period__last_this_statement__starts_the_day_after():
    """Дата прошлой выписки — конец прошлого периода; этот начинается назавтра."""
    period = find_period(page('DECEMBER 31, 2024: LAST STATEMENT', 'JANUARY 31, 2025: THIS STATEMENT'))

    assert period is not None
    assert (period.start, period.end) == (date(2025, 1, 1), date(2025, 1, 31))


def test__find_period__year_only_on_the_second_side__taken_for_both():
    period = find_period(page('Statement: Apr 1 - Apr 30, 2025'))

    assert period is not None
    assert (period.start, period.end) == (date(2025, 4, 1), date(2025, 4, 30))


def test__find_period__day_before_month_name__parsed():
    """Индийские и британские банки печатают день перед месяцем."""
    period = find_period(page('Searched By From 01 Jan 2024 To 31 Mar 2024'))

    assert period is not None
    assert (period.start, period.end) == (date(2024, 1, 1), date(2024, 3, 31))


def test__find_period__numeric_day_first__inferred_from_the_dates_themselves():
    """'31-05-2026' читается только как 31 мая, и это переворачивает обе границы."""
    period = find_period(page('STATEMENT PERIOD', '01-03-2026 to 31-05-2026'))

    assert period is not None
    assert (period.start, period.end) == (date(2026, 3, 1), date(2026, 5, 31))


def test__find_period__boundaries_swapped__put_in_order():
    """Обратный период отсёк бы каждую операцию по дате, и выписка вышла бы пустой."""
    period = find_period(page('JANUARY 31, 2025: LAST STATEMENT', 'DECEMBER 31, 2024: THIS STATEMENT'))

    assert period is not None
    assert period.start < period.end


def test__find_period__nothing_recognisable__none():
    assert find_period(page('SOME TEXT')) is None


def test__find_bank__name_inside_address_line__extracted():
    assert find_bank(page('Fulton. Bank Lancaster, PA 17604')) == 'Fulton Bank'
    assert find_bank(page('P.O. Box 4140 BANK')) is None
    assert find_bank(page('GREAT LAKES COMMERCE BANK')) == 'Great Lakes Commerce Bank'


def test__find_bank__counterparty_in_an_operation_row__ignored():
    """Банк контрагента в описании платежа — не банк выписки."""
    assert find_bank(page('01/08 By Clg: DEL ACCTS-CITI BANK 3,206.08')) is None
    assert find_bank(page('Payment to First Bank 100.00')) is None


def test__find_bank__only_the_header_zone_counts__nothing_below_the_first_row():
    """Перенос описания без даты и суммы стоит уже в таблице, а не в шапке."""
    lines = page('Page 2', '01/12 Chq Paid-MICR Inward Clearing-RAHUL 6,233.58 16,202.33', 'CHAKRABORTY-HDFC BANK LTD.')

    assert find_bank(lines) is None
    assert find_bank(page('Progressive National Bank', '01/12 NEFT 6,233.58 16,202.33')) == 'Progressive National Bank'


def test__display_case__capitals__printed_case_with_acronyms_kept():
    assert display_case('GREAT LAKES COMMERCE BANK') == 'Great Lakes Commerce Bank'
    assert display_case('BANK OF AMERICA') == 'Bank of America'
    assert display_case('PNC BANK') == 'PNC Bank'
    assert display_case('HSBC BANK') == 'HSBC Bank'
    assert display_case('FIRST-CITIZENS BANK') == 'First-Citizens Bank'
    assert display_case('Ixonia Bank') == 'Ixonia Bank'


def test__find_account_last4__date_next_to_the_caption__not_taken_for_a_number():
    """В сетке реквизитов подпись счёта и период попадают в одну строку."""
    assert find_account_last4(page('Business Current Account 01-03-2026 to 31-05-2026')) is None
    assert find_account_last4(page('ACCOUNT NUMBER 01-03-2026 to 31-05-2026 XXXXXX7725')) == '7725'


def test__find_account_last4__caption_above_the_value__taken_from_the_line_below():
    """Сетка реквизитов: подпись в одной строке, значение под ней."""
    assert find_account_last4(page('ACCOUNT NUMBER BRANCH / IFSC', 'XXXXXX1067 Powai, Mumbai / MERI0002106')) == '1067'
    assert find_account_last4(page('ACCOUNT NUMBER BRANCH / IFSC INR 99,242.99', 'XXXXXX4821 Indiranagar')) == '4821'
    assert find_account_last4(page('ACCOUNT NUMBER', 'Statement period 01/01/2025')) is None
    assert find_account_last4(page('ACCOUNT NUMBER CUSTOMER CARE', 'Call us 877-367-5371')) is None


def test__find_account_last4__masked_forms__extracted():
    assert find_account_last4(page('Primary Account: XXXX1858')) == '1858'
    assert find_account_last4(page('Account ****4071')) == '4071'
    assert find_account_last4(page('ACCOUNT NUMBER 0011016426')) == '6426'
    assert find_account_last4(page('nothing')) is None


def test__build_account__full_header__filled():
    account = build_account(
        page('GREAT LAKES COMMERCE BANK', 'Account ****4071', 'Statement period: January 01, 2025 - January 28, 2025')
    )

    assert (account.bank, account.account_last4) == ('Great Lakes Commerce Bank', '4071')
    assert account.period is not None


def test__build_summary__nothing_recognisable__empty_not_invented():
    """Шапки нет — значит эталона нет. Придумывать его нельзя: на нём сверяют всё остальное."""
    assert build_summary(page('SOME TEXT', 'ANOTHER LINE')) == Summary()


def test__pick_repair__two_equally_trusted_candidates__nothing_changed():
    """Неоднозначную правку честнее не делать: молча неверное число хуже открытого расхождения."""
    summary = Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00'))
    repairs = [
        Repair('beginning_balance', Decimal('180.00'), 90.0),
        Repair('ending_balance', Decimal('40.00'), 90.0),
    ]

    assert _pick_repair(summary, repairs) == summary


def test__pick_repair__one_candidate_read_worse__it_is_the_one_corrected():
    """Чинится число, прочитанное хуже остальных, — уверенность движка и есть основание."""
    summary = Summary(beginning_balance=Decimal('100.00'), ending_balance=Decimal('120.00'))
    repairs = [
        Repair('beginning_balance', Decimal('180.00'), 41.0, printed='1S0.00'),
        Repair('ending_balance', Decimal('40.00'), 96.0),
    ]

    fixed = _pick_repair(summary, repairs)

    assert fixed.beginning_balance == Decimal('180.00')
    assert fixed.ending_balance == Decimal('120.00')


def test__find_period__short_without_year__none():
    """Короткая форма без года вроде 'Apr 1 - Apr 30' не может быть распознана без контекста."""
    period = find_period(page('Statement: April 1 - April 30'))

    # Без года и без других подсказок период не может быть определён
    assert period is None


def test__find_account_last4__not_from_date__extracted():
    """'Account Statement April 2025' не должна дать '2025' как номер счёта."""
    # Явное указание счёта работает
    last4 = find_account_last4(page('Account: ****4071'))
    assert last4 == '4071'

    # А из 'Account Statement April 2025' ничего не должно выйти
    nothing = find_account_last4(page('Account Statement April 2025'))
    assert nothing is None


def test__collect_label_hits__two_counters_in_one_line__each_label_gets_its_own():
    """Счётчик берётся между подписью и её суммой, а не первый попавшийся в строке."""
    from bankstatement.header import collect_label_hits

    hits = collect_label_hits(page('Deposits Total (3) 100.00 Withdrawals Total (5) 40.00'))
    counts = {hit.field: hit.count for hit in hits}

    assert counts['deposits_total'] == 3
    assert counts['withdrawals_total'] == 5


def test__filter_candidates__many_confident_copies_of_one_value__other_value_survives():
    """Колонтитул повторяет одно число на каждой странице; сводка с верным числом не должна выпасть."""
    from bankstatement.header import MAX_CANDIDATES_PER_FIELD, LabelHit, _filter_candidates
    from bankstatement.models import Source

    def hit(value, confidence, index):
        return LabelHit(
            field='ending_balance',
            value=Decimal(value),
            count=None,
            line_index=index,
            source=Source(page=1, confidence=confidence),
        )

    copies = [hit('999.99', 95.0, index) for index in range(MAX_CANDIDATES_PER_FIELD + 3)]
    kept = _filter_candidates([*copies, hit('100.00', 40.0, 99)])

    assert {h.value for h in kept} == {Decimal('999.99'), Decimal('100.00')}

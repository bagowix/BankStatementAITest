from datetime import date
from decimal import Decimal

import pytest

from bankstatement.money import (
    infer_day_first,
    is_money,
    normalise_money_token,
    parse_date,
    parse_money,
    plausible_misreading,
    reading_variants,
    repair_date_token,
    repair_money_token,
)


@pytest.mark.parametrize(
    ('token', 'expected'),
    [
        ('1,908,989.60', Decimal('1908989.60')),
        ('$-10.00', Decimal('-10.00')),
        ('-13,495.14', Decimal('-13495.14')),
        ('(1,234.56)', Decimal('-1234.56')),
        ('1,234.56-', Decimal('-1234.56')),
        ('0.00', Decimal('0.00')),
        ('597,068.70', Decimal('597068.70')),
    ],
)
def test__parse_money__bank_formats__parsed(token, expected):
    assert parse_money(token) == expected


@pytest.mark.parametrize('token', ['', 'abc', '1234', '12.3', '04/01', '1.234', 'No.25'])
def test__parse_money__non_money_token__none(token):
    assert parse_money(token) is None
    assert not is_money(token)


def test__normalise_money_token__dollar_read_as_letter__restored():
    """Renasant: 'PREVIOUS BALANCE S-10.00'."""
    assert normalise_money_token('S-10.00') == '$-10.00'
    assert normalise_money_token('Salary') == 'Salary'


def test__repair_money_token__slash_instead_of_seven__repaired_by_grouping():
    """Fulton: '2,173,5/6.64'. Разрядная сетка подтверждает правку."""
    assert parse_money('2,173,5/6.64') == Decimal('2173576.64')


def test__repair_money_token__grouping_breaks__left_alone():
    """'2,2/72,320.90' дало бы группу из четырёх цифр — значит правка неверна."""
    assert parse_money('2,2/72,320.90') is None
    assert repair_money_token('2,2/72,320.90') == '2,2/72,320.90'


def test__repair_money_token__comma_as_decimal_point__repaired():
    """Последняя группа из двух цифр — это центы, а не разряд."""
    assert parse_money('487,634,08') == Decimal('487634.08')


def test__reading_variants__swallowed_decimal_point__recovered():
    """Реальный баг Renasant: выносные точки слиплись с числом."""
    assert Decimal('363.80') in reading_variants('363580')


def test__reading_variants__section_sign_as_five__covered():
    """'§' на скане — и знак доллара, и пятёрка; обе версии должны быть в наборе."""
    variants = reading_variants('§99,438.47')

    assert Decimal('599438.47') in variants
    assert Decimal('99438.47') in variants


def test__reading_variants__clean_token__includes_itself():
    assert Decimal('1234.56') in reading_variants('1,234.56')


def test__reading_variants__limit__respected():
    assert len(reading_variants('1,111,111.11', limit=3)) <= 3


@pytest.mark.parametrize(
    ('printed', 'required', 'expected'),
    [
        (Decimal('517532.62'), Decimal('517532.52'), True),  # одна цифра
        (Decimal('533895.92'), Decimal('933895.92'), True),  # одна цифра в старшем разряде
        (Decimal('12.34'), Decimal('21.34'), True),  # перестановка соседних
        (Decimal('120.00'), Decimal('1200.00'), False),  # разная длина — не опечатка
        (Decimal('100.00'), Decimal('100.00'), False),  # ничего не изменилось
        (Decimal('123.45'), Decimal('987.45'), False),  # слишком далеко
    ],
)
def test__plausible_misreading__forms__classified(printed, required, expected):
    assert plausible_misreading(printed, required) is expected


@pytest.mark.parametrize(
    ('token', 'expected'),
    [
        ('04/01/2025', date(2025, 4, 1)),
        ('01/31/25', date(2025, 1, 31)),
        ('April 1, 2025', date(2025, 4, 1)),
        ('Apr 01', None),  # без контекста года не разрешается
        ('01 Jan 2024', date(2024, 1, 1)),
        ('01-Jan-2024', date(2024, 1, 1)),
        ('1 January 2024', date(2024, 1, 1)),
        ('April 1 2025', date(2025, 4, 1)),
    ],
)
def test__parse_date__absolute_forms__parsed(token, expected):
    assert parse_date(token) == expected


def test__parse_date__short_form__year_taken_from_period():
    period = (date(2025, 4, 1), date(2025, 4, 30))
    assert parse_date('Apr 02', period=period) == date(2025, 4, 2)
    assert parse_date('04/02', period=period) == date(2025, 4, 2)


def test__parse_date__december_january_boundary__year_inside_period():
    """Стык декабря и января: механический выбор года даёт ошибку на год."""
    period = (date(2024, 12, 15), date(2025, 1, 15))
    assert parse_date('12/20', period=period) == date(2024, 12, 20)
    assert parse_date('01/05', period=period) == date(2025, 1, 5)


def test__parse_date__date_outside_period__nearest_year():
    """'03/31' в апрельской выписке — остаток по предыдущему периоду."""
    period = (date(2025, 4, 1), date(2025, 4, 30))
    assert parse_date('03/31', period=period) == date(2025, 3, 31)


def test__parse_date__hyphen_form__parsed():
    period = (date(2025, 1, 1), date(2025, 1, 31))
    assert parse_date('01-02', period=period) == date(2025, 1, 2)


def test__parse_date__year_hint_only__used():
    assert parse_date('04/02', year_hint=2019) == date(2019, 4, 2)


@pytest.mark.parametrize(
    ('token', 'expected'),
    [
        ('O1-07', date(2025, 1, 7)),  # ноль прочитан как буква
        ('O1L-07', date(2025, 1, 7)),  # плюс мусорный глиф от линовки
        # Апрель вне январского периода: дата разбирается, но год берётся
        # ближайший — отбраковка чужих дат живёт в parse.py, а не здесь.
        ('~Apr02', date(2024, 4, 2)),
        ('AprO09', date(2024, 4, 9)),
    ],
)
def test__parse_date__ocr_damage__repaired(token, expected):
    period = (date(2024, 12, 31), date(2025, 1, 31))
    assert parse_date(token, period=period) == expected


def test__parse_date__month_glued_to_day__parsed():
    """Ixonia печатает 'Apr01' и 'AprO09' — буква внутри числа."""
    period = (date(2025, 4, 1), date(2025, 4, 30))
    assert parse_date('Apr01', period=period) == date(2025, 4, 1)
    assert parse_date('AprO09', period=period) == date(2025, 4, 9)
    assert parse_date('§=Apr08', period=period) == date(2025, 4, 8)


@pytest.mark.parametrize('token', ['Marketing 12', 'CHECK', 'A/C', '1-800-FULTON-4', 'XRO1DD', '1693'])
def test__parse_date__not_a_date__none(token):
    assert parse_date(token, period=(date(2025, 4, 1), date(2025, 4, 30))) is None


def test__repair_date_token__long_token__untouched():
    assert repair_date_token('1-800-FULTON-4') == '1-800-FULTON-4'


def test__parse_date__empty__none():
    assert parse_date('  ') is None
    assert parse_date('~') is None


def test__parse_date__iso_format__parsed():
    """ISO-форма 2025-04-01."""
    assert parse_date('2025-04-01') == date(2025, 4, 1)
    assert parse_date('2025-01-31') == date(2025, 1, 31)
    assert parse_date('2024-12-25') == date(2024, 12, 25)


def test__parse_date__september_abbr__parsed():
    """Поддержка неофициального сокращения 'Sept' (кроме стандартного 'Sep')."""
    period = (date(2025, 9, 1), date(2025, 9, 30))
    assert parse_date('Sept 15', period=period) == date(2025, 9, 15)
    assert parse_date('Sep 15', period=period) == date(2025, 9, 15)


def test__parse_date__day_first_order__parsed():
    """Поддержка порядка день-месяц через параметр day_first."""
    period = (date(2025, 4, 1), date(2025, 4, 30))
    # С day_first=False (по умолчанию): 25/04 -> месяц 25, день 4 (невозможно)
    # С day_first=True: 25/04 -> день 25, месяц 4 (апрель 25)
    assert parse_date('25/04', period=period, day_first=False) is None
    assert parse_date('25/04', period=period, day_first=True) == date(2025, 4, 25)
    # С датой, в пределах периода:
    # С day_first=False: 04/20 -> месяц 4, день 20 (апрель 20)
    # С day_first=True: 04/20 -> день 4, месяц 20 (невозможно)
    assert parse_date('04/20', period=period, day_first=False) == date(2025, 4, 20)
    assert parse_date('04/20', period=period, day_first=True) is None


@pytest.mark.parametrize(
    ('token', 'expected'),
    [
        ('£1,234.56', Decimal('1234.56')),
        ('₹1,234.56', Decimal('1234.56')),
        ('€10.00', Decimal('10.00')),
        ('-£5.00', Decimal('-5.00')),
    ],
)
def test__parse_money__other_currency_signs__parsed(token, expected):
    """Британские и индийские выписки печатают свой знак валюты перед числом."""
    assert is_money(token)
    assert parse_money(token) == expected


def test__infer_day_first__day_over_twelve__day_first():
    assert infer_day_first(['01-03-2026', 'RTGS', '31-05-2026']) is True


def test__infer_day_first__month_first_evidence__stays_us_order():
    assert infer_day_first(['04/20/2025', '04/01', 'DEPOSIT']) is False


def test__infer_day_first__no_evidence__us_order():
    assert infer_day_first(['04/01', '05/02', 'nothing']) is False

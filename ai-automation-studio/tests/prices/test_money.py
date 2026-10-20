import time
from decimal import Decimal

import pytest

from autostudio.prices.money import (
    MAX_PRICE_TEXT,
    ParsedPrice,
    Qualifier,
    bounded_amount,
    collapse_lines,
    collapse_spaces,
    find_price_mentions,
    normalize_currency,
    parse_price,
    parse_schema_amount,
    resolve_currency,
)

EXACT = Qualifier.EXACT

# (текст витрины, сумма, валюта, квалификатор) — ловушки реальных магазинов.
CASES: list[tuple[str, str, str | None, Qualifier]] = [
    ('1 299,00 ₾', '1299.00', 'GEL', EXACT),
    ('1 299,00 ₾', '1299.00', 'GEL', EXACT),  # NBSP
    ('1 299,00 ₾', '1299.00', 'GEL', EXACT),  # узкий NBSP
    ('1 299 ₾', '1299', 'GEL', EXACT),  # тонкий пробел
    ('₾1,299.00', '1299.00', 'GEL', EXACT),
    ('1299 GEL', '1299', 'GEL', EXACT),
    ('1 299 ლ', '1299', 'GEL', EXACT),
    ('1 299 ლარი', '1299', 'GEL', EXACT),
    ('$1,299.99', '1299.99', 'USD', EXACT),
    ('US$ 15', '15', 'USD', EXACT),
    ('1.299,99 €', '1299.99', 'EUR', EXACT),
    ('EUR 12,50', '12.50', 'EUR', EXACT),
    ('12.5', '12.5', None, EXACT),
    ('1,299', '1299', None, EXACT),  # одиночная запятая + ровно три цифры → тысячи
    ('12,99', '12.99', None, EXACT),  # две цифры → дробь
    ('1.299', '1299', None, EXACT),  # то же правило для точки (неоднозначность задокументирована)
    ('12.500 ₽', '12500', 'RUB', EXACT),
    ('0,500', '0.500', None, EXACT),  # ведущий ноль не бывает группой тысяч
    ('1,299,000.50', '1299000.50', None, EXACT),
    ('1.299.000 ₸', '1299000', 'KZT', EXACT),
    ("1'299.95 CHF", '1299.95', None, EXACT),  # швейцарский апостроф; CHF не в списке — валюта не угадывается
    ('£9.99', '9.99', 'GBP', EXACT),
    ('2 500 ₴', '2500', 'UAH', EXACT),
    ('2 500 грн.', '2500', 'UAH', EXACT),
    ('1.299,99 TL', '1299.99', 'TRY', EXACT),
    ('1 299,99 ₺', '1299.99', 'TRY', EXACT),
    ('15 000 ֏', '15000', 'AMD', EXACT),
    ('120 AMD', '120', 'AMD', EXACT),
    ('45,90 ₼', '45.90', 'AZN', EXACT),
    ('45.90 AZN', '45.90', 'AZN', EXACT),
    ('1 299 руб.', '1299', 'RUB', EXACT),
    ('1 299 рублей', '1299', 'RUB', EXACT),
    ('999 р.', '999', 'RUB', EXACT),
    ('1299 RUR', '1299', 'RUB', EXACT),
    ('from 1 200 ₽', '1200', 'RUB', Qualifier.FROM),
    ('от 1 200', '1200', None, Qualifier.FROM),
    ('Цена от 1 200 ₽', '1200', 'RUB', Qualifier.FROM),
    ('from $10', '10', 'USD', Qualifier.FROM),
    ('100 ლარიდან', '100', 'GEL', Qualifier.FROM),
    ('10–20', '10', None, Qualifier.RANGE),
    ('10 - 20 GEL', '10', 'GEL', Qualifier.RANGE),
    ('$10 – $20', '10', 'USD', Qualifier.RANGE),
    ('от 10 до 20 ₾', '10', 'GEL', Qualifier.RANGE),
    ('12,- €', '12', 'EUR', EXACT),
    ('Price: 2 999 ₾ incl. VAT', '2999', 'GEL', EXACT),
    ('iPhone 15 128GB — 2 999 ₾', '2999', 'GEL', EXACT),  # число с валютой важнее модели и объёма
    ('Shower gel 250ml 12,50 ₾', '12.50', 'GEL', EXACT),  # «gel» строчными — не валюта
    ('Скидка -15% 849 ₾', '849', 'GEL', EXACT),
    ('Скидка 15 % — 849', '849', None, EXACT),
    ('Цена 1 299 (в лари, GEL)', '1299', 'GEL', EXACT),  # одна валюта на весь текст
    ('1299GEL', '1299', 'GEL', EXACT),
    ('12.50€', '12.50', 'EUR', EXACT),
    ('From: 1 200 ₾', '1200', 'GEL', Qualifier.FROM),  # двоеточие после «from»
    ('Цена от: 1 200 ₽', '1200', 'RUB', Qualifier.FROM),
    ('4 999 ₾ − 10%', '4999', 'GEL', EXACT),  # бейдж скидки после цены — не диапазон
    ('4 999 ₾ -15 %', '4999', 'GEL', EXACT),
    ('12.50000 €', '12.5', 'EUR', EXACT),  # нули в хвосте дроби — та же цена
    ('AirPods Pro 2\n729 ₾', '729', 'GEL', EXACT),  # перевод строки — граница элемента, не тысячи
    ('1 299\n₾', '1299', 'GEL', EXACT),  # знак валюты в соседнем элементе
]


@pytest.mark.parametrize(('text', 'amount', 'currency', 'qualifier'), CASES)
def test__parse_price__real_formats__decimal_and_currency(text, amount, currency, qualifier):
    parsed = parse_price(text)
    assert parsed is not None, text
    assert parsed.amount == Decimal(amount)
    assert isinstance(parsed.amount, Decimal)
    assert parsed.currency == currency
    assert parsed.qualifier is qualifier


def test__parse_price__case_count__at_least_thirty():
    assert len(CASES) >= 30


@pytest.mark.parametrize('text', ['', 'нет в наличии', 'Out of stock', 'SKU 4006381333931', 'Model A15', '-15%'])
def test__parse_price__no_price__none(text):
    assert parse_price(text) is None


@pytest.mark.parametrize('text', ['Free', 'Бесплатно', 'უფასო', '0 ₾', '0,00 €'])
def test__parse_price__free_not_allowed__none(text):
    assert parse_price(text) is None


@pytest.mark.parametrize('text', ['Free', 'Бесплатно', '0 ₾'])
def test__parse_price__free_allowed__zero_flagged(text):
    parsed = parse_price(text, allow_free=True)
    assert parsed is not None
    assert parsed.amount == 0
    assert parsed.qualifier is Qualifier.FREE


def test__parse_price__old_and_new_price__first_with_currency_wins():
    # Селектор должен указывать на новую цену; парсер честно берёт первую с валютой.
    parsed = parse_price('1 499 ₾ 1 299 ₾')
    assert parsed is not None
    assert parsed.amount == Decimal(1499)


def test__parse_price__ambiguous_dollar__token_kept():
    parsed = parse_price('$12')
    assert parsed is not None
    assert parsed.currency_token == '$'


def test__parse_price__georgian_word_with_la_letter__not_currency():
    # «ლ» внутри слова — обычная буква, а не лари.
    parsed = parse_price('ფასი 50')
    assert parsed is not None
    assert parsed.currency is None


def test__find_price_mentions__span__includes_currency_in_original_text():
    text = 'Цена: 1 299,00 ₾ сегодня'
    mention = find_price_mentions(text)[0]
    assert text[mention.start : mention.end] == '1 299,00 ₾'
    assert mention.currency == 'GEL'


def test__find_price_mentions__huge_number__skipped():
    assert find_price_mentions('ID 12345678901') == []


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('1299.00', Decimal('1299.00')),
        ('1.299', Decimal('1.299')),  # schema.org: точка — всегда десятичная
        (1299, Decimal(1299)),
        (Decimal('12.5'), Decimal('12.5')),
        ('1 299,00', Decimal('1299.00')),  # «по-витринному» — разбирается как текст
        ('0', None),
        ('0.00', None),
        ('', None),
        ('call us', None),
        (None, None),
        (True, None),
        (Decimal('NaN'), None),
        (Decimal('1e12'), None),
    ],
)
def test__parse_schema_amount__cases(value, expected):
    assert parse_schema_amount(value) == expected


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('GEL', 'GEL'),
        (' gel ', 'GEL'),
        ('₾', 'GEL'),
        ('US$', 'USD'),
        ('CAD', 'CAD'),
        ('RUR', 'RUB'),
        ('ლარი', 'GEL'),
        ('', None),
        (None, None),
        ('dollars', None),
    ],
)
def test__normalize_currency__cases(value, expected):
    assert normalize_currency(value) == expected


def _parsed(currency: str | None, token: str | None) -> ParsedPrice:
    return ParsedPrice(amount=Decimal(1), currency=currency, currency_token=token)


def test__resolve_currency__structured__wins_over_config():
    assert resolve_currency(structured='USD', parsed=_parsed('GEL', '₾'), declared='GEL') == 'USD'


def test__resolve_currency__dollar_sign_and_declared__declared_wins():
    assert resolve_currency(structured=None, parsed=_parsed('USD', '$'), declared='CAD') == 'CAD'


def test__resolve_currency__explicit_symbol_conflicts_with_config__page_wins():
    # Магазин показал евро — значит, сравнивать с лари нельзя, а не «исправлять» валюту.
    assert resolve_currency(structured=None, parsed=_parsed('EUR', '€'), declared='GEL') == 'EUR'


def test__resolve_currency__nothing_on_page__declared():
    assert resolve_currency(structured=None, parsed=_parsed(None, None), declared='GEL') == 'GEL'
    assert resolve_currency(structured=None, parsed=None, declared=None) is None


def test__collapse_spaces__nbsp_and_newlines__single_spaces():
    assert collapse_spaces(' a  b\n\tc ') == 'a b c'


def test__collapse_lines__lines_kept_spaces_squeezed_empty_dropped():
    assert collapse_lines(' a\u00a0 b \n\n  \r\n c\td ') == 'a b\nc d'


def test__find_price_mentions__number_across_line_break__not_glued():
    text = 'AirPods Pro 2\n729 ₾'
    assert [(m.amount, m.currency) for m in find_price_mentions(text)] == [(Decimal(2), None), (Decimal(729), 'GEL')]


def test__find_price_mentions__symbol_on_next_line_owned_by_its_line__not_stolen():
    # «$» стоит перед 799 в своей строке: числу «15» из заголовка он не достаётся.
    mentions = find_price_mentions('iPhone 15\n$799')
    assert [(m.amount, m.currency) for m in mentions] == [(Decimal(15), None), (Decimal(799), 'USD')]
    assert (mentions[1].number_start, mentions[1].number_end) == (11, 14)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('iPhone 15 $799', [(Decimal(15), None), (Decimal(799), 'USD')]),  # «$» ближе к 799
        ('Pixel 8\n$\n649', [(Decimal(8), None), (Decimal(649), 'USD')]),  # «$» — знак перед числом
        ('729\nლ\n2', [(Decimal(729), 'GEL'), (Decimal(2), None)]),  # «ლ» — после числа
        ('iPhone 15\n₾\n1,299.00', [(Decimal(15), None), (Decimal('1299.00'), None)]),  # неясно — ничей
        ('1 499 ₾ 1 299 ₾', [(Decimal(1499), 'GEL'), (Decimal(1299), 'GEL')]),
    ],
)
def test__find_price_mentions__sign_between_two_numbers__one_owner(text, expected):
    assert [(m.amount, m.currency) for m in find_price_mentions(text)] == expected


def test__find_price_mentions__dense_page__linear_time():
    # Раньше каждый знак валюты сравнивался с каждым числом: 8000 цен — 80 секунд.
    started = time.perf_counter()
    mentions = find_price_mentions('1 ₾ ' * 20_000)
    assert len(mentions) == 20_000
    assert time.perf_counter() - started < 3


@pytest.mark.parametrize(
    'text',
    ['1 299 ₾ 12345678901234567890 ₾', '12.123456 ₾', '0.' + '0' * 50 + '1 ₾', '9' * 5000 + ' ₾'],
    ids=['barcode', 'six-decimals', 'tiny', 'five-thousand-digits'],
)
def test__find_price_mentions__absurd_numbers__skipped_without_huge_decimals(text):
    mentions = find_price_mentions(text)
    assert all(m.amount <= Decimal('999999999') for m in mentions)
    assert all(len(format(m.amount, 'f')) < 20 for m in mentions)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (Decimal('1E-999999999'), None),  # JSON-LD «1e-999999999»: в ячейке — гигабайт нулей
        (Decimal('0.000001'), None),
        (Decimal('1E+999999999'), None),
        (Decimal('-5'), None),
        (Decimal('Infinity'), None),
        (Decimal('12.50000'), Decimal('12.5000')),
        (Decimal('0.0001'), Decimal('0.0001')),
        (Decimal('1E+3'), Decimal(1000)),
    ],
)
def test__bounded_amount__limits(value, expected):
    assert bounded_amount(value) == expected


def test__bounded_amount__zero_only_when_allowed():
    assert bounded_amount(Decimal(0)) is None
    assert bounded_amount(Decimal(0), allow_zero=True) == 0


@pytest.mark.parametrize(
    'value',
    [Decimal('1E-999999999'), Decimal('0.000001'), '1299 ₾ ' + 'x' * MAX_PRICE_TEXT, '1e-60000', '1E-999999999'],
)
def test__parse_schema_amount__hostile_values__none(value):
    assert parse_schema_amount(value) is None


def test__parse_schema_amount__exponent_string__strict_decimal():
    assert parse_schema_amount('1e3') == Decimal(1000)


def test__normalize_currency__long_text__none():
    assert normalize_currency('GEL ' * 100) is None

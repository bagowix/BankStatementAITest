import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from autostudio.errors import ConfigError
from autostudio.office.money import (
    currency_code,
    format_amount,
    format_money,
    invoice_currency,
    money,
    parse_amount,
    parse_month,
    shift_months,
    tbilisi_today,
)
from autostudio.office.operator import (
    OPERATOR_ENV,
    OperatorProfile,
    group_iban,
    iban_problem,
    is_valid_iban,
    load_operator,
    operator_path,
    swift_problem,
)
from tests.office.helpers import OPERATOR_TOML, VALID_IBAN

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- деньги -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('1250', Decimal('1250')),
        ('1250.50', Decimal('1250.50')),
        (' 1 250.5 ', Decimal('1250.5')),
        ('0.1', Decimal('0.1')),
    ],
)
def test__parse_amount__plain_numbers__exact_decimal(text, expected):
    value = parse_amount(text, what='--amount')
    assert value == expected
    assert isinstance(value, Decimal)


@pytest.mark.parametrize('text', ['1,250.00', '1250,5', '-5', 'abc', '1e3', 'NaN', '', '1.2.3'])
def test__parse_amount__ambiguous_or_bad__config_error(text):
    with pytest.raises(ConfigError, match='--amount'):
        parse_amount(text, what='--amount')


def test__parse_amount__zero__error_unless_allowed():
    with pytest.raises(ConfigError, match='ноль'):
        parse_amount('0', what='--amount')
    assert parse_amount('0', what='--fee', allow_zero=True) == 0


def test__parse_amount__too_many_places__config_error():
    with pytest.raises(ConfigError, match='2 знаков'):
        parse_amount('10.005', what='--amount', places=2)
    assert parse_amount('10.05', what='--amount', places=2) == Decimal('10.05')


def test__parse_amount__decimal_and_int_inputs__kept_exact():
    assert parse_amount(Decimal('0.30'), what='x') == Decimal('0.30')
    assert parse_amount(7, what='x') == Decimal(7)
    with pytest.raises(ConfigError, match='неотрицательное'):
        parse_amount(Decimal('-1'), what='x')
    with pytest.raises(ConfigError, match='неотрицательное'):
        parse_amount(Decimal('Infinity'), what='x')


def test__money__half_up__not_bankers_rounding():
    # Банковское округление дало бы 0.12 и 1.00 — бухгалтер ждёт «половина вверх».
    assert money(Decimal('0.125')) == Decimal('0.13')
    assert money(Decimal('1.005')) == Decimal('1.01')
    assert money(Decimal('2')) == Decimal('2.00')


def test__format_amount__en_and_ru__separators():
    assert format_amount(Decimal('1234567.5'), 'en') == '1,234,567.50'
    assert format_amount(Decimal('1234567.5'), 'ru') == '1 234 567,50'
    assert format_money(Decimal('99'), 'USD', 'en') == 'USD 99.00'
    assert format_money(Decimal('99'), 'GEL', 'ru') == '99,00 GEL'


def test__currency_code__lowercase__normalized_and_checked():
    assert currency_code('usd') == 'USD'
    with pytest.raises(ConfigError, match='трёх букв'):
        currency_code('US$')
    assert invoice_currency('gel') == 'GEL'
    with pytest.raises(ConfigError, match='допустимы'):
        invoice_currency('GBP')


def test__parse_month__valid_and_invalid():
    assert parse_month('2026-10') == dt.date(2026, 10, 1)
    for bad in ('2026-13', '10/2026', '2026-1'):
        with pytest.raises(ConfigError, match='ГГГГ-ММ'):
            parse_month(bad)


def test__shift_months__month_ends_and_year_boundaries():
    assert shift_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
    assert shift_months(dt.date(2024, 3, 31), -1) == dt.date(2024, 2, 29)
    assert shift_months(dt.date(2026, 12, 15), 1) == dt.date(2027, 1, 15)
    assert shift_months(dt.date(2026, 10, 5), -12) == dt.date(2025, 10, 5)


def test__tbilisi_today__late_utc_evening__next_day_in_tbilisi():
    assert tbilisi_today(dt.datetime(2026, 10, 31, 21, 30, tzinfo=dt.UTC)) == dt.date(2026, 11, 1)
    with pytest.raises(ValueError, match='поясом'):
        tbilisi_today(dt.datetime(2026, 10, 31, 21, 30))  # noqa: DTZ001 - проверяем отказ от наивного времени


# --- IBAN и SWIFT --------------------------------------------------------------------------


@pytest.mark.parametrize(
    'iban',
    [
        VALID_IBAN,
        'ge07 xx00 0000 0000 0000 00',
        'GB82WEST12345698765432',  # пример из ISO 13616 / реестра SWIFT
        'DE89370400440532013000',
        'BE68539007547034',
    ],
)
def test__iban__valid_checksum__accepted(iban):
    assert is_valid_iban(iban)
    assert iban_problem(iban) is None


@pytest.mark.parametrize(
    ('iban', 'why'),
    [
        ('GE00XX0000000000000000', 'контрольная сумма'),  # образец из operator.example.toml
        ('GE07XX0000000000000001', 'контрольная сумма'),  # опечатка в последней цифре
        ('GB82WEST12345698765423', 'контрольная сумма'),  # переставлены две цифры
        ('GE07XX00000000000000', 'нужно 22'),
        ('GE07120000000000000000', 'грузинский IBAN'),
        ('XX', 'код страны'),
        ('GE07-XX00-0000', 'код страны'),
    ],
)
def test__iban__invalid__problem_explained(iban, why):
    assert not is_valid_iban(iban)
    assert why in (iban_problem(iban) or '')


def test__group_iban__groups_of_four():
    assert group_iban('ge07xx0000000000000000') == 'GE07 XX00 0000 0000 0000 00'


@pytest.mark.parametrize('swift', ['XXXXGE22', 'xxxxge22abc', 'EXMPDEFF500'])
def test__swift__valid_shapes__accepted(swift):
    assert swift_problem(swift) is None


@pytest.mark.parametrize('swift', ['XXXX22', 'XXXXGE2', '1234GE22', 'XXXXGE22XX'])
def test__swift__bad_shapes__rejected(swift):
    assert swift_problem(swift) is not None


# --- профиль оператора ---------------------------------------------------------------------


def test__load_operator__valid_file__profile(operator_file):
    profile = load_operator(operator_file)
    assert profile.iban_compact == VALID_IBAN
    assert profile.iban_grouped == 'GE07 XX00 0000 0000 0000 00'
    assert profile.swift_code == 'XXXXGE22'
    assert profile.invoice_prefix == 'AS-'
    assert profile.default_currency == 'USD'
    assert profile.display_name('ru') == 'ИП Джейн Пример'
    assert profile.display_name('en') == 'Individual Entrepreneur Jane Example'
    assert profile.tax_note.text('ru').startswith('Статус малого бизнеса')


def test__person_name__no_owner_name__prefix_stripped(operator):
    assert operator.person_name('en') == 'Jane Example'
    assert operator.person_name('ru') == 'Джейн Пример'
    named = OperatorProfile(
        legal_name='Jane Example Studio',
        owner_name='Jane Q. Example',
        id_number='000000000',
        address='a',
        bank_name='b',
        iban=VALID_IBAN,
        swift='XXXXGE22',
        email='jane@example.com',
    )
    assert named.person_name('ru') == 'Jane Q. Example'
    bare = OperatorProfile(
        legal_name='Jane Example Studio',
        id_number='000000000',
        address='a',
        bank_name='b',
        iban=VALID_IBAN,
        swift='XXXXGE22',
        email='jane@example.com',
    )
    assert bare.person_name('en') == 'Jane Example Studio'


def test__load_operator__many_problems__all_listed_with_path(tmp_path):
    path = tmp_path / 'operator.toml'
    bad = (
        OPERATOR_TOML.replace(VALID_IBAN, 'GE00XX0000000000000000')
        .replace('XXXXGE22', 'BAD')
        .replace('"000000000"', '"12AB"')
        .replace('jane@example.com', 'jane-at-example')
        .replace('"jane_example"', '"@jane"')
    )
    path.write_text(bad + 'invoice_prefix = "as 2026"\n', encoding='utf-8')
    with pytest.raises(ConfigError) as caught:
        load_operator(path)
    message = str(caught.value)
    assert str(path) in message
    for fragment in ('IBAN', 'SWIFT', 'id_number', 'email', 'telegram', 'invoice_prefix'):
        assert fragment in message


def test__load_operator__missing_file__explains_example(tmp_path):
    with pytest.raises(ConfigError, match=r'operator\.example\.toml'):
        load_operator(tmp_path / 'operator.toml')


def test__load_operator__broken_toml_and_unknown_field__config_error(tmp_path):
    broken = tmp_path / 'broken.toml'
    broken.write_text('legal_name = "unterminated\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='битый TOML'):
        load_operator(broken)
    unknown = tmp_path / 'unknown.toml'
    unknown.write_text(OPERATOR_TOML + 'iban_typo = "x"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='iban_typo'):
        load_operator(unknown)


def test__operator_profile__blank_required_and_bad_field_keys__rejected():
    with pytest.raises(ConfigError) as caught:
        OperatorProfile(
            legal_name=' ',
            id_number='000000000',
            address='',
            bank_name='b',
            iban=VALID_IBAN,
            swift='XXXXGE22',
            email='jane@example.com',
            contract_fields={'server_location': 'x'},
        )
    assert 'legal_name: пусто' in str(caught.value)
    assert 'address: пусто' in str(caught.value)
    assert 'contract_fields' in str(caught.value)


def test__operator_path__flag_then_env_then_repo_root():
    assert operator_path(Path('a.toml'), {OPERATOR_ENV: 'b.toml'}) == Path('a.toml')
    assert operator_path(None, {OPERATOR_ENV: 'b.toml'}) == Path('b.toml')
    assert operator_path(None, {}) == Path('operator.toml')


def test__example_file__only_placeholder_iban_blocks_use(tmp_path):
    example = REPO_ROOT / 'operator.example.toml'
    with pytest.raises(ConfigError) as caught:
        load_operator(example)
    # Всё, кроме образцового IBAN, валидно: скопированный образец не выпустит счёт с выдуманным счётом.
    problems = str(caught.value).split(': ', 1)[1]
    assert 'IBAN' in problems
    assert ';' not in problems
    copy = tmp_path / 'operator.toml'
    copy.write_text(example.read_text(encoding='utf-8').replace('GE00XX0000000000000000', VALID_IBAN), encoding='utf-8')
    profile = load_operator(copy)
    assert profile.legal_name == 'Individual Entrepreneur Jane Example'
    assert profile.contract_fields['SERVER_LOCATION']

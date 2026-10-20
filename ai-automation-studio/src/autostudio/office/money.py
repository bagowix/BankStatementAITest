"""Деньги и даты офиса: только Decimal, округление до центов и тетри по ROUND_HALF_UP."""

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final, Literal
from zoneinfo import ZoneInfo

from autostudio.errors import ConfigError

type InvoiceCurrency = Literal['USD', 'EUR', 'GEL']
type Language = Literal['en', 'ru']

INVOICE_CURRENCIES: Final[tuple[InvoiceCurrency, ...]] = ('USD', 'EUR', 'GEL')
CENT: Final = Decimal('0.01')
# Счета, налог и порог считаются в датах Тбилиси: «сегодня» оператора, а не UTC.
TBILISI: Final = ZoneInfo('Asia/Tbilisi')

_CURRENCY: Final = re.compile(r'^[A-Z]{3}$')
_PLAIN_NUMBER: Final = re.compile(r'^\d+(\.\d+)?$')
_MONTH: Final = re.compile(r'^(\d{4})-(\d{2})$')


def money(value: Decimal) -> Decimal:
    """Округляет до двух знаков (центы, тетри) по правилу «половина вверх».

    Args:
        value: Сумма с любой точностью.

    Returns:
        Сумма с ровно двумя знаками после точки.
    """
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(
    text: str | Decimal | int, *, what: str, allow_zero: bool = False, places: int | None = None
) -> Decimal:
    """Читает неотрицательное число без float: «1250», «1250.50», «1 250.50».

    Запятая не принимается вовсе: «1,250» — это 1250 для американца и 1,25 для
    грузинского бухгалтера, а ошибка в сумме счёта дороже лишнего вопроса.

    Args:
        text: Строка из флага или значение из TOML (там дробные уже Decimal).
        what: Как поле называется в сообщении об ошибке (`--amount`).
        allow_zero: Разрешён ли ноль.
        places: Сколько знаков после точки допустимо; None — сколько угодно.

    Returns:
        Конечное неотрицательное Decimal.

    Raises:
        ConfigError: Не число, запятая, отрицательное, бесконечность или ноль, где он запрещён.
    """
    if isinstance(text, Decimal):
        value = text
    elif isinstance(text, int):
        value = Decimal(text)
    else:
        cleaned = text.strip().replace(' ', '').replace(' ', '').replace('_', '')
        if ',' in cleaned:
            raise ConfigError(f'{what}={text!r}: запятая неоднозначна — пишите точку и без разделителей: 1250.50')
        if not _PLAIN_NUMBER.match(cleaned):
            raise ConfigError(f'{what}={text!r}: ожидается неотрицательное число, например 1250.50')
        try:
            value = Decimal(cleaned)
        except InvalidOperation:  # pragma: no cover - регулярное выражение уже отсекло нечисла
            raise ConfigError(f'{what}={text!r}: не число') from None
    if not value.is_finite() or value < 0:
        raise ConfigError(f'{what}={text!r}: ожидается неотрицательное конечное число')
    if value == 0 and not allow_zero:
        raise ConfigError(f'{what}: ноль здесь не имеет смысла')
    exponent = value.as_tuple().exponent
    if places is not None and isinstance(exponent, int) and -exponent > places:
        raise ConfigError(f'{what}={text!r}: не больше {places} знаков после точки')
    return value


def currency_code(text: str, *, what: str = '--currency') -> str:
    """Проверяет трёхбуквенный код валюты ISO 4217 и приводит к верхнему регистру.

    Args:
        text: Код, например `usd`.
        what: Имя поля для сообщения об ошибке.

    Returns:
        Код в верхнем регистре.

    Raises:
        ConfigError: Не три латинские буквы.
    """
    code = text.strip().upper()
    if not _CURRENCY.match(code):
        raise ConfigError(f'{what}={text!r}: ожидается код валюты из трёх букв (USD, EUR, GEL)')
    return code


def invoice_currency(text: str) -> InvoiceCurrency:
    """Валюта счёта: только USD, EUR или GEL — те, что принимает счёт ИП.

    Args:
        text: Код валюты.

    Returns:
        Код как литерал.

    Raises:
        ConfigError: Валюта не из списка.
    """
    code = currency_code(text)
    for allowed in INVOICE_CURRENCIES:
        if code == allowed:
            return allowed
    raise ConfigError(f'валюта счёта {code}: допустимы {", ".join(INVOICE_CURRENCIES)}')


def format_amount(value: Decimal, language: Language) -> str:
    """Сумма для людей: `1,250.50` по-английски и `1 250,50` по-русски.

    Args:
        value: Сумма.
        language: Язык документа.

    Returns:
        Строка с двумя знаками после разделителя.
    """
    text = format(money(value), ',.2f')
    if language == 'ru':
        # Неразрывный пробел: сумма не разорвётся между строками при печати.
        return text.replace(',', ' ').replace('.', ',')
    return text


def format_money(value: Decimal, currency: str, language: Language) -> str:
    """Сумма с валютой: `USD 1,250.50` / `1 250,50 USD`."""
    amount = format_amount(value, language)
    return f'{amount} {currency}' if language == 'ru' else f'{currency} {amount}'


def parse_month(text: str) -> dt.date:
    """Месяц `ГГГГ-ММ` → первое число месяца.

    Raises:
        ConfigError: Формат не `ГГГГ-ММ` или месяца нет.
    """
    match = _MONTH.match(text.strip())
    if match is None or not 1 <= int(match.group(2)) <= 12:
        raise ConfigError(f'месяц {text!r}: ожидается ГГГГ-ММ, например 2026-10')
    return dt.date(int(match.group(1)), int(match.group(2)), 1)


def shift_months(day: dt.date, months: int) -> dt.date:
    """Тот же день через `months` месяцев; 31-е в коротком месяце становится последним днём.

    Args:
        day: Исходная дата.
        months: Сдвиг, может быть отрицательным.

    Returns:
        Сдвинутая дата.
    """
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    next_month = dt.date(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_month - dt.timedelta(days=1)).day
    return dt.date(year, month, min(day.day, last_day))


def tbilisi_today(now: dt.datetime) -> dt.date:
    """Сегодняшняя дата в Тбилиси для момента с часовым поясом."""
    if now.tzinfo is None:
        raise ValueError('ожидается время с часовым поясом')
    return now.astimezone(TBILISI).date()

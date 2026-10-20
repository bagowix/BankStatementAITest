"""Разбор цены из текста витрины: «1 299,00 ₾», «$1,299.99», «1.299,99 €», «от 1 200 ₽».

Чистые функции без ввода-вывода и без float: сумма собирается из цифр строки сразу в
`Decimal`, иначе 0.1 + 0.2 однажды покажет клиенту «изменение цены» на копейку.

Правило разделителей (и его неоднозначность):

- пробелы всех видов (NBSP, узкий NBSP, тонкий) и апостроф — всегда разделители тысяч;
- если в числе есть и точка, и запятая, последняя из них — десятичная, первая — тысячи
  («1.299,99» и «1,299.99» → 1299.99);
- одиночный разделитель, за которым ровно три цифры и нет разделителя другого вида, —
  это тысячи («1,299» → 1299, «1.299» → 1299); иначе это десятичная точка («12,99», «12.5»);
- исключение: ведущий ноль («0,500») группой тысяч быть не может — это дробь.

Неоднозначность честная: «1.299» в валюте с тремя знаками после точки (KWD, BHD) было бы
1.299, но это не наш рынок. Поэтому, где страница даёт машинное значение schema.org
(`"price": "1299.00"`, `content="1299.00"`), оно разбирается строго (`parse_schema_amount`)
и предпочитается тексту витрины.
"""

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

import msgspec

# Всё, что браузер рисует как пробел, приводится к обычному пробелу один-к-одному:
# длина строки не меняется, и позиции найденной цены совпадают с исходным текстом
# (на этом стоит проверка цитаты модели).
_SPACES: Final = str.maketrans(dict.fromkeys('          \t\n\r\f\v', ' '))

# Цена больше миллиарда на витрине малого бизнеса — это штрихкод, артикул или телефон.
MAX_AMOUNT: Final = Decimal('999999999')

_NUMBER: Final = re.compile(
    r"""
    (?<![\d.,'’])
    (?:
        (?P<lead>\d{1,3})(?P<sep>[ '’.,])(?P<groups>\d{3}(?:(?P=sep)\d{3})*)   # группы по три цифры
        (?:(?!(?P=sep))[.,](?P<gfrac>\d+))?                                    # дробь другим знаком
      |
        (?P<int>\d+)(?:[.,](?P<frac>\d+))?
    )
    (?P<dash>[.,][-–])?   # «12,-» — целая цена в швейцарской и немецкой записи
    (?!\d)
    """,
    re.VERBOSE,
)

_ISO_CODES: Final[dict[str, str]] = {
    'USD': 'USD',
    'EUR': 'EUR',
    'GBP': 'GBP',
    'GEL': 'GEL',
    'RUB': 'RUB',
    'RUR': 'RUB',
    'UAH': 'UAH',
    'KZT': 'KZT',
    'TRY': 'TRY',
    'TL': 'TRY',
    'AMD': 'AMD',
    'AZN': 'AZN',
}

_SYMBOLS: Final[dict[str, str]] = {
    'US$': 'USD',
    '$': 'USD',
    '€': 'EUR',
    '£': 'GBP',
    '₾': 'GEL',
    '₽': 'RUB',
    '₴': 'UAH',
    '₸': 'KZT',
    '₺': 'TRY',
    '֏': 'AMD',
    '₼': 'AZN',
}

# Знак, который делят несколько валют: «$» — это и USD, и CAD, и AUD. Для него
# валюта, подтверждённая оператором в конфиге, важнее догадки по символу.
AMBIGUOUS_TOKENS: Final = frozenset({'$'})

# Коды — только заглавными: «Shower gel 250 ml» в магазине косметики не должен стать лари.
# Грузинская «ლ» — обычная буква алфавита, поэтому валютой она считается, только стоя
# отдельно (или словом «ლარი» с окончаниями, включая «ლარიდან» — «от … лари»).
_CURRENCY: Final = re.compile(
    r'(?P<code>(?<![A-Za-z])(?:USD|EUR|GBP|GEL|RUB|RUR|UAH|KZT|TRY|TL|AMD|AZN)(?![A-Za-z]))'
    r'|(?P<sym>US\$|[$€£₾₽₴₸₺֏₼])'
    r'|(?P<lari>(?<![ა-ჿ])(?:ლარ[ა-ჿ]*|ლ)(?![ა-ჿ]))'
    r'|(?P<rub>(?<![а-яёА-ЯЁ])(?:[Рр]уб(?:л[а-яё]*)?\.?|р\.)(?![а-яё]))'
    r'|(?P<uah>(?<![а-яёіїєА-ЯЁІЇЄ])грн\.?(?![а-яёіїє]))'
)

_FREE: Final = re.compile(r'(?<!\w)(?:free|бесплатно|gratis|kostenlos|უფასო)(?!\w)', re.IGNORECASE)
_FROM: Final = re.compile(r'(?:^|[\s(:])(?:from|starting at|от|ab|desde|dès|à partir de)\s*$', re.IGNORECASE)
_RANGE: Final = re.compile(r'^\s*(?:[-–—‒−]|\.\.\.?|…|to|до)\s*(?:US\$|[$€£₾₽₴₸₺֏₼])?\s*\d', re.IGNORECASE)
_SCHEMA_NUMBER: Final = re.compile(r'^\d+(?:\.\d+)?$')
_RANGE_GAP: Final = re.compile(r'\s*(?:[-–—‒−]|\.\.\.?|…|to|до)\s*', re.IGNORECASE)
# Максимальный зазор между числом и знаком валюты, чтобы считать их одной ценой («1 299 ₾»).
_MAX_GAP: Final = 2


class Qualifier(StrEnum):
    """Что именно означает найденное число."""

    EXACT = 'exact'
    FROM = 'from'  # «от 1 200 ₽» — нижняя граница, а не цена конкретного товара
    RANGE = 'range'  # «10–20» — взята нижняя граница
    FREE = 'free'  # «Бесплатно» — ноль, только если это явно разрешено


class ParsedPrice(msgspec.Struct, frozen=True, kw_only=True):
    """Цена, прочитанная из текста.

    Attributes:
        amount: Сумма, всегда `Decimal`.
        currency: Код ISO 4217 или None, если валюта в тексте не названа.
        currency_token: Как валюта записана на странице (`$`, `₾`, `GEL`) — по нему видно,
            догадка это или явный код.
        qualifier: Точная цена, «от», нижняя граница диапазона или «бесплатно».
    """

    amount: Decimal
    currency: str | None
    currency_token: str | None = None
    qualifier: Qualifier = Qualifier.EXACT


class PriceMention(msgspec.Struct, frozen=True, kw_only=True):
    """Число-цена в тексте с позициями: цитата для проверки ответа модели.

    Attributes:
        start: Начало фрагмента (вместе с прилегающим знаком валюты).
        end: Конец фрагмента (не включая).
        amount: Сумма.
        currency: Код валюты рядом с числом или None.
        currency_token: Запись валюты на странице.
    """

    start: int
    end: int
    amount: Decimal
    currency: str | None
    currency_token: str | None


class _Token(msgspec.Struct, frozen=True):
    start: int
    end: int
    text: str
    code: str


def _currency_tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _CURRENCY.finditer(text):
        token = match.group(0)
        if match.lastgroup == 'code':
            code = _ISO_CODES[token]
        elif match.lastgroup == 'sym':
            code = _SYMBOLS[token]
        elif match.lastgroup == 'lari':
            code = 'GEL'
        elif match.lastgroup == 'rub':
            code = 'RUB'
        else:
            code = 'UAH'
        tokens.append(_Token(match.start(), match.end(), token, code))
    return tokens


def _amount(match: re.Match[str]) -> Decimal | None:
    if match.group('lead') is not None:
        lead, groups, frac = match.group('lead'), match.group('groups'), match.group('gfrac')
        sep = match.group('sep')
        if lead == '0' and sep in '.,' and len(groups) == 3 and frac is None:
            # «0,500»: группа тысяч не начинается с нуля — значит, это дробь.
            digits = f'0.{groups}'
        else:
            digits = lead + groups.replace(sep, '') + (f'.{frac}' if frac else '')
    else:
        frac = match.group('frac')
        digits = match.group('int') + (f'.{frac}' if frac else '')
    try:
        value = Decimal(digits)
    except InvalidOperation:  # pragma: no cover - регулярное выражение пропускает только цифры
        return None
    return value if value <= MAX_AMOUNT else None


def _adjacent(tokens: list[_Token], start: int, end: int, text: str) -> _Token | None:
    best: _Token | None = None
    best_gap = _MAX_GAP + 1
    for token in tokens:
        if token.end <= start:
            gap_text = text[token.end : start]
        elif token.start >= end:
            gap_text = text[end : token.start]
        else:
            continue
        if gap_text.strip(' ') or len(gap_text) >= best_gap:
            continue
        best, best_gap = token, len(gap_text)
    return best


def _not_a_price(text: str, start: int, end: int, *, check_before: bool, check_after: bool) -> bool:
    # «128GB», «A15», «x2» — артикулы и характеристики, «-15 %» — скидка, а не цена.
    before = text[start - 1] if start > 0 else ''
    after = text[end] if end < len(text) else ''
    if check_before and before.isalpha():
        return True
    return check_after and (after.isalpha() or text[end:].lstrip(' ')[:1] == '%')


def find_price_mentions(text: str) -> list[PriceMention]:
    """Все числа текста, похожие на цену, по порядку.

    Число, приклеенное к букве («128GB») или к знаку процента («-15%»), пропускается,
    если только эта «буква» не знак валюты («1299GEL»).

    Args:
        text: Текст витрины или его фрагмент.

    Returns:
        Найденные упоминания с позициями в исходной строке.
    """
    norm = text.translate(_SPACES)
    tokens = _currency_tokens(norm)
    mentions: list[PriceMention] = []
    for match in _NUMBER.finditer(norm):
        amount = _amount(match)
        if amount is None:
            continue
        start, end = match.start(), match.end()
        token = _adjacent(tokens, start, end, norm)
        before_free = token is None or token.start >= end
        after_free = token is None or token.end <= start
        if _not_a_price(norm, start, end, check_before=before_free, check_after=after_free):
            continue
        span_start = min(start, token.start) if token else start
        span_end = max(end, token.end) if token else end
        mentions.append(
            PriceMention(
                start=span_start,
                end=span_end,
                amount=amount,
                currency=token.code if token else None,
                currency_token=token.text if token else None,
            )
        )
    return mentions


def parse_price(text: str, *, allow_free: bool = False) -> ParsedPrice | None:
    """Читает цену из текста витрины.

    Из нескольких чисел выбирается первое, рядом с которым стоит знак валюты; если таких
    нет — первое число вообще. Если валюта у числа не указана, но во всём тексте названа
    ровно одна валюта, берётся она.

    Args:
        text: Текст элемента цены, цитата модели или meta-значение.
        allow_free: Считать «Бесплатно»/«Free» и нулевую цену ценой 0. По умолчанию
            нет: ноль на витрине чаще значит «цена по запросу», чем подарок.

    Returns:
        Цена или None, если цены в тексте нет.
    """
    norm = text.translate(_SPACES)
    mentions = [m for m in find_price_mentions(norm) if m.amount > 0 or allow_free]
    if not mentions:
        if allow_free and _FREE.search(norm):
            return ParsedPrice(amount=Decimal(0), currency=None, qualifier=Qualifier.FREE)
        return None
    index = next((i for i, m in enumerate(mentions) if m.currency is not None), 0)
    chosen = mentions[index]
    currency, token = chosen.currency, chosen.currency_token
    if index > 0 and _RANGE_GAP.fullmatch(norm[mentions[index - 1].end : chosen.start]):
        # «10 - 20 GEL»: валюта стоит только у верхней границы, а цена диапазона — нижняя.
        # (Будь валюта у нижней границы, выбрана была бы она сама.)
        lower = mentions[index - 1]
        return ParsedPrice(amount=lower.amount, currency=currency, currency_token=token, qualifier=Qualifier.RANGE)
    if currency is None:
        codes = {t.code: t.text for t in _currency_tokens(norm)}
        if len(codes) == 1:
            currency, token = next(iter(codes.items()))
    qualifier = Qualifier.EXACT
    if chosen.amount == 0:
        qualifier = Qualifier.FREE
    elif _RANGE.match(norm[chosen.end :]):
        qualifier = Qualifier.RANGE
    elif _FROM.search(norm[: chosen.start]) or (token is not None and token.endswith('დან')):
        qualifier = Qualifier.FROM
    return ParsedPrice(amount=chosen.amount, currency=currency, currency_token=token, qualifier=qualifier)


def parse_schema_amount(value: str | int | Decimal | None) -> Decimal | None:
    """Строгий разбор машинного значения schema.org (`price`, `content`, `og:price:amount`).

    По schema.org десятичный знак — точка, без разделителей тысяч, поэтому «1.299» здесь
    — это 1.299, а не тысяча двести девяносто девять. Только если значение явно записано
    «по-витринному» (с запятой, пробелами или знаком валюты), оно уходит в `parse_price`.

    Args:
        value: Число из JSON (int или Decimal — float сюда не попадает) или строка.

    Returns:
        Положительная сумма или None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | Decimal):
        amount = Decimal(value)
    else:
        cleaned = value.strip()
        if _SCHEMA_NUMBER.match(cleaned):
            amount = Decimal(cleaned)
        else:
            parsed = parse_price(cleaned)
            if parsed is None:
                return None
            amount = parsed.amount
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        return None
    return amount


def normalize_currency(value: str | None) -> str | None:
    """Приводит валюту из разметки к коду ISO 4217.

    Args:
        value: `GEL`, `gel`, `₾`, `US$` и т.п.

    Returns:
        Трёхбуквенный код или None, если значение не похоже на валюту.
    """
    if not value:
        return None
    cleaned = value.strip()
    if cleaned in _SYMBOLS:
        return _SYMBOLS[cleaned]
    upper = cleaned.upper()
    if upper in _ISO_CODES:
        return _ISO_CODES[upper]
    if re.fullmatch(r'[A-Z]{3}', upper):
        return upper
    tokens = _currency_tokens(cleaned)
    return tokens[0].code if len(tokens) == 1 and tokens[0].text == cleaned else None


def resolve_currency(*, structured: str | None, parsed: ParsedPrice | None, declared: str | None) -> str | None:
    """Выбирает валюту цены по старшинству источников.

    Явный код из разметки страницы важнее всего: если магазин показал нам цену в USD
    (гео-IP), это надо увидеть, а не «исправить» на лари из конфига. Валюта из конфига
    оператора нужна, когда страница валюту не назвала или назвала её неоднозначным «$».

    Args:
        structured: Код из JSON-LD/микроразметки/meta.
        parsed: Цена из текста (с записью валюты на странице).
        declared: Валюта, которую оператор указал для этого предложения.

    Returns:
        Код валюты или None (тогда цена не сравнивается с вашей).
    """
    if structured:
        return structured
    if parsed is not None and parsed.currency:
        if parsed.currency_token in AMBIGUOUS_TOKENS and declared:
            return declared
        return parsed.currency
    return declared


def collapse_spaces(text: str) -> str:
    """Сжимает любые пробельные символы в одиночные пробелы (для сравнения цитат)."""
    return ' '.join(text.translate(_SPACES).split())

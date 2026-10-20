"""Разбор цены из текста витрины: «1 299,00 ₾», «$1,299.99», «1.299,99 €», «от 1 200 ₽».

Чистые функции без ввода-вывода и без float: сумма собирается из цифр строки сразу в
`Decimal`, иначе 0.1 + 0.2 однажды покажет клиенту «изменение цены» на копейку.

Правило разделителей (и его неоднозначность):

- пробелы всех видов (NBSP, узкий NBSP, тонкий) и апостроф — всегда разделители тысяч;
- если в числе есть и точка, и запятая, последняя из них — десятичная, первая — тысячи
  («1.299,99» и «1,299.99» → 1299.99);
- одиночный разделитель, за которым ровно три цифры и нет разделителя другого вида, —
  это тысячи («1,299» → 1299, «1.299» → 1299); иначе это десятичная точка («12,99», «12.5»);
- исключение: ведущий ноль («0,500») группой тысяч быть не может — это дробь;
- перевод строки — граница элемента страницы, а не разделитель тысяч: «AirPods Pro 2» в
  заголовке и «729 ₾» под ним — это 2 и 729, а не 2 729. Знак валюты в соседнем элементе
  («729» и «₾» в разных `<span>`) к числу прилипает, если не принадлежит числу в своей строке.

Неоднозначность честная: «1.299» в валюте с тремя знаками после точки (KWD, BHD) было бы
1.299, но это не наш рынок. Поэтому, где страница даёт машинное значение schema.org
(`"price": "1299.00"`, `content="1299.00"`), оно разбирается строго (`parse_schema_amount`)
и предпочитается тексту витрины.

Суммы ограничены: не больше `MAX_AMOUNT` и не больше `MAX_DECIMALS` значащих знаков после
точки. «1e-999999999» в JSON-LD — не цена, а мусор (или ловушка): записанная в ячейку
целиком, она заняла бы гигабайты.
"""

import re
from bisect import bisect_left, bisect_right
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Literal

import msgspec

# Всё, что браузер рисует как пробел, приводится к обычному пробелу, а переводы строк — к '\n',
# один-к-одному: длина строки не меняется, и позиции найденной цены совпадают с исходным текстом
# (на этом стоит проверка цитаты модели).
_HSPACES: Final = '           　\t'
_VSPACES: Final = '\n\r\f\v  \x85'
_SPACES: Final = str.maketrans({**dict.fromkeys(_HSPACES, ' '), **dict.fromkeys(_VSPACES, '\n')})

# Цена больше миллиарда на витрине малого бизнеса — это штрихкод, артикул или телефон.
MAX_AMOUNT: Final = Decimal('999999999')
_MAX_INTEGER_DIGITS: Final = len(str(MAX_AMOUNT))
# Больше четырёх значащих знаков после точки у цены не бывает; нули в хвосте («12.50000») — бывают.
MAX_DECIMALS: Final = 4
_QUANTUM: Final = Decimal(1).scaleb(-MAX_DECIMALS)
# Хвост дробной части длиннее — уже не цена, даже если это одни нули.
_MAX_FRACTION_TEXT: Final = 12
# Текст элемента цены или машинное значение длиннее — это не цена, а контейнер или мусор:
# его не разбираем (и не пишем в таблицу клиента).
MAX_PRICE_TEXT: Final = 200
_MAX_CURRENCY_TEXT: Final = 32
# Сколько символов перед числом смотреть в поисках «от» / «from».
_FROM_WINDOW: Final = 40

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
# «from 1 200», «От: 1 200» — двоеточие после слова тоже бывает.
_FROM: Final = re.compile(r'(?<!\w)(?:from|starting at|от|ab|desde|dès|à partir de)\s*:?\s*$', re.IGNORECASE)
# Без «^»: `match(text, pos)` и так привязан к pos, а «^» совпал бы только в начале строки.
_RANGE: Final = re.compile(r'\s*(?:[-–—‒−]|\.\.\.?|…|to|до)\s*(?:US\$|[$€£₾₽₴₸₺֏₼])?\s*\d', re.IGNORECASE)
# «4 999 ₾ − 10%» — это бейдж скидки после цены, а не диапазон «от 4 999 до 10».
_DISCOUNT: Final = re.compile(r'\s*[-–—‒−]\s*\d{1,3}(?:[.,]\d+)?\s*%')
_PERCENT_AFTER: Final = re.compile(r' *%')
# Машинное значение schema.org: «1299.00», а также экспоненциальная запись «1e3» / «1e-60000»
# (её витринный разбор прочитал бы как «60000»).
_SCHEMA_NUMBER: Final = re.compile(r'^\d+(?:\.\d+)?(?:[eE][+-]?\d{1,10})?$')
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
        number_start: Начало самого числа.
        number_end: Конец самого числа (не включая).
        amount: Сумма.
        currency: Код валюты рядом с числом или None.
        currency_token: Запись валюты на странице.
    """

    start: int
    end: int
    number_start: int
    number_end: int
    amount: Decimal
    currency: str | None
    currency_token: str | None


type _Side = Literal['prefix', 'suffix', '']
type _Verdict = Literal['left', 'right', 'both', 'none']

# Где знак стоит по обычаю: «$649», «£9.99» — перед числом; «729 ლ», «1 299 руб.» — после.
# «€», «₾», коды валют встречаются с обеих сторон.
_PREFIX_SYMBOLS: Final = frozenset({'$', 'US$', '£'})
_SUFFIX_GROUPS: Final = frozenset({'lari', 'rub', 'uah'})


class _Token(msgspec.Struct, frozen=True):
    start: int
    end: int
    text: str
    code: str
    side: _Side = ''


class _Option(msgspec.Struct, frozen=True):
    # Знак валюты рядом с числом: зазор, индекс знака и «через границу строки».
    gap: int
    index: int
    crosses: bool


def _currency_tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _CURRENCY.finditer(text):
        found = match.group(0)
        if match.lastgroup == 'code':
            code = _ISO_CODES[found]
        elif match.lastgroup == 'sym':
            code = _SYMBOLS[found]
        elif match.lastgroup == 'lari':
            code = 'GEL'
        elif match.lastgroup == 'rub':
            code = 'RUB'
        else:
            code = 'UAH'
        side: _Side = ''
        if found in _PREFIX_SYMBOLS:
            side = 'prefix'
        elif match.lastgroup in _SUFFIX_GROUPS or found == '₽':
            side = 'suffix'
        tokens.append(_Token(match.start(), match.end(), found, code, side))
    return tokens


def bounded_amount(value: Decimal, *, allow_zero: bool = False) -> Decimal | None:
    """Сумма в пределах здравого смысла или None.

    Args:
        value: Сумма из текста или разметки.
        allow_zero: Разрешить ноль («бесплатно»).

    Returns:
        Сумма (хвост из нулей дальше `MAX_DECIMALS` знаков отрезан) или None — для NaN,
        бесконечности, отрицательных, слишком больших и слишком дробных значений.
    """
    if not value.is_finite() or value < 0 or value > MAX_AMOUNT or (value == 0 and not allow_zero):
        return None
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_DECIMALS:
        # «12.50000» — те же 12.5; «1e-999999999» — не цена, а мусор разметки.
        rounded = value.quantize(_QUANTUM)
        return rounded if rounded == value else None
    return value


def _amount(match: re.Match[str]) -> Decimal | None:
    frac: str | None
    if match.group('lead') is not None:
        lead, groups, sep = match.group('lead'), match.group('groups'), match.group('sep')
        frac = match.group('gfrac')
        if lead == '0' and sep in '.,' and len(groups) == 3 and frac is None:
            # «0,500»: группа тысяч не начинается с нуля — значит, это дробь.
            integer, frac = '0', groups
        else:
            integer = lead + groups.replace(sep, '')
    else:
        integer, frac = match.group('int'), match.group('frac')
    # Проверки по строке — до Decimal: на странице встречаются «числа» в тысячи цифр.
    if len(integer.lstrip('0')) > _MAX_INTEGER_DIGITS:
        return None
    if frac and (len(frac) > _MAX_FRACTION_TEXT or len(frac.rstrip('0')) > MAX_DECIMALS):
        return None
    try:
        value = Decimal(f'{integer}.{frac}' if frac else integer)
    except InvalidOperation:  # pragma: no cover - регулярное выражение пропускает только цифры
        return None
    return bounded_amount(value, allow_zero=True)


def _option(norm: str, tokens: list[_Token], index: int, start: int, end: int) -> _Option | None:
    # Зазор между числом и знаком: не больше `_MAX_GAP` символов и только пробелы/переводы строки.
    if end - start > _MAX_GAP:
        return None
    gap = norm[start:end]
    if gap.strip(' \n'):
        return None
    return _Option(gap=end - start, index=index, crosses='\n' in gap)


def _contest(token: _Token, left: _Option, right: _Option) -> _Verdict:
    """Чей знак, стоящий между двумя числами: левого, правого, обоих или ничей."""
    if left.crosses != right.crosses:
        return 'right' if left.crosses else 'left'  # своя строка важнее соседней
    if left.gap != right.gap:
        return 'left' if left.gap < right.gap else 'right'
    if token.side == 'prefix':
        return 'right'
    if token.side == 'suffix':
        return 'left'
    # Неясно, к какому числу относится «₾» или «GEL»: в одной строке — к обоим (как раньше:
    # «1 499 ₾ 1 299 ₾»), а через границы элементов — ни к какому: честный пропуск лучше
    # «15 GEL» из «iPhone 15» над отдельным «₾».
    return 'none' if left.crosses else 'both'


def _attach(norm: str, spans: list[tuple[int, int]], tokens: list[_Token]) -> list[int | None]:
    """Какой знак валюты принадлежит каждому числу (индекс в `tokens` или None).

    Знак ищется только у ближайших соседей (bisect по позициям), поэтому разбор линеен по
    длине текста — страница в мегабайты цифр не превращается в часы работы. Знак между двумя
    числами достаётся одному (`_contest`): «iPhone 15 $799» и «iPhone 15» над «$799» — это 15
    без валюты и 799 долларов.
    """
    starts = [token.start for token in tokens]
    ends = [token.end for token in tokens]
    options: list[list[_Option]] = []
    left_of: dict[int, tuple[int, _Option]] = {}  # знак справа от числа
    right_of: dict[int, tuple[int, _Option]] = {}  # знак слева от числа
    for number, (start, end) in enumerate(spans):
        found: list[_Option] = []
        before = bisect_right(ends, start) - 1
        if before >= 0 and (option := _option(norm, tokens, before, tokens[before].end, start)) is not None:
            found.append(option)
            right_of[before] = (number, option)
        after = bisect_left(starts, end)
        if after < len(tokens) and (option := _option(norm, tokens, after, end, tokens[after].start)) is not None:
            found.append(option)
            left_of[after] = (number, option)
        options.append(found)
    for index in left_of.keys() & right_of.keys():
        (left, left_option), (right, right_option) = left_of[index], right_of[index]
        verdict = _contest(tokens[index], left_option, right_option)
        if verdict in ('right', 'none'):
            options[left].remove(left_option)
        if verdict in ('left', 'none'):
            options[right].remove(right_option)
    owners: list[int | None] = []
    for found in options:
        pool = [option for option in found if not option.crosses] or found
        # При равном зазоре побеждает знак перед числом (он раньше в списке).
        owners.append(min(pool, key=lambda option: option.gap).index if pool else None)
    return owners


def _not_a_price(text: str, start: int, end: int, *, check_before: bool, check_after: bool) -> bool:
    # «128GB», «A15», «x2» — артикулы и характеристики, «-15 %» — скидка, а не цена.
    before = text[start - 1] if start > 0 else ''
    after = text[end] if end < len(text) else ''
    if check_before and before.isalpha():
        return True
    return check_after and (after.isalpha() or _PERCENT_AFTER.match(text, end) is not None)


def find_price_mentions(text: str) -> list[PriceMention]:
    """Все числа текста, похожие на цену, по порядку.

    Число, приклеенное к букве («128GB») или к знаку процента («-15%»), пропускается,
    если только эта «буква» не знак валюты («1299GEL»). Число не продолжается через
    перевод строки (границу элемента страницы).

    Args:
        text: Текст витрины или его фрагмент.

    Returns:
        Найденные упоминания с позициями в исходной строке.
    """
    norm = text.translate(_SPACES)
    tokens = _currency_tokens(norm)
    numbers = [
        (match.start(), match.end(), amount)
        for match in _NUMBER.finditer(norm)
        if (amount := _amount(match)) is not None
    ]
    owners = _attach(norm, [(start, end) for start, end, _ in numbers], tokens)
    mentions: list[PriceMention] = []
    for (start, end, amount), owner in zip(numbers, owners, strict=True):
        token = tokens[owner] if owner is not None else None
        before_free = token is None or token.start >= end
        after_free = token is None or token.end <= start
        if _not_a_price(norm, start, end, check_before=before_free, check_after=after_free):
            continue
        mentions.append(
            PriceMention(
                start=min(start, token.start) if token else start,
                end=max(end, token.end) if token else end,
                number_start=start,
                number_end=end,
                amount=amount,
                currency=token.code if token else None,
                currency_token=token.text if token else None,
            )
        )
    return mentions


def parse_price(text: str, *, allow_free: bool = False, infer_currency: bool = True) -> ParsedPrice | None:
    """Читает цену из текста витрины.

    Из нескольких чисел выбирается первое, рядом с которым стоит знак валюты; если таких
    нет — первое число вообще. Если валюта у числа не указана, но во всём тексте названа
    ровно одна валюта, берётся она (если `infer_currency`).

    Args:
        text: Текст элемента цены, цитата модели или meta-значение.
        allow_free: Считать «Бесплатно»/«Free» и нулевую цену ценой 0. По умолчанию
            нет: ноль на витрине чаще значит «цена по запросу», чем подарок.
        infer_currency: Брать единственную валюту текста для числа без знака. Для проверки
            цитаты модели выключается: валюта обязана стоять рядом с числом.

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
    if index > 0 and _RANGE_GAP.fullmatch(norm, mentions[index - 1].end, chosen.start):
        # «10 - 20 GEL»: валюта стоит только у верхней границы, а цена диапазона — нижняя.
        # (Будь валюта у нижней границы, выбрана была бы она сама.)
        lower = mentions[index - 1]
        return ParsedPrice(amount=lower.amount, currency=currency, currency_token=token, qualifier=Qualifier.RANGE)
    if currency is None and infer_currency:
        codes = {t.code: t.text for t in _currency_tokens(norm)}
        if len(codes) == 1:
            currency, token = next(iter(codes.items()))
    qualifier = Qualifier.EXACT
    if chosen.amount == 0:
        qualifier = Qualifier.FREE
    elif _RANGE.match(norm, chosen.end) and not _DISCOUNT.match(norm, chosen.end):
        qualifier = Qualifier.RANGE
    elif _FROM.search(norm, max(0, chosen.start - _FROM_WINDOW), chosen.start) or (
        token is not None and token.endswith('დან')
    ):
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
        Положительная сумма в пределах `bounded_amount` или None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | Decimal):
        amount = Decimal(value)
    else:
        cleaned = value.strip()
        if len(cleaned) > MAX_PRICE_TEXT:
            return None
        if _SCHEMA_NUMBER.match(cleaned):
            amount = Decimal(cleaned)
        else:
            parsed = parse_price(cleaned)
            if parsed is None:
                return None
            amount = parsed.amount
    return bounded_amount(amount)


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
    if len(cleaned) > _MAX_CURRENCY_TEXT:
        return None
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
    """Сжимает любые пробельные символы (и переводы строк) в одиночные пробелы."""
    return ' '.join(text.translate(_SPACES).split())


def collapse_lines(text: str) -> str:
    """Сжимает пробелы внутри строк и убирает пустые строки, сохраняя границы строк.

    Перевод строки здесь — граница элемента страницы: по нему `find_price_mentions` не
    склеивает числа из соседних элементов.
    """
    lines = (' '.join(line.split()) for line in text.translate(_SPACES).split('\n'))
    return '\n'.join(line for line in lines if line)

"""Разбор денежных токенов и дат, и обратная задача — правдоподобные перепрочтения.

Обратная задача нужна для ремонта: когда цепочка баланса требует от строки
конкретную дельту, мы не гадаем, а проверяем конечный список того, во что OCR
мог превратить исходное число.
"""

import calendar
import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

# Знак, доллар и скобка встречаются в любом порядке: Renasant печатает '$-10.00',
# Fulton — '-$10.00', бухгалтерская запись — '(10.00)' и '10.00-'.
# Знак валюты бывает не только долларом: британские и индийские выписки печатают
# '£1,234.56' и '₹1,234.56', европейские — '€'. Код валюты словом ('INR', 'Rs.')
# стоит отдельным токеном и сюда не попадает.
CURRENCY_SIGNS: Final[str] = '$£€₹¥'
MONEY_RE: Final[re.Pattern[str]] = re.compile(rf'^[(\-+{CURRENCY_SIGNS}\s]*[\d,]{{1,17}}\.\d{{2}}[)\-+\s]*$')
_STRIP = str.maketrans('', '', CURRENCY_SIGNS + ', ')

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS |= {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
# Добавить неофициальное сокращение 'sept' (вместо стандартного 'sep')
MONTHS['sept'] = 9


def normalise_money_token(token: str) -> str:
    """Починить ведущий знак валюты, прочитанный как буква.

    Renasant отдаёт 'PREVIOUS BALANCE S-10.00': '$' распознан как 'S'. Подмена
    делается только когда остаток токена — уже валидные деньги, поэтому обычное
    слово, начинающееся на S, ею не затрагивается.
    """
    raw = token.strip()

    if raw[:1] in {'S', 's', '§'} and MONEY_RE.match(raw[1:]):
        return '$' + raw[1:]

    return raw


# Символы, в которые OCR превращает цифры внутри числа. '/' вместо '7' —
# самая частая подмена на сканах Fulton, остальные общие с датами.
_MONEY_LOOKALIKES = str.maketrans(
    {'O': '0', 'o': '0', 'D': '0', 'l': '1', 'I': '1', 'S': '5', 'B': '8', 'Z': '2', 'G': '6', '/': '7'}
)
# Разрядные запятые — встроенная контрольная сумма числа: разряды идут по три.
GROUPED: Final[re.Pattern[str]] = re.compile(r'^(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}$')
DECIMAL_COMMA: Final[re.Pattern[str]] = re.compile(r'^\d{1,3}(?:,\d{3})*,\d{2}$')


def repair_money_token(token: str) -> str:
    """Вернуть числу цифровой вид, если OCR подменил цифры символами.

    Правка принимается только тогда, когда результат сходится по разрядной
    сетке: '2,173,5/6.64' -> '2,173,576.64' проходит, а '2,2/72,320.90' даёт
    группу из четырёх цифр и отвергается. Так починка не выдумывает суммы —
    непочиненная строка честно уйдёт в расхождение и будет видна в отчёте.
    """
    raw = normalise_money_token(token)

    if MONEY_RE.match(raw):
        return raw

    prefix = '-' if ('(' in raw or raw.rstrip().endswith('-') or raw.lstrip().startswith('-')) else ''
    body = raw.strip('()+- ' + CURRENCY_SIGNS).translate(_MONEY_LOOKALIKES)

    # Последняя группа из двух цифр — это не разряд, а центы: запятая стоит на
    # месте десятичной точки. Разрядная сетка сама на это указывает.
    if DECIMAL_COMMA.match(body):
        head, _, cents = body.rpartition(',')
        body = f'{head}.{cents}'

    return prefix + body if GROUPED.match(body) else raw


def is_money(token: str) -> bool:
    return bool(MONEY_RE.match(repair_money_token(token)))


def parse_money(token: str) -> Decimal | None:
    """'$-1,234.56' | '(1,234.56)' | '1,234.56-' -> Decimal.

    Скобки и хвостовой минус — обычная бухгалтерская запись отрицательных сумм;
    встречается в выписках чаще, чем ведущий минус.
    """
    raw = repair_money_token(token)

    if not MONEY_RE.match(raw):
        return None

    negative = '(' in raw or raw.rstrip().endswith('-') or '-' in raw
    body = raw.translate(_STRIP).strip('()+-')

    try:
        value = Decimal(body)
    except InvalidOperation:
        return None

    return -value if negative else value


# Во что мог превратиться символ, который мы ВИДИМ. Направление важно: список
# читается как «наблюдаемое -> возможное исходное», и обратные пары порождали
# варианты, которых распознавание не создаёт. Цифра, прочитанная как буква,
# чинится раньше — в `repair_money_token`; здесь остаётся то, что та починка
# пропускает, потому что токен и без неё выглядит валидными деньгами.
_CONFUSIONS: Final[list[tuple[str, str]]] = [
    # 'S' перед числом по умолчанию читается как знак доллара
    # (см. normalise_money_token), но на скане Renasant это бывает и пятёрка.
    ('S', '5'),
    ('s', '5'),
    # '§' — тот же случай: и доллар, и пятёрка. Выбор делает цепочка баланса.
    ('§', '5'),
    # Разрядная запятая и десятичная точка путаются в обе стороны, и обе
    # подмены дают осмысленное число.
    (',', '.'),
    ('.', ','),
]


def reading_variants(token: str, limit: int = 400) -> set[Decimal]:
    """Во что мог превратиться этот токен при распознавании — и наоборот.

    Ограничено одной подстановкой на вариант плюс восстановление потерянной
    десятичной точки: цепочка из двух ошибок в одном числе на практике не
    встречается, а перебор растёт квадратично.
    """
    out: set[Decimal] = set()
    raw = token.strip()

    direct = parse_money(raw)

    if direct is not None:
        out.add(direct)

    for old, new in _CONFUSIONS:
        if old not in raw:
            continue

        for i, ch in enumerate(raw):
            if ch != old:
                continue

            candidate = raw[:i] + new + raw[i + 1 :]
            value = parse_money(candidate)

            if value is not None:
                out.add(value)

            if len(out) >= limit:
                return out

    if direct is None:
        # Точка потерялась или была прочитана как цифра. Второе — реальный случай
        # Renasant, где выносные точки строки слипаются с числом и '363.80'
        # приходит как '363580'. Такой токен деньгами не читается вовсе, поэтому
        # перебрать все положения точки безопасно: ложный вариант всё равно
        # обязан дать точное равенство, чтобы быть принятым.
        digits = re.sub(r'[^\d]', '', raw)

        if 3 <= len(digits) <= 16:
            for i in range(1, len(digits)):
                for candidate in (
                    f'{digits[:i]}.{digits[i:]}',  # точка потеряна
                    f'{digits[:i]}.{digits[i + 1 :]}',  # точка прочитана как цифра
                ):
                    if (value := parse_money(candidate)) is not None:
                        out.add(value)

        return out

    # Токен читается как деньги, и тогда с точкой возможна ровно одна беда — она
    # съехала на разряд ('1200.00' вместо '120.00'). Перебирать здесь все
    # положения точки нельзя: из верного '1,234.56' это делает правдоподобное и
    # ложное '123.56', и ремонт молча заменяет напечатанное число.
    out.update(shifted for shifted in (direct / 10, direct * 10) if _fits_cents(shifted))

    return out


def _fits_cents(value: Decimal) -> bool:
    """Укладывается ли число в центы — то есть может ли оно быть суммой."""
    exponent = value.as_tuple().exponent

    return isinstance(exponent, int) and exponent >= -2


# Буквы, в которые OCR превращает цифры в датах. Отдельно от _CONFUSIONS:
# здесь направление всегда одно — буква прочитана вместо цифры.
_DIGIT_LOOKALIKES = str.maketrans({'O': '0', 'o': '0', 'D': '0', 'l': '1', 'I': '1', 'S': '5', 'B': '8', 'Z': '2'})
_DATE_SHAPE = re.compile(r'^\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?$')
_NUMERIC_DATE = re.compile(r'^(\d{1,2})[/-](\d{1,2})(?:[/-]\d{2,4})?$')


def infer_day_first(tokens: Iterable[str]) -> bool:
    """Порядок «день/месяц» выводится из самих дат документа.

    Число больше двенадцати не может быть месяцем: '31-05-2026' читается только
    как 31 мая. Улики считаются по всем токенам в обе стороны, и побеждает та,
    которой больше, — одиночный мусор вроде '99/01' документ не переворачивает.
    Американские выписки печатают месяц первым, индийские и британские — день,
    и без этой проверки половина дат второй группы либо не читается вовсе, либо
    молча уезжает в другой месяц.

    Args:
        tokens: Токены, среди которых могут быть даты.

    Returns:
        True, если день стоит перед месяцем.
    """
    first_over = second_over = 0

    for token in tokens:
        m = _NUMERIC_DATE.match(repair_date_token(token))

        if m is None:
            continue

        first, second = int(m.group(1)), int(m.group(2))

        if first > 12 >= second:
            first_over += 1
        elif second > 12 >= first:
            second_over += 1

    return first_over > second_over


_TRIM_CHARS = '~|:;,.*_"\'()[]{}<>«»‘’`†°—–-=§^\\/!?#@%&+ '


def _trim(token: str) -> str:
    """Снять мусорные глифы по краям токена.

    Скан приносит дату как '~Apr02' или '4,' — обрамление рождается из линовки
    таблицы, а не из текста, и мешает разбору сильнее, чем любая подмена цифр.
    """
    return token.strip().strip(_TRIM_CHARS)


def repair_date_token(token: str) -> str:
    """Вернуть дате цифровой вид, если OCR подменил цифры буквами.

    Renasant отдаёт 'O1-07' и 'O1L-07' вместо '01-07'. Сначала подменяются буквы
    с цифровым двойником, затем выбрасываются остальные — так уходит одиночный
    глиф от линовки, не разрушая разделители. Форма проверяется до починки,
    поэтому обычное слово сюда не попадает.
    """
    raw = _trim(token)

    if len(raw) > 12 or ('/' not in raw and '-' not in raw):
        return raw

    repaired = re.sub(r'[^\d/-]', '', raw.translate(_DIGIT_LOOKALIKES))

    return repaired if _DATE_SHAPE.match(repaired) else raw


def parse_date(
    token: str,
    *,
    year_hint: int | None = None,
    period: tuple[date, date] | None = None,
    day_first: bool = False,
) -> date | None:
    """04/01/2025, 04/01, 01-02, Apr 01, April 1, 2025, 2025-04-01, а также 01 Jan 2024 и 01-Jan-2024.

    Год у коротких форм берётся из периода выписки или year_hint. На стыке
    декабря и января механический выбор начала периода даёт ошибку в год,
    поэтому из двух кандидатов берётся тот, что попадает в период.

    Args:
        token: Токен с датой.
        year_hint: Подсказка года для коротких форм (имеет приоритет после period).
        period: Диапазон периода выписки; если задан, дата должна в него попадать.
        day_first: Порядок M/D по умолчанию (False); если True, порядок D/M.
    """
    raw = repair_date_token(token)

    if not raw:
        return None

    # ISO-формат: 2025-04-01
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', raw)

    if m:
        yy, mm, dd = (int(x) for x in m.groups())

        return _safe_date(yy, mm, dd)

    # Полная форма с годом: 04/01/2025 или 01/04/2025 (в зависимости от day_first)
    m = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$', raw)

    if m:
        first, second, yy = (int(x) for x in m.groups())

        if yy < 100:
            yy += 2000

        if day_first:
            dd, mm = first, second
        else:
            mm, dd = first, second

        return _safe_date(yy, mm, dd)

    # День перед месяцем: '01 Jan 2024', '01-Jan-2024', '1 January 2024' — так
    # печатают индийские и британские банки.
    m = _DAY_MONTH.match(raw)

    if m:
        month = MONTHS.get(m.group(2).lower())

        if month is None:
            return None

        if m.group(3):
            return _safe_date(int(m.group(3)), month, int(m.group(1)))

        return _resolve_year(month, int(m.group(1)), year_hint, period)

    # Пробел между месяцем и числом необязателен: Ixonia печатает 'Apr01'.
    parsed = _split_month_day(raw)

    if parsed:
        name, dd, year_token = parsed
        # Отдельное имя, а не переиспользование mm: там уже выведен int, и
        # проверка на None стала бы недостижимой для mypy.
        month = MONTHS.get(name)

        if month is None:
            return None

        if year_token:
            return _safe_date(int(year_token), month, dd)

        return _resolve_year(month, dd, year_hint, period)

    # Короткая форма: 04/01 или 01/04 (в зависимости от day_first)
    m = re.match(r'^(\d{1,2})[/-](\d{1,2})$', raw)

    if m:
        first, second = int(m.group(1)), int(m.group(2))

        if day_first:
            dd, mm = first, second
        else:
            mm, dd = first, second

        return _resolve_year(mm, dd, year_hint, period)

    return None


_MONTH_DAY = re.compile(r'^([A-Za-z]{3,9})\.?\s*([0-9OoDlISBZ]{1,3})(?:,?\s*(\d{4}))?$')
_DAY_MONTH = re.compile(r'^(\d{1,2})[\s/-]+([A-Za-z]{3,9})\.?(?:[\s/-]+(\d{4}))?$')


def _split_month_day(raw: str) -> tuple[str, int, str | None] | None:
    """Разделить 'Apr01', 'April 1, 2025', 'AprO09' на месяц, день и год.

    Разбиение перебирается, а не берётся жадно: скан вставляет букву внутрь
    числа ('AprO09'), и по жадному разбору именем месяца оказывается 'apro'.
    День при этом чинится теми же подменами, что и остальные даты.
    """
    m = _MONTH_DAY.match(raw)

    if not m:
        return None

    head, tail, year_token = m.group(1), m.group(2), m.group(3)

    for cut in range(len(head), 2, -1):
        name = head[:cut].lower()

        if name not in MONTHS:
            continue

        digits = (head[cut:] + tail).translate(_DIGIT_LOOKALIKES)

        if digits.isdigit() and 1 <= int(digits) <= 31:
            return name, int(digits), year_token

    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_year(month: int, day: int, year_hint: int | None, period: tuple[date, date] | None) -> date | None:
    if period:
        for year in {period[0].year, period[1].year}:
            candidate = _safe_date(year, month, day)

            if candidate and period[0] <= candidate <= period[1]:
                return candidate

        # Дата вне периода (например, ENDING BALANCE FROM PRIOR STATEMENT
        # датирован последним днём предыдущего месяца) — берём ближайший год.
        for year in (period[0].year, period[1].year):
            if (candidate := _safe_date(year, month, day)) is not None:
                return candidate

    if year_hint:
        return _safe_date(year_hint, month, day)

    return None


def plausible_misreading(printed: Decimal, required: Decimal) -> bool:
    """Могло ли `required` быть прочитано как `printed`.

    Проверяются две формы ошибки, которыми OCR испортил числа в этих
    документах: одна перепутанная цифра и перестановка соседних. Длина при этом
    обязана совпасть — потеря разряда меняет число слишком сильно, чтобы
    считать её опечаткой, и такие случаи должны попадать в отчёт, а не чиниться.
    """
    left, right = f'{abs(printed):.2f}', f'{abs(required):.2f}'

    if len(left) != len(right) or left == right:
        return False

    diff = [i for i, (a, b) in enumerate(zip(left, right, strict=True)) if a != b]

    if len(diff) == 1:
        return left[diff[0]].isdigit() and right[diff[0]].isdigit()

    if len(diff) == 2 and diff[1] - diff[0] == 1:
        return left[diff[0]] == right[diff[1]] and left[diff[1]] == right[diff[0]]

    return False

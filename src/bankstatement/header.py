"""Разбор шапки выписки: итоги, счёт, банк, границы периода.

Шапка — самая ответственная часть документа. Она и требуемый выход (сверяется
на точное совпадение), и эталон, которым проверяются транзакции: ошибка здесь
бракует правильный разбор или пропускает неправильный.

Поэтому подписи не зашиты под конкретный банк, а собраны в синонимы, читаются в
двух макетах (подпись слева от суммы и подпись над суммой), а выбор между
кандидатами делает собственный инвариант шапки:
beginning + deposits - withdrawals == ending.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import product
from typing import Final

from msgspec.structs import replace

from bankstatement.layout import Line
from bankstatement.models import Account, Period, Source, Summary
from bankstatement.money import infer_day_first, parse_date, parse_money, plausible_misreading, reading_variants
from bankstatement.recognize.base import Word

# Подписи всех четырёх банков плюс распространённые синонимы. Порядок внутри
# группы не важен, важна непересекаемость групп: 'checks' относится к списаниям,
# но 'check' в 'Check Summary' — заголовок блока, а не итог, поэтому маркеры
# итогов всегда требуют денежного значения.
#
# Непересекаемость групп (проверено вручную):
# - начальный остаток: не содержит 'total', 'credits', 'deposits', 'debits', 'checks', 'withdrawals'
# - конечный остаток: не содержит 'total', 'credits', 'deposits', 'debits', 'checks', 'withdrawals'
# - поступления: не содержит 'balance', 'ending', 'withdrawals', 'debits', 'checks'
# - списания: не содержит 'balance', 'ending', 'credits', 'deposits', 'additions'
LABELS: dict[str, tuple[str, ...]] = {
    'beginning_balance': (
        'beginning balance',
        'starting balance',
        'prior statement balance',
        'previous statement balance',
        'previous balance',
        'opening balance',
        'balance forward',
        'beginning ledger balance',
        'last statement balance',
        'balance last statement',
        'beginning ledger',
        'previous ledger balance',
        'prior ledger balance',
    ),
    'ending_balance': (
        'ending balance',
        'ending statement balance',
        'closing balance',
        'new balance',
        'current balance',
        'ending ledger balance',
        'ending ledger',
        'statement closing balance',
    ),
    'deposits_total': (
        'deposits and credits',
        'total deposits',
        'deposits/credits',
        'additions',
        'total credits',
        'total deposits/credits',
        'credits',
        'deposits',
        'total additions',
        'total deposits and credits',
        'deposits credited',
    ),
    'withdrawals_total': (
        'withdrawals and debits',
        'total withdrawals',
        'checks/debits',
        'subtractions',
        'total debits',
        'total checks/debits',
        'total checks paid',
        'checks paid',
        'withdrawals',
        'debits',
        'checks',
        'total checks',
        'total withdrawals and debits',
    ),
}

BANK_NAME: Final[re.Pattern[str]] = re.compile(
    r"\b([A-Z][A-Za-z&.'’-]*(?:[ ]+[A-Z][A-Za-z&.'’-]*){0,2}[ ]+(?:BANK|Bank|CREDIT UNION|Credit Union))\b"
)
ACCOUNT_HINT: Final[re.Pattern[str]] = re.compile(r'(?:account\s*(?:number|no\.?|#)?\s*[:.]?\s*)([X*x\d\- ]{4,})', re.I)
LAST4: Final[re.Pattern[str]] = re.compile(r'(\d{4})\s*$')
DATE_TOKEN: Final[re.Pattern[str]] = re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b')
ACCOUNT_CAPTION: Final[re.Pattern[str]] = re.compile(r'\baccount\s*(?:number|no\.?|#)\b', re.I)
MASKED_NUMBER: Final[re.Pattern[str]] = re.compile(r'(?<![\w.-])([Xx*]{2,}\d{4}|\d{6,})(?![\w.-])')
"""Номер счёта под подписью: маска с четырьмя цифрами или сплошная цифровая
строка. Группы через дефис не берутся: так печатают телефон службы поддержки,
а он в сетке реквизитов стоит рядом."""
COUNT_IN_LABEL: Final[re.Pattern[str]] = re.compile(r'\((\d{1,5})\)')
DATE_LEADING: Final[re.Pattern[str]] = re.compile(r'^\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b')

# Одна дата в любой из четырёх записей: '04/01/2025' и '04/01', 'April 1, 2025' и
# 'Apr 1' (год необязателен — его подскажет вторая граница), '01 Jan 2024' и
# '01-Jan-2024' (день перед месяцем), '2025-04-01'. Месяц словом — только
# настоящий месяц: иначе 'PERIOD 01-03-2026' читался бы как «месяц PERIOD».
_MONTH: Final[str] = r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?'
_DATE_FORM: Final[str] = (
    r'\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?'
    rf'|{_MONTH}\s+\d{{1,2}}(?:,?\s*\d{{4}})?'
    rf'|\d{{1,2}}[\s/-]+{_MONTH}(?:[\s/-]+\d{{4}})?'
    r'|\d{4}-\d{1,2}-\d{1,2}'
)
PERIOD_RANGE: Final[re.Pattern[str]] = re.compile(
    rf'({_DATE_FORM})\s*(?:-|–|through|thru|to)\s*({_DATE_FORM})',
    re.I,
)
AS_OF: Final[re.Pattern[str]] = re.compile(
    r'(beginning|ending)\s+balance\s+as\s+of\s+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', re.I
)
LAST_THIS_STATEMENT: Final[re.Pattern[str]] = re.compile(
    r'([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})\s*:?\s*(last|this)\s+statement', re.I
)

MAX_CANDIDATES_PER_FIELD: Final[int] = 5
"""Максимум кандидатов на поле для перебора в build_summary. Вместо 100+ кандидатов
(часто дубли в заголовке) оставляем TOP-5 по уверенности. Максимум комбинаций:
5^4 = 625 вместо 100M. Инвариант шапки выбирает правильное значение из остатка."""


@dataclass(frozen=True, slots=True)
class LabelHit:
    """Попадание «подпись итога — сумма» вместе с происхождением суммы."""

    field: str
    value: Decimal
    count: int | None
    line_index: int
    source: Source
    token: str = ''

    @property
    def confidence(self) -> float | None:
        return self.source.confidence


@dataclass(frozen=True, slots=True)
class Repair:
    """Правка одного числа шапки: чем заменить и на каком основании."""

    field: str
    value: Decimal
    confidence: float
    printed: str = ''


def _normalise(text: str) -> str:
    return re.sub(r'[^a-z0-9/ ]+', ' ', text.lower()).strip()


def is_activity_row(line: Line) -> bool:
    """Строка начинается с даты — значит это операция, а не итог.

    Разделение структурное: проверяем, что первый токен похож на дату.
    Fulton печатает в теле выписки '03/31 ENDING BALANCE FROM PRIOR STATEMENT 1,908,989.60',
    и эта строка верно определяется как операция (начинается с даты).
    Но подписи шапки вроде 'Deposits 12,345.67' и 'Credits 1,500.00'
    имеют форму 'слово + число' и не проходят проверку.

    Критерий: первый токен должен быть в виде MM/DD, MM-DD, DD/MM, DD-MM,
    то есть две группы цифр, разделённые слешем или дефисом.
    """
    stripped = line.text.strip()

    return bool(DATE_LEADING.match(stripped))


def _find_all_labels(text: str, line: Line) -> dict[str, tuple[float, float, str]]:
    """Найти ВСЕ вхождения подписей в нормализованный текст строки.

    Возвращает dict[field] = (x0, x1, matched_phrase) для каждого найденного поля.
    Если одно поле встречается несколько раз, берётся первое вхождение.

    Горизонтальные границы (x0, x1) получаются из _phrase_span(), который
    ищет фразу по словам в исходном тексте, а не в нормализованном.
    """
    normalised_text = _normalise(text)
    result: dict[str, tuple[float, float, str]] = {}

    for field, variants in LABELS.items():
        if field in result:
            continue  # уже нашли для этого поля

        for variant in variants:
            normalised_variant = _normalise(variant)

            if normalised_variant in normalised_text:
                # Нашли вхождение — получим горизонтальные границы
                span = _phrase_span(line, variant)

                if span is not None:
                    result[field] = (span[0], span[1], variant)
                    break

    return result


def _unified_hits(index: int, line: Line, next_line: Line | None = None) -> list[LabelHit]:
    """Найти все пары «подпись итога — денежное значение» в строке и на следующей.

    Объединяет логику встроенных подписей (подпись и сумма на одной строке) и
    стопочного макета (подписи в одной строке, суммы на следующей).

    Алгоритм:
    1. Если строка — операция (начинается с даты), пропустить
    2. Найти ВСЕ вхождения подписей в текущей строке
    3. Для каждой подписи:
       a. Если есть денежные токены на этой же строке:
          - Найти ближайший справа от конца подписи, но левее следующей подписи
       b. Если нет такого токена, поискать на следующей строке:
          - Найти токен с максимальным горизонтальным перекрытием со span подписи
    """
    if is_activity_row(line):
        return []

    labels = _find_all_labels(line.text, line)

    if not labels:
        return []

    hits: list[LabelHit] = []
    label_spans = list(labels.values())  # Список (x0, x1, variant) в порядке обнаружения

    for field, (label_x0, label_x1, _) in labels.items():
        # Попытка 1: денежный токен на этой же строке
        best_money: Word | None = None
        # Следующая подпись справа (если она есть): и сумма, и счётчик этой
        # подписи лежат левее неё.
        next_label_x0 = min((span[0] for span in label_spans if span[0] > label_x1), default=float('inf'))
        count_right = next_label_x0

        if line.money:
            # Ищем денежный токен справа от конца подписи
            candidates = [w for w in line.money if w.x0 >= label_x1]

            if candidates:
                # Выбрать денежный токен слева от следующей подписи
                best_money = next((w for w in candidates if w.x1 <= next_label_x0), candidates[-1])
                count_right = min(count_right, best_money.x0)

        # Попытка 2: денежный токен на следующей строке (stacked макет)
        if best_money is None and next_line and next_line.money:
            best_overlap = 0.0

            for word in next_line.money:
                overlap = _overlap((label_x0, label_x1), (word.x0, word.x1))

                if overlap > best_overlap:
                    best_money, best_overlap = word, overlap

        if best_money is None:
            continue

        value = parse_money(best_money.text)

        if value is None:
            continue

        hits.append(
            LabelHit(
                field=field,
                value=value,
                count=_count_near(line, label_x0, count_right),
                line_index=index,
                # Рамка берётся у самого денежного токена, а не у строки: в
                # выкладке «подпись над суммой» они и вовсе на разных строках,
                # и показать пальцем нужно именно число.
                source=Source(
                    page=best_money.page + 1,
                    bbox=(best_money.x0, best_money.y0, best_money.x1, best_money.y1),
                    engine=best_money.engine,
                    confidence=best_money.confidence,
                ),
                token=best_money.text,
            )
        )

    return hits


def _count_near(line: Line, left: float, right: float) -> int | None:
    """Счётчик «(N)» между подписью и её суммой, а не первый попавшийся в строке.

    В строке «Deposits (3) 100.00 Withdrawals (5) 40.00» первый счётчик достался
    бы обоим полям, и число списаний стало бы тройкой — прямой промах по
    критерию точного совпадения шапки.
    """
    for word in line.words:
        if word.x0 >= left and word.x1 <= right and (found := COUNT_IN_LABEL.search(word.text)):
            return int(found.group(1))

    return None


def _phrase_span(line: Line, phrase: str) -> tuple[float, float] | None:
    """Горизонтальные границы фразы, если её слова идут в строке подряд."""
    target = phrase.split()
    words = line.words
    normalised = [_normalise(w.text) for w in words]

    for start in range(len(words) - len(target) + 1):
        window = normalised[start : start + len(target)]

        if window == target:
            chunk = words[start : start + len(target)]

            return chunk[0].x0, chunk[-1].x1

    return None


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return min(a[1], b[1]) - max(a[0], b[0])


def collect_label_hits(lines: list[Line]) -> list[LabelHit]:
    """Все попадания «подпись итога — сумма», в обоих макетах.

    Одна и та же подпись встречается по нескольку раз (сводка, колонтитул,
    заголовок таблицы), поэтому здесь собираются все попадания без выбора —
    выбирать будет инвариант.

    Используется _unified_hits(), который объединяет логику inline и stacked макетов.
    """
    hits: list[LabelHit] = []

    for index, line in enumerate(lines):
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        hits.extend(_unified_hits(index, line, next_line))

    return hits


def _filter_candidates(hits: list[LabelHit]) -> list[LabelHit]:
    """Оставить по одному попаданию на значение, а из них — TOP-K по уверенности.

    Сначала значение, потом уверенность: колонтитул повторяет одно и то же
    число на каждой странице, и пять его уверенных копий вытеснили бы менее
    уверенное, но верное значение из блока сводки. Инвариант выбирает из
    оставшихся.
    """
    best: dict[Decimal, LabelHit] = {}

    for hit in hits:
        seen = best.get(hit.value)

        if seen is None or (hit.confidence or 0) > (seen.confidence or 0):
            best[hit.value] = hit

    distinct = sorted(best.values(), key=lambda h: h.line_index)

    if len(distinct) <= MAX_CANDIDATES_PER_FIELD:
        return distinct

    return sorted(distinct, key=lambda h: (-(h.confidence or 0), h.value))[:MAX_CANDIDATES_PER_FIELD]


def build_summary(lines: list[Line]) -> Summary:
    """Собрать шапку, выбрав кандидатов так, чтобы сошёлся её инвариант.

    Новое: кандидаты на каждое поле фильтруются до TOP-K по уверенности,
    чтобы избежать взрыва комбинаций при полном перебору. Инвариант выбирает
    верное значение из оставшихся кандидатов.
    """
    hits = collect_label_hits(lines)

    if not hits:
        return Summary()

    by_field: dict[str, list[LabelHit]] = {}

    for hit in hits:
        by_field.setdefault(hit.field, []).append(hit)

    # Фильтруем кандидатов на каждое поле
    for field in by_field:
        by_field[field] = _filter_candidates(by_field[field])

    fields = ('beginning_balance', 'deposits_total', 'withdrawals_total', 'ending_balance')
    options: list[list[LabelHit | None]] = [list(by_field.get(f, [])) or [None] for f in fields]

    best: tuple[int, Decimal, Summary, dict[str, LabelHit | None]] | None = None

    for combo in product(*options):
        summary = _summary_from(dict(zip(fields, combo, strict=True)))
        residual = summary.self_check()
        known = sum(1 for c in combo if c is not None)

        if residual is None:
            score = (known, Decimal('Infinity'))
        elif residual == 0:
            score = (known + 10, Decimal(0))  # сошедшийся инвариант перевешивает всё
        else:
            score = (known, abs(residual))

        if best is None or (score[0], -score[1]) > (best[0], -best[1]):
            best = (score[0], score[1], summary, dict(zip(fields, combo, strict=True)))

    if best is None:
        return Summary()

    return best[2] if best[1] == 0 else _repair_summary(best[2], best[3])


def _summary_from(chosen: dict[str, LabelHit | None]) -> Summary:
    def value(field: str) -> Decimal | None:
        hit = chosen.get(field)

        return hit.value if hit else None

    def count(field: str) -> int | None:
        hit = chosen.get(field)

        return hit.count if hit else None

    return Summary(
        beginning_balance=value('beginning_balance'),
        ending_balance=value('ending_balance'),
        deposits_total=value('deposits_total'),
        deposits_count=count('deposits_total'),
        withdrawals_total=value('withdrawals_total'),
        withdrawals_count=count('withdrawals_total'),
        sources={field: hit.source for field, hit in chosen.items() if hit is not None},
    )


def _repair_summary(summary: Summary, chosen: dict[str, LabelHit | None]) -> Summary:
    """Инвариант не сошёлся — подобрать прочтение одного из четырёх чисел.

    Чинится ровно одно значение: у шапки четыре числа и одно уравнение, так что
    правка двух чисел сразу подгонит что угодно под ноль. Одна правка, наоборот,
    переопределена — она обязана совпасть с тем, что реально напечатано.

    Если ноль дают правки разных полей, выбор делается по уверенности
    распознавания, и только когда она строго ниже у одного из кандидатов. Иначе
    правка неоднозначна, и шапка остаётся как напечатана: молча выбрать одну из
    двух версий значит с равной вероятностью записать в отчёт ложное число.
    """
    residual = summary.self_check()

    if residual is None or residual == 0:
        return summary

    variants = _repairs_from_variants(summary, chosen)
    computed = _repairs_from_equation(summary, chosen, residual)

    return _pick_repair(summary, variants or computed)


def _repairs_from_variants(summary: Summary, chosen: dict[str, LabelHit | None]) -> list[Repair]:
    """Правки, объяснённые конкретной подменой символа при распознавании."""
    out: list[Repair] = []

    for field, hit in chosen.items():
        if hit is None or not hit.token:
            continue

        for variant in reading_variants(hit.token):
            if variant != hit.value and replace(summary, **{field: variant}).self_check() == 0:
                out.append(
                    Repair(
                        field=field,
                        value=variant,
                        confidence=hit.confidence if hit.confidence is not None else 100.0,
                        printed=hit.token,
                    )
                )
                break

    return out


def _repairs_from_equation(summary: Summary, chosen: dict[str, LabelHit | None], residual: Decimal) -> list[Repair]:
    """Правки, выведенные из уравнения шапки: три числа задают четвёртое.

    Вычисленное значение принимается, только если оно отличается от
    напечатанного как опечатка распознавания. Иначе это была бы подгонка
    результата под собственную же формулу.
    """
    out: list[Repair] = []

    for field, sign in (
        ('beginning_balance', -1),
        ('deposits_total', -1),
        ('withdrawals_total', 1),
        ('ending_balance', 1),
    ):
        printed = getattr(summary, field)
        hit = chosen.get(field)

        if printed is None:
            continue

        required = printed + sign * residual

        if plausible_misreading(printed, required):
            confidence = hit.confidence if hit is not None and hit.confidence is not None else 100.0
            out.append(
                Repair(
                    field=field,
                    value=required,
                    confidence=confidence,
                    printed=hit.token if hit is not None else '',
                )
            )

    return out


def _apply_repair(summary: Summary, repair: Repair) -> Summary:
    """Заменить число и сознаться в провенансе, что оно исправлено.

    Без этой отметки выправленное число шапки неотличимо от прочитанного, а
    шапка — эталон, которым проверяется всё остальное: правку в ней перепроверят
    в первую очередь, если будет видно, что она была.
    """
    sources = dict(summary.sources)

    if (source := sources.get(repair.field)) is not None:
        sources[repair.field] = replace(source, repaired=True, printed=repair.printed or None)

    return replace(summary, sources=sources, **{repair.field: repair.value})


def _pick_repair(summary: Summary, repairs: list[Repair]) -> Summary:
    if not repairs:
        return summary

    if len(repairs) == 1:
        return _apply_repair(summary, repairs[0])

    repairs.sort(key=lambda item: item.confidence)

    if repairs[0].confidence == repairs[1].confidence:
        return summary  # правка неоднозначна — честнее показать расхождение

    return _apply_repair(summary, repairs[0])


def _period(first: date, second: date) -> Period:
    """Период в хронологическом порядке.

    Границы читаются из двух разных мест страницы, и ошибки в одной цифре года
    хватает, чтобы они поменялись местами. Обратный период отсекал бы по дате
    каждую операцию, и выписка вышла бы пустой — а по одной строке не видно,
    что виновата не она.
    """
    return Period(start=min(first, second), end=max(first, second))


def find_period(lines: list[Line]) -> Period | None:
    """Границы периода. Банки записывают их четырьмя разными способами."""
    head = lines[:80]
    text = '\n'.join(line.text for line in head)
    # Порядок день/месяц выводится из всех дат в шапке и первых строках таблицы:
    # '31-05-2026' читается только как 31 мая, и это переворачивает весь документ.
    day_first = infer_day_first(w.text for line in head for w in line.words)

    # Попытка 1: диапазон дат. Совпадений бывает несколько, и первое не обязано
    # быть датами ('Page 1 - 3'): берётся первое, которое читается.
    for m in PERIOD_RANGE.finditer(text):
        if (found := _date_range(m.group(1), m.group(2), day_first)) is not None:
            return found

    # Попытка 2: 'Beginning Balance as of 04/01/2025' / 'Ending Balance as of 04/30/2025'
    as_of: dict[str, date] = {}

    for kind, token in AS_OF.findall(text):
        if (parsed := parse_date(token, day_first=day_first)) is not None:
            as_of[kind.lower()] = parsed

    if 'beginning' in as_of and 'ending' in as_of:
        return _period(as_of['beginning'], as_of['ending'])

    # Попытка 3: 'DECEMBER 31, 2024: LAST STATEMENT' / 'JANUARY 31, 2025: THIS STATEMENT'.
    # Дата прошлой выписки — последний день прошлого периода, а не первый день
    # этого: операций за 31 декабря в январской выписке быть не может.
    statements: dict[str, date] = {}

    for token, kind in LAST_THIS_STATEMENT.findall(text):
        if (parsed := parse_date(token)) is not None:
            statements[kind.lower()] = parsed

    if 'last' in statements and 'this' in statements:
        return _period(statements['last'] + timedelta(days=1), statements['this'])

    return None


def _date_range(first: str, second: str, day_first: bool) -> Period | None:
    """Две границы периода; год, напечатанный лишь у одной, достаётся обеим."""
    start = parse_date(first, day_first=day_first)
    end = parse_date(second, day_first=day_first)

    if start and not end:
        end = parse_date(second, year_hint=start.year, day_first=day_first)

    if end and not start:
        start = parse_date(first, year_hint=end.year, day_first=day_first)

    if start and end:
        return _period(start, end)

    return None


def find_bank(lines: list[Line]) -> str | None:
    """Имя банка — фрагмент «<Собственные слова> Bank» в верхней части страницы.

    Ищется фрагмент, а не строка целиком: имя почти всегда напечатано в одной
    строке с адресом или телефоном. Суффиксы типа 'N.A.' (National Association)
    или 'FSB' (Federal Savings Bank) обычно удаляются из конца.
    """
    for line in lines[:15]:
        # Имя банка живёт в шапке страницы, до первой операции. Ниже начинаются
        # описания платежей, а в них стоит банк контрагента ('By Clg: CITI BANK',
        # перенос 'CHAKRABORTY-HDFC BANK LTD.'), и по нему выписку относили бы не
        # к тому банку.
        if is_activity_row(line):
            break

        if line.money:
            continue

        if (m := BANK_NAME.search(line.text)) is not None:
            # Нормализовать пробелы и удалить точки в названии
            name = re.sub(r'\s+', ' ', m.group(1)).replace('.', '').strip()
            # Удалить суффиксы N.A., FSB только если они идут ПОСЛЕ названия банка
            name = re.sub(r'\s+(?:NA|FSB)\s*$', '', name, flags=re.I).strip()

            return display_case(name)

    return None


SMALL_WORDS: Final[frozenset[str]] = frozenset({'of', 'and', 'the', 'for', 'de', 'du', 'la', 'del'})


def display_case(name: str) -> str:
    """Имя, набранное капителью, привести к виду, в котором его пишут.

    'GREAT LAKES COMMERCE BANK' в шапке — типографика, а не имя; в контракте
    задания и в справочниках банк называется 'Great Lakes Commerce Bank'.
    Аббревиатуры остаются: слово без гласных или не длиннее трёх букв ('PNC',
    'TD', 'HSBC') при опускании регистра превратилось бы в бессмыслицу.
    Имя, набранное не капителью, не трогается вовсе.

    Args:
        name: Имя банка как прочитано.

    Returns:
        Имя в печатном регистре.
    """
    if name != name.upper():
        return name

    out: list[str] = []

    for token in name.split():
        letters = re.sub(r'[^A-Za-z]', '', token)

        if letters.lower() in SMALL_WORDS and out:
            out.append(token.lower())
        elif len(letters) <= 3 or not re.search(r'[AEIOUY]', letters):
            out.append(token)
        else:
            out.append('-'.join(part.capitalize() for part in token.split('-')))

    return ' '.join(out)


def find_account_last4(lines: list[Line]) -> str | None:
    """Последние 4 цифры номера счёта.

    Ищется явное указание вроде 'Account: XXXX1858' или 'Account #4071',
    затем поиск по контексту 'account' + 4 цифры, но исключаются даты
    типа 'Account Statement April 2025'.
    """
    for index, line in enumerate(lines[:40]):
        # Дата рядом с подписью — не номер счёта: в сетке реквизитов 'ACCOUNT
        # NUMBER' и 'STATEMENT PERIOD 01-03-2026 to 31-05-2026' попадают в одну
        # строку, и без этого номером становился бы год.
        text = DATE_TOKEN.sub(' ', line.text)

        if (m := ACCOUNT_HINT.search(text)) and (d := LAST4.search(m.group(1).strip())):
            return d.group(1)

        # Подпись над значением: 'ACCOUNT NUMBER' в одной строке сетки, а
        # 'XXXXXX1067' — в следующей, под ней.
        if ACCOUNT_CAPTION.search(text) and index + 1 < len(lines):
            below = MASKED_NUMBER.search(lines[index + 1].text)

            if below is not None:
                return below.group(1)[-4:]

    month_pattern = re.compile(
        r'(january|february|march|april|may|june|july|august|september|october|'
        r'november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+',
        re.I,
    )

    for line in lines[:40]:
        text = DATE_TOKEN.sub(' ', line.text).strip()

        if 'account' not in text.lower() or 'statement' in text.lower():
            continue

        if m := LAST4.search(text):
            text_before = text[: text.rfind(m.group(1))]

            if not month_pattern.search(text_before):
                return m.group(1)

    return None


def build_account(lines: list[Line]) -> Account:
    return Account(
        bank=find_bank(lines),
        account_last4=find_account_last4(lines),
        period=find_period(lines),
    )

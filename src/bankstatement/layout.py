"""Геометрия страницы: слова -> визуальные строки.

Строки собираются только по координате y. Ни PyMuPDF, ни Tesseract нельзя
спрашивать о том, что такое строка: первый на этих макетах отдаёт отдельный блок
почти на каждый фрагмент, второй сбивается на многоколоночных страницах.
"""

import re
from dataclasses import dataclass, replace
from itertools import pairwise
from statistics import median
from typing import Final

from bankstatement.money import MONTHS, is_money
from bankstatement.recognize.base import Word


@dataclass(frozen=True, slots=True)
class Line:
    """Визуальная строка: слова, оказавшиеся на одной высоте."""

    words: tuple[Word, ...]
    page: int

    @property
    def y(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def text(self) -> str:
        return ' '.join(w.text for w in self.words)

    @property
    def money(self) -> tuple[Word, ...]:
        return tuple(w for w in self.words if is_money(w.text))

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            min(w.x0 for w in self.words),
            min(w.y0 for w in self.words),
            max(w.x1 for w in self.words),
            max(w.y1 for w in self.words),
        )

    @property
    def confidence(self) -> float | None:
        seen = [w.confidence for w in self.words if w.confidence is not None]

        return sum(seen) / len(seen) if seen else None


def group_lines(words: list[Word], tolerance: float | None = None) -> list[Line]:
    """Сгруппировать слова в строки по вертикальной близости.

    Строка удерживается медианным интервалом [y0, y1] своих слов, а не средним
    центром. Это принципиально: в сканах на строке попадаются глифы в один
    пиксель высотой (выносные точки, тире, следы печати), и среднее от них
    уползает настолько, что строка рвётся пополам — именно так у Renasant
    терялась пара чеков. Медиана к таким выбросам нечувствительна.

    Допуск по умолчанию — доля медианной высоты слова: он подстраивается под
    кегль страницы, тогда как фиксированный порог ломается при смене dpi.
    """
    if not words:
        return []

    if tolerance is None:
        tolerance = max(median([w.height for w in words]) * 0.6, 1.0)

    rows: list[list[Word]] = []

    for word in sorted(words, key=lambda w: (w.y0, w.x0)):
        if rows and _belongs(word, rows[-1], tolerance):
            rows[-1].append(word)
        else:
            rows.append([word])

    return [
        Line(words=merge_split_numbers(merge_split_dates(sorted(row, key=lambda w: w.x0))), page=row[0].page)
        for row in rows
    ]


DAY_NUMBER: Final[re.Pattern[str]] = re.compile(r'^\d{1,2}[.,]?$')
MAX_GAP_IN_DATE: Final[float] = 1.5
"""Доля высоты строки. Чуть шире, чем внутри числа: между названием месяца и
числом стоит настоящий пробел, а не потерянный разделитель."""


def merge_split_dates(words: list[Word]) -> list[Word]:
    """Собрать дату, разорванную пробелом: 'Apr' + '02' -> 'Apr 02'.

    Ixonia печатает и 'Apr01', и 'Apr 02' на соседних строках одной таблицы.
    Без склейки вторая форма перестаёт быть датой, строка выпадает из операций
    и рвёт цепочку баланса — при том, что сумма и остаток в ней прочитаны верно.
    """
    out: list[Word] = []

    for word in words:
        head = out[-1] if out else None

        if (
            head is not None
            and head.text.strip('.,').lower() in MONTHS
            and DAY_NUMBER.match(word.text)
            and word.x0 - head.x1 <= MAX_GAP_IN_DATE * max(head.height, word.height)
        ):
            out[-1] = replace(head, text=f'{head.text} {word.text}', x1=word.x1)
            continue

        out.append(word)

    return out


NUMBER_HEAD: Final[re.Pattern[str]] = re.compile(r'^\$?\d{1,3}(?:,\d{3})*,?$')
NUMBER_TAIL: Final[re.Pattern[str]] = re.compile(r'^\d{3}(?:,\d{3})*[.,]\d{2}$')


MAX_GAP_IN_NUMBER: Final[float] = 1.0
"""Доля высоты строки. Потерянная запятая оставляет зазор в 0.3–0.65 высоты
(замер по Binder), а соседние колонки дневной сводки отстоят на одиннадцать."""

TIGHT_GAP: Final[float] = 0.5
"""Доля медианного зазора строки, ниже которой зазор считается ненормально
тесным. Замер по Binder: '58'+'227.64' и '381'+'809.16' дают 0.30 и 0.18 при
обычных пробелах той же строки."""


def _median_gap(words: list[Word]) -> float | None:
    """Типичный зазор между соседними словами строки."""
    if len(words) < 2:
        return None

    return median(second.x0 - first.x1 for first, second in pairwise(words))


def _split_number(head: Word, tail: Word, median_gap: float | None) -> bool:
    """Разорвана ли здесь одна сумма на месте потерянной разрядной запятой.

    Одного расстояния мало. Замер по Binder: в строке сводки все токены стоят
    одинаково тесно, и '$336' + '565.07' по зазору неотличимы от обычной пары
    слов — доля от медианы там 1.06. Зато голова помечена знаком валюты, и это
    прямое свидетельство, что число началось. Там, где такой пометки нет
    ('58' + '227.64'), работает второй признак: зазор втрое меньше обычного на
    этой же строке.

    Синтетическая страница из tests/conftest.py проверяет обратное: у 'CHECK 101
    250.00' пробелы одинаковы, головы без знака валюты, и склейки быть не должно.
    """
    if not (NUMBER_HEAD.match(head.text) and NUMBER_TAIL.match(tail.text)):
        return False

    gap = tail.x0 - head.x1

    if gap > MAX_GAP_IN_NUMBER * max(head.height, tail.height):
        return False

    marked = head.text.startswith('$') or head.text.endswith(',')

    return marked or (median_gap is not None and median_gap > 0 and gap < median_gap * TIGHT_GAP)


def merge_split_numbers(words: list[Word]) -> tuple[Word, ...]:
    """Склеить число, разорванное на месте потерянной разрядной запятой.

    OCR теряет запятую и отдаёт '$848' и '578.92' двумя токенами — по отдельности
    это не сумма, вместе это $848,578.92. Без склейки шапка выписки теряет
    старший разряд, и весь период рассыпается на пустом месте.
    """
    median_gap = _median_gap(words)
    out: list[Word] = []

    for word in words:
        head = out[-1] if out else None

        if head is not None and _split_number(head, word, median_gap):
            out[-1] = replace(
                head,
                text=f'{head.text.rstrip(",")},{word.text}',
                x1=word.x1,
                confidence=min(head.confidence or 0.0, word.confidence or 0.0) or None,
            )
            continue

        out.append(word)

    return tuple(out)


def _belongs(word: Word, row: list[Word], tolerance: float) -> bool:
    """Слово на строке, если его центр внутри её полосы или рядом с медианой.

    Допуск нарочно не прибавляется к границам полосы: тогда полоса растёт от
    каждого присоединённого слова и за несколько шагов проглатывает соседнюю
    строку. Полоса задаёт «точно свои», допуск — «чуть выбившиеся».
    """
    top = median([w.y0 for w in row])
    bottom = median([w.y1 for w in row])

    if top <= word.cy <= bottom:
        return True

    return abs(word.cy - median([w.cy for w in row])) <= tolerance

"""Гипотеза «секции»: колонки остатка нет, знак операции задаёт заголовок блока.

Слабее цепочки остатков: подтверждает только итог периода, но не каждую строку,
поэтому и гарантия у сведённого ею периода другая — B, а не A. Зато это
единственное прочтение для выписок, где остаток построчно не печатают.
"""

import re
from typing import Final

from bankstatement.layout import Line
from bankstatement.models import Summary, Transaction, UnplacedRow
from bankstatement.money import is_money, parse_date, parse_money
from bankstatement.rows import DateRange, Hypothesis, Row, _describe

BALANCE_MARKER: Final[re.Pattern[str]] = re.compile(r'\b(balance|remainder)\b', re.I)

CREDIT_WORDS: Final[re.Pattern[str]] = re.compile(r'\b(credits?|deposits?|additions?)\b', re.I)
DEBIT_WORDS: Final[re.Pattern[str]] = re.compile(r'\b(debits?|withdrawals?|checks?|subtractions?|fees?)\b', re.I)


def section_signs(lines: list[Line]) -> dict[int, tuple[int, int]]:
    """Знак операции по заголовку блока, для выписок без колонки баланса.

    Renasant печатает «CHECKS», «OTHER DEBITS», «CREDITS» и не печатает баланс
    построчно — знак больше взять неоткуда. Значение словаря — страница
    заголовка и знак; страница важна, см. `_filter_headers`.
    """
    signs: dict[int, tuple[int, int]] = {}

    for index, line in enumerate(lines):
        if line.money:
            continue

        text = line.text
        credit, debit = CREDIT_WORDS.search(text), DEBIT_WORDS.search(text)

        if credit and not debit:
            signs[index] = (line.page, 1)
        elif debit and not credit:
            signs[index] = (line.page, -1)

    return signs


def _filter_headers(signs: dict[int, tuple[int, int]], lines: list[Line]) -> dict[int, tuple[int, int]]:
    """Исключить повторяющиеся строки (колонтитулы) из заголовков раздела.

    Если строка с CREDIT/DEBIT_WORDS встречается на нескольких страницах
    примерно на одной высоте, это скорее всего колонтитул (заголовок страницы),
    а не заголовок раздела. Заголовок раздела должен быть уникален или не
    повторяться регулярно.

    Args:
        signs: словарь {индекс_линии: (страница, знак)}
        lines: все линии документа

    Returns:
        Отфильтрованный словарь без колонтитулов
    """
    # Сгруппировать строки-заголовки по y-координате
    y_groups: dict[int, list[tuple[int, int]]] = {}

    for header_index, (page, _) in signs.items():
        line = lines[header_index]

        if not line.words:
            continue

        # Взять среднюю y-координату строки
        y_avg = (line.words[0].y0 + line.words[0].y1) / 2
        y_key = int(y_avg / 10.0)  # Квантовать на ширину примерной строки

        if y_key not in y_groups:
            y_groups[y_key] = []

        y_groups[y_key].append((page, header_index))

    # Найти y-координаты, которые встречаются на разных страницах — колонтитулы
    repeated_y = set()

    for items in y_groups.values():
        pages_with_this_y = {p for p, _ in items}

        if len(pages_with_this_y) > 1:
            # Эта y-координата на разных страницах — колонтитул, исключить
            for _, header_index in items:
                repeated_y.add(header_index)

    # Вернуть только заголовки, не являющиеся колонтитулами
    return {idx: (page, sign) for idx, (page, sign) in signs.items() if idx not in repeated_y}


def _heading_at(index: int, signs: dict[int, tuple[int, int]]) -> int | None:
    """Индекс последнего заголовка раздела до этой строки."""
    applicable = [i for i in signs if i <= index]

    return max(applicable) if applicable else None


COLUMN_TOLERANCE: Final[float] = 6.0
"""Допуск попадания суммы в колонку, в точках PDF. Суммы выровнены по правому
краю, и разброс внутри колонки — доли пункта; шесть пунктов примерно равны ширине
символа, то есть заведомо больше типографского разброса и заведомо меньше
расстояния между колонками. Замер на Renasant: настоящий список выровнен по 579.6,
микротекст на образах чеков даёт 305.0 и 539.2."""


def _column_edges(lines: list[Line], signs: dict[int, tuple[int, int]], period: DateRange | None) -> list[float]:
    """Правые края сумм на страницах, где раздел объявлен своим заголовком.

    Это эталон колонки. Продолжение таблицы на следующей странице печатается по
    той же сетке, а посторонний текст — нет, и это единственное, чем они
    различаются: даты и суммы есть и там, и там.
    """
    pages = {page for page, _ in signs.values()}
    edges: list[float] = []

    for line in lines:
        if line.page not in pages or not line.money:
            continue

        edges.extend(row.money[-1].x1 for row in _section_rows(line, period))

    return edges


def _in_column(row: Row, edges: list[float]) -> bool:
    """Попадает ли сумма строки в одну из колонок, известных по заголовкам."""
    return any(abs(row.money[-1].x1 - edge) <= COLUMN_TOLERANCE for edge in edges)


def _section_rows(line: Line, period: DateRange | None) -> list[Row]:
    """Записи строки для выписки без колонки баланса.

    Обычно запись одна. Но чековый список печатается в две колонки — «номер
    дата сумма номер дата сумма», — и тогда строка несёт несколько операций.
    Разбиение допускается только при ровной сетке (сумм ровно столько же,
    сколько дат), иначе дата, попавшая в описание, разорвала бы операцию.
    Границей записи служит сумма: она всегда стоит последней в своей записи.
    """
    words = list(line.words)
    dates = [(i, d) for i, w in enumerate(words) if (d := parse_date(w.text, period=period)) is not None]
    money_at = [i for i, w in enumerate(words) if is_money(w.text)]

    if not dates or not money_at:
        return []

    if len(dates) >= 2 and len(money_at) == len(dates):
        rows: list[Row] = []
        start = 0

        for slot, end in enumerate(money_at):
            chunk = words[start : end + 1]
            body = [w for w in chunk if not is_money(w.text) and parse_date(w.text, period=period) is None]
            rows.append(Row(date=dates[slot][1], description=_describe(body), money=(words[end],), line=line))
            start = end + 1

        return rows

    first_index, first_date = dates[0]
    body = [w for w in words[first_index + 1 :] if not is_money(w.text)]

    return [Row(date=first_date, description=_describe(body), money=tuple(words[i] for i in money_at), line=line)]


def section_hypothesis(lines: list[Line], summary: Summary, period: DateRange | None) -> Hypothesis:
    """Колонки баланса нет: знак берётся из заголовка блока, сумма — как есть.

    Слабее цепочки: подтверждает только итог, но не каждую строку. Поэтому и
    гарантия у неё другая — оракул отметит период как сведённый по совокупности.

    Потерянные строки (те, для которых не найден знак) отслеживаются в
    skipped и suspect_pages.
    """
    signs = section_signs(lines)
    # Исключить колонтитулы перед использованием
    signs = _filter_headers(signs, lines)

    if not signs:
        return Hypothesis(name='sections', transactions=[])

    out: list[Transaction] = []
    suspect: set[int] = set()
    unplaced: list[UnplacedRow] = []
    edges = _column_edges(lines, signs, period)
    printed = {abs(value) for value in (summary.beginning_balance, summary.ending_balance) if value is not None}

    def drop(row: Row, reason: str, kind: str = 'lost') -> None:
        """Строка не размещена: записать её с причиной и пометить страницу."""
        amounts = [value for word in row.money if (value := parse_money(word.text)) is not None]
        unplaced.append(
            UnplacedRow(
                date=row.date, description=row.description, amounts=amounts, reason=reason, kind=kind, source=row.source
            )
        )

        if kind != 'marker':
            suspect.add(row.line.page)

    for index, line in enumerate(lines):
        if not line.money:
            continue

        rows = _section_rows(line, period)

        if not rows:
            continue

        heading = _heading_at(index, signs)

        if heading is None:
            for row in rows:
                drop(row, 'no_sign')

            continue

        # Заголовок с другой страницы применим только к продолжению той же
        # таблицы. Отличить продолжение от постороннего текста можно лишь по
        # сетке: у образов чеков и бланка сверки свои даты и свои суммы.
        carried = signs[heading][0] != line.page
        sign = signs[heading][1]

        for row in rows:
            if carried and not _in_column(row, edges):
                drop(row, 'foreign_column')
                continue

            value = parse_money(row.money[-1].text)

            if value is None:
                drop(row, 'unreadable')
                continue

            # Строка «Sep 30 ENDING BALANCE -4.00» под заголовком списаний — это
            # маркер остатка, а не операция: у цепочки его отсекает сам остаток,
            # здесь остатка нет, и отличить маркер можно только по подписи и по
            # совпадению с напечатанным итогом. Без этого маркер становился
            # списанием на ту же сумму, и обороты сходились случайно.
            if abs(value) in printed and BALANCE_MARKER.search(row.description):
                drop(row, 'balance_marker', kind='marker')
                continue

            amount = abs(value) * sign
            out.append(
                Transaction(
                    date=row.date,
                    description=row.description,
                    deposit=amount if amount > 0 else None,
                    withdrawal=-amount if amount < 0 else None,
                    source=row.source,
                )
            )

    return Hypothesis(
        name='sections',
        transactions=out,
        skipped=len(unplaced),
        suspect_pages=frozenset(suspect),
        unplaced_rows=unplaced,
    )

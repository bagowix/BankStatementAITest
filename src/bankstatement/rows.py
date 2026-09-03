"""Строки-кандидаты: что из строк страницы вообще может быть операцией.

Общая часть обеих гипотез разбора. Здесь строка ещё не операция: у неё есть
дата, описание и денежные токены, а сумму, знак и остаток ей назначит гипотеза.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

from bankstatement.layout import Line
from bankstatement.models import Source, Transaction, UnplacedRow
from bankstatement.money import infer_day_first, is_money, parse_date
from bankstatement.recognize.base import Word

NOISE: Final[re.Pattern[str]] = re.compile(r'[*=_~—–]{2,}')
HEADER_KEYWORDS: Final[re.Pattern[str]] = re.compile(
    r'\b(ACCOUNT|STATEMENT|PAGE|SCHEDULE|TOTAL|BALANCE|ENDING|'
    r'DEPOSITS|DEPOSITS AND CREDITS|WITHDRAWALS|CHECKS|DEBITS)\b',
    re.I,
)

type DateRange = tuple[date, date]


@dataclass(frozen=True, slots=True)
class Row:
    """Строка-кандидат: дата, слова описания и денежные токены."""

    date: date
    description: str
    money: tuple[Word, ...]
    line: Line

    @property
    def source(self) -> Source:
        return Source(
            # Номер страницы наружу — человеческий, с единицы: провенанс читает
            # аудитор, а не индексирует программа.
            page=self.line.page + 1,
            bbox=self.line.bbox,
            engine=self.line.words[0].engine,
            confidence=self.line.confidence,
        )


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """Прочтение страницы одним способом плюс то, что при нём осталось за бортом."""

    name: str
    transactions: list[Transaction]
    repaired: int = 0
    skipped: int = 0
    suspect_pages: frozenset[int] = frozenset()
    """Страницы, где что-то не сошлось: пропущенная строка, ремонт, разрыв.

    Это адрес для эскалации. Перечитывать дорогим движком весь период, когда
    спорны три страницы из пятнадцати, — платить за то, что уже прочитано."""
    unplaced_rows: list['UnplacedRow'] = field(default_factory=list)
    """Строки с денежной информацией, не размещённые в цепочке. Для аудита."""


def _clean(text: str) -> str:
    words = [w for w in NOISE.sub(' ', text).split() if any(c.isalnum() for c in w)]

    return ' '.join(words).strip(' -|')


def _describe(words: list[Word]) -> str:
    return _clean(' '.join(w.text for w in words))


def _skip_continuation(words: list[Word]) -> bool:
    """Проверить, что строка — НЕ продолжение, а часть служебной информации.

    Используется при приклейке строк-продолжений (описаний): если строка
    содержит ключевые слова итогов или заголовков, то это не продолжение
    описания операции, а служебная информация.
    """
    text = ' '.join(w.text for w in words)

    if HEADER_KEYWORDS.search(text):
        return True

    # Одинокое слово без единой цифры описанием контрагента не бывает.
    return len(words) <= 1 and not any(c.isdigit() for w in words for c in w.text)


def collect_rows(lines: list[Line], period: DateRange | None) -> list[Row]:
    """Строки, начинающиеся с даты; строки-продолжения дописываются в описание.

    Продолжение — строка без даты и без сумм: банки переносят так реквизиты
    контрагента. Приклеивать её к предыдущей операции безопасно, потому что
    сумм она не несёт и на арифметику повлиять не может.

    Исключение: строки со служебной информацией (заголовки таблиц, итоги,
    колонтитулы) не приклеиваются, даже если выглядят как продолжения.
    """
    rows: list[Row] = []
    # Порядок день/месяц — свойство документа, а не строки: он выводится по всем
    # датам сразу и применяется ко всем.
    day_first = infer_day_first(w.text for line in lines for w in line.words[:DATE_SEARCH_WORDS])

    for line in lines:
        words = list(line.words)

        if not words:
            continue

        found = _leading_date(words, period, day_first)
        money = tuple(w for w in words if is_money(w.text))

        if found is None:
            # Продолжение не переходит границу страницы: первое, что стоит на
            # новой странице без даты и суммы, — это колонтитул и логотип, и
            # «PAYMENT» превращался бы в «PAYMENT IXONIA BANK».
            if not money and rows and rows[-1].line.page == line.page and not _skip_continuation(words):
                tail = _describe(words)

                if tail:
                    rows[-1] = _with_description(rows[-1], f'{rows[-1].description} {tail}'.strip())

            continue

        at, first = found

        if not _within_period(first, period):
            continue

        body = [w for w in words[at + 1 :] if not is_money(w.text)]
        rows.append(Row(date=first, description=_describe(body), money=money, line=line))

    return rows


PERIOD_SLACK: Final[timedelta] = timedelta(days=1)
"""Запас к границам периода — ровно сутки. Выписка датирует строку остатка
последним днём предыдущего месяца, и это единственное законное отступление;
всё, что дальше, приходит с вклеенных образов чужих документов."""


def _within_period(when: date, period: DateRange | None) -> bool:
    """Дата операции лежит в периоде выписки.

    Проверка отсекает микротекст на вклеенных образах чеков: там встречаются
    свои даты и свои суммы, и по виду такая строка неотличима от операции.
    Дата — единственное, чем она себя выдаёт.
    """
    if period is None:
        return True

    return period[0] - PERIOD_SLACK <= when <= period[1] + PERIOD_SLACK


DATE_SEARCH_WORDS: Final[int] = 3
"""Дата операции стоит первой, но перед ней случается мусор от линовки: Binder
отдаёт строку как 'nite 4, ~Apr02 GENUINE PARTS...'. Три слова — это запас на
такой мусор и одновременно гарантия, что дата из описания не будет принята за
дату операции."""


def _leading_date(words: list[Word], period: DateRange | None, day_first: bool = False) -> tuple[int, date] | None:
    for index, word in enumerate(words[:DATE_SEARCH_WORDS]):
        if is_money(word.text):
            return None  # сумма раньше даты — это не строка операции

        if (parsed := parse_date(word.text, period=period, day_first=day_first)) is not None:
            return index, parsed

    return None


def _with_description(row: Row, description: str) -> Row:
    return Row(date=row.date, description=description, money=row.money, line=row.line)

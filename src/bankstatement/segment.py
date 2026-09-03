"""Деление документа на выписки и сбор реквизитов по согласию страниц.

Один PDF не равен одной выписке: Binder — это подшивка из десяти выписок за
разные месяцы, среди которых два разных счёта. Границу задаёт не колонтитул и не
сброс нумерации страниц (и то, и другое OCR читает плохо), а блок сводки:
выписка начинается там, где объявлен её остаток на начало, и рядом стоит хотя бы
ещё один итог того же блока.

Реквизиты собираются голосованием страниц. Логотип и номер счёта печатаются в
шапке каждой страницы, и одно неудачное распознавание перевешивается десятком
удачных — на скане это разница между 'bslxonia Bank' и 'Ixonia Bank'.
"""

from collections import Counter
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from typing import Final

from bankstatement.header import build_account, build_summary, find_period
from bankstatement.ingest import Ingestion
from bankstatement.layout import Line
from bankstatement.models import Account, Period, Summary


@dataclass(frozen=True, slots=True)
class Segment:
    """Одна выписка внутри документа: её страницы, шапка и реквизиты."""

    pages: tuple[int, ...]
    summary: Summary
    account: Account
    lines: tuple[Line, ...]


def summary_fields(summary: Summary) -> int:
    """Сколько из четырёх итогов шапки удалось прочитать."""
    return sum(
        1
        for value in (
            summary.beginning_balance,
            summary.ending_balance,
            summary.deposits_total,
            summary.withdrawals_total,
        )
        if value is not None
    )


def starts_statement(lines: list[Line]) -> bool:
    """Страница открывает выписку: остаток на начало объявлен внутри блока сводки.

    Одного остатка на начало мало: строка «Balance forward 1,500.00» стоит на
    каждой странице продолжения, и по ней документ распадался на столько выписок,
    сколько в нём страниц. Но и «двух итогов из четырёх» мало в другую сторону —
    замер по подшивке Ixonia: у всех десяти настоящих начал прочитаны все четыре
    итога и всегда есть остаток на начало, а у девяти ложных срабатываний ровно
    два итога и остатка на начало нет ни разу. Это постраничный колонтитул с
    остатком на конец и одним оборотом.

    Отсюда критерий: начало выписки объявляет свой остаток на начало, и рядом
    стоит хотя бы ещё один итог того же блока.
    """
    summary = build_summary(lines)

    return summary.beginning_balance is not None and summary_fields(summary) >= 2


@dataclass(frozen=True, slots=True)
class Opening:
    """Шапка страницы, открывающей выписку: итоги и границы периода."""

    summary: Summary
    period: Period | None


def same_statement(left: Opening, right: Opening) -> bool:
    """Две шапки описывают одну выписку: совпали и итоги, и период.

    Некоторые банки повторяют блок сводки на каждой странице, и без этой
    проверки двухстраничная выписка распадалась на две одностраничные с
    одинаковыми остатками. Одних итогов мало: два пустых месяца подряд печатают
    те же остатки и нулевые обороты, и различает их только период.
    """
    a, b = left.summary, right.summary
    same_totals = (
        a.beginning_balance == b.beginning_balance
        and a.ending_balance == b.ending_balance
        and a.deposits_total == b.deposits_total
        and a.withdrawals_total == b.withdrawals_total
    )
    same_period = left.period is None or right.period is None or left.period == right.period

    return same_totals and same_period


def merge_accounts(accounts: list[Account]) -> Account:
    """Реквизиты по большинству голосов среди страниц выписки."""

    def vote(values: list[str | None]) -> str | None:
        seen = Counter(v for v in values if v)

        return seen.most_common(1)[0][0] if seen else None

    def vote_period(periods: list[Period | None]) -> Period | None:
        seen = Counter(p for p in periods if p)

        return seen.most_common(1)[0][0] if seen else None

    return Account(
        bank=vote_bank([a.bank for a in accounts]),
        account_last4=vote([a.account_last4 for a in accounts]),
        period=vote_period([a.period for a in accounts]),
    )


def vote_bank(values: list[str | None]) -> str | None:
    """Имя банка по большинству, где приклеенный мусор голосует за чистое имя.

    Скан логотипа приносит 'EslIxonia Bank' рядом с 'Ixonia Bank': значок перед
    именем читается буквами и прилипает к первому слову. Настоящее слово к
    следующему не приклеивается никогда, поэтому такой вариант — голос за имя
    без него. Отдельное лишнее слово ('Onla Ixonia Bank') так не считается: по
    одной выписке не отличить мусор от потерянного слова, это решает документ.
    """
    counts = Counter(v for v in values if v)

    if not counts:
        return None

    def glued(short: str, long: str) -> bool:
        return long != short and long.lower().endswith(short.lower()) and long[-len(short) - 1].isalpha()

    support = {name: sum(n for other, n in counts.items() if other == name or glued(name, other)) for name in counts}

    return max(support, key=lambda name: (support[name], counts[name], -len(name)))


def reread(ingestion: Ingestion, part: Segment, level: int, pages: Collection[int] | None = None) -> Segment:
    """Перечитать выписку, подняв движок только для указанных страниц.

    Эскалация адресная: страницы, сошедшиеся с первого раза, остаются как
    прочитаны, и повторно за них не платят. Список приходит из разбора — это те
    страницы, где что-то не сошлось.

    Ступень призвана добавлять сведения, а не отбирать их, и правило действует
    дважды. Уже известные реквизиты не теряются: новое чтение может не найти
    границы периода, а без них разбор строк лишается года и рассыпается. И
    страница, которую ступень прочесть не смогла — модель отказалась, бюджет
    исчерпан, ответ пуст, — остаётся такой, какой её прочла предыдущая ступень:
    иначе перечит терял бы её строки целиком и проигрывал оракулу не качеством
    чтения, а пустым местом. Замер по подшивке: gpt-4o ответила отказом на пять
    страниц из двадцати, и все четыре перечита ушли в минус.
    """
    targets = set(part.pages if pages is None else pages)
    ingestion.prefetch([index for index in part.pages if index in targets], min_level=level)
    kept = _lines_by_page(part)
    by_page: list[list[Line]] = []

    for index in part.pages:
        fresh = ingestion.lines(index, min_level=level) if index in targets else []
        by_page.append(fresh or kept[index])

    reread_account = merge_accounts([build_account(lines) for lines in by_page])
    known = part.account

    # Прежние реквизиты в приоритете, новое чтение лишь заполняет пробелы. Имя
    # банка, прошедшее голосование по всем страницам прежней ступени, весомее
    # имени с одной перечитанной страницы: psm6 на одной странице июня подшивки
    # прочёл «lxonia ia Ban Bank», и при равном счёте голосов короткое «Ban
    # Bank» перекрывало «Ixonia Bank» всей выписки.
    return Segment(
        pages=part.pages,
        summary=build_summary(by_page[_summary_page(by_page)]),
        account=Account(
            bank=known.bank or reread_account.bank,
            account_last4=known.account_last4 or reread_account.account_last4,
            period=known.period or reread_account.period,
        ),
        lines=tuple(line for lines in by_page for line in lines),
    )


def _lines_by_page(part: Segment) -> dict[int, list[Line]]:
    """Принятые строки выписки по страницам."""
    return {index: [line for line in part.lines if line.page == index] for index in part.pages}


BATCH: Final[int] = 2
"""Во сколько раз пачка предчтения больше числа потоков. Пачками, а не всем
документом сразу: граница выписки видна задолго до конца подшивки, и первая
выписка может уйти на разбор, пока читаются остальные. Двойной запас держит
потоки занятыми на стыке пачек."""


def iter_segments(ingestion: Ingestion) -> Iterator[Segment]:
    """Выдавать выписки по мере того, как становится видна их граница.

    Ждать, пока прочитается весь документ, незачем: выписка закончена в тот
    момент, когда на следующей странице объявлен новый начальный остаток. У
    подшивки на сотню страниц это разница между «первый результат через две
    минуты» и «через десять секунд».
    """
    pages: list[list[Line]] = []
    start = 0
    opened = False
    current: Opening | None = None
    batch = max(1, ingestion.workers * BATCH)

    for first in range(0, ingestion.page_count, batch):
        chunk = range(first, min(first + batch, ingestion.page_count))
        ingestion.prefetch(chunk)

        for index in chunk:
            lines = ingestion.lines(index)
            pages.append(lines)

            if not starts_statement(lines):
                continue

            opening = Opening(summary=build_summary(lines), period=find_period(lines))

            if not opened:
                # Страницы до первого объявленного остатка — обложка и оборот с
                # инструкцией; они принадлежат первой выписке, а не отдельной.
                opened = True
                current = opening
                continue

            if current is not None and same_statement(opening, current):
                # Шапка повторена на каждой странице выписки: те же остатки и
                # тот же период — та же выписка, а не новая.
                continue

            yield _assemble(pages[start:index], start)
            start = index
            current = opening

    if start < len(pages):
        yield _assemble(pages[start:], start)


def _summary_page(pages: list[list[Line]]) -> int:
    """Страница сегмента, с которой брать шапку.

    Первая страница не годится: перед выпиской попадается обложка или
    сопроводительное письмо, и шапка тогда собирается с них — пустой. Берём
    страницу, где шапка сходится сама с собой, а если такой нет — самую полную.
    """
    best, score = 0, 0

    for index, lines in enumerate(pages):
        summary = build_summary(lines)

        if summary.self_check() == 0:
            return index

        if (fields := summary_fields(summary)) > score:
            best, score = index, fields

    return best


def _assemble(pages: list[list[Line]], first: int) -> Segment:
    """Собрать сегмент: шапка с подходящей страницы, реквизиты голосованием."""
    span = tuple(range(first, first + len(pages)))

    return Segment(
        pages=span,
        summary=build_summary(pages[_summary_page(pages)]),
        account=merge_accounts([build_account(lines) for lines in pages]),
        lines=tuple(line for lines in pages for line in lines),
    )


def segment(ingestion: Ingestion) -> list[Segment]:
    """Все выписки документа. Тот же разбор, что и потоковый, но целиком."""
    return list(iter_segments(ingestion))

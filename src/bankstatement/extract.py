"""Точка входа: PDF на входе, сведённый результат на выходе.

Здесь собирается вся сборка — распознавание, деление на выписки, гипотезы
разбора, приговор оракула. Выбор между гипотезами делается ровно одним
правилом: побеждает та, которую оракул признал сведённой. Если сведённой нет,
берётся ближайшая, и период честно помечается как не сошедшийся — с дельтой и
диагнозом. Для аудита пустая графа лучше уверенно неверной.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import msgspec

from bankstatement.ingest import Ingestion, PageCallback
from bankstatement.models import (
    ExtractionResult,
    Guarantee,
    Losses,
    Payload,
    Reconciliation,
    ReconStatus,
    Source,
    StatementPeriod,
    Summary,
    Transaction,
    UnplacedRow,
)
from bankstatement.parse import hypotheses
from bankstatement.reconcile import reconcile
from bankstatement.rows import Hypothesis
from bankstatement.segment import Segment, iter_segments, reread

log = logging.getLogger(__name__)

type PeriodCallback = Callable[[StatementPeriod], None]
"""Сигнал о том, что очередной период разобран и проверен."""


def extract(pdf_path: str) -> Payload:
    """Извлечь выписку. Контракт задания: путь к PDF -> словарь."""
    return extract_result(pdf_path).to_payload()


def extract_result(
    pdf_path: str | Path,
    on_page: PageCallback | None = None,
    on_period: PeriodCallback | None = None,
    *,
    max_pages: int | None = None,
    deadline: float | None = None,
) -> ExtractionResult:
    """То же, но моделями: у CLI, стенда и веб-слоя есть доступ к деталям.

    Периоды отдаются наблюдателю по мере готовности. В подшивке из десяти
    выписок первая сведена задолго до конца разбора, и держать её при себе — это
    заставлять человека ждать данные, которые уже есть.

    Args:
        pdf_path: Путь к PDF.
        on_page: Наблюдатель хода распознавания.
        on_period: Наблюдатель готовых периодов.
        max_pages: Отказать документу длиннее этого ещё до распознавания.
        deadline: Момент по `time.monotonic()`, после которого новые выписки не
            разбираются: результат отдаётся с теми, что готовы, и помечен как
            неполный.
    """
    periods: list[StatementPeriod] = []
    truncated = False

    with Ingestion(pdf_path, on_page=on_page, max_pages=max_pages) as ingestion:
        for part in iter_segments(ingestion):
            if deadline is not None and time.monotonic() > deadline:
                truncated = True
                log.warning('разбор %s прерван по времени: страницы с %d не разобраны', pdf_path, part.pages[0] + 1)

                break

            period = _build_period(ingestion, part)
            periods.append(period)

            if on_period is not None:
                _notify(on_period, period)

        stats = ingestion.engine_stats()
        page_count = ingestion.page_count

    return ExtractionResult(
        periods=_canonical_banks(periods),
        source_file=str(pdf_path),
        engine_stats=stats,
        page_count=page_count,
        truncated=truncated,
    )


def _canonical_banks(periods: list[StatementPeriod]) -> list[StatementPeriod]:
    """Имя банка согласуется по документу целиком.

    Логотип один на всех страницах подшивки, а читается по-разному: на одной
    выписке 'Ixonia Bank', на другой 'Onla Ixonia Bank' со значком, прочитанным
    буквами, на третьей ничего. Большинство выписок называет банк верно, и это
    имя получает каждая выписка, чьё чтение пусто либо отличается от него так,
    как отличается ошибка распознавания: лишним или потерянным словом с краю,
    приклеенным спереди мусором, одной перепутанной буквой. Второй признак —
    номер счёта: выписка по тому же счёту, что и выписки с именем большинства,
    ведётся тем же банком, как бы ни прочёлся логотип («Ban Bank» на одной
    выписке подшивки). Сам номер счёта так не согласуется: в одной подшивке
    бывает два счёта, и большинство здесь ничего не доказывает.
    """
    names = Counter(bank for p in periods if (bank := p.account.bank))

    if not names:
        return periods

    plurality = names.most_common(1)[0][0]
    same_account = {p.account.account_last4 for p in periods if p.account.bank == plurality and p.account.account_last4}
    out: list[StatementPeriod] = []

    for period in periods:
        bank = period.account.bank
        shared = period.account.account_last4 in same_account

        if bank is None or _misread_of(bank, plurality) or (bank != plurality and shared):
            account = msgspec.structs.replace(period.account, bank=plurality)
            period = msgspec.structs.replace(period, account=account)

        out.append(period)

    return out


def _misread_of(candidate: str, name: str) -> bool:
    """Могло ли `candidate` быть чтением `name` с одной ошибкой распознавания.

    Три формы, и все три встретились в одной подшивке: лишнее или потерянное
    слово с краю ('Onla Ixonia Bank'), значок логотипа, приклеенный к первому
    слову ('ESIxonia Bank'), одна перепутанная буква ('Lxonia Bank'). Два разных
    банка так друг на друга не похожи.
    """
    a, b = candidate.lower(), name.lower()

    if a == b:
        return False

    if a.endswith(' ' + b) or b.endswith(' ' + a):
        return True

    if a.endswith(b) and a[-len(b) - 1].isalpha():
        return True

    return len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) == 1


def _notify(on_period: PeriodCallback, period: StatementPeriod) -> None:
    """Сообщить о готовом периоде, не роняя разбор из-за наблюдателя."""
    try:
        on_period(period)
    except Exception:  # pragma: no cover - наблюдатель вспомогательный
        log.exception('наблюдатель периодов упал')


def _build_period(ingestion: Ingestion, part: Segment) -> StatementPeriod:
    best, verdict = choose(part)

    if verdict.status is not ReconStatus.RECONCILED:
        part, best, verdict = _escalate(ingestion, part, best, verdict)

    unplaced = _classified(best, part.summary)
    proven = _proven(verdict, part.summary, unplaced)
    summary = _with_computed_counts(part.summary, best.transactions, proven, part.pages[0] + 1)

    return StatementPeriod(
        account=part.account,
        summary=summary,
        transactions=best.transactions,
        reconciliation=verdict,
        pages=[index + 1 for index in part.pages],
        losses=Losses(placed=len(best.transactions), repaired=best.repaired, proven=proven, unplaced=unplaced),
    )


def _with_computed_counts(summary: Summary, transactions: list[Transaction], proven: bool, page: int) -> Summary:
    """Счётчики операций там, где банк их не печатает.

    Контракт задания требует число поступлений и списаний, а печатает его один
    банк из четырёх. У доказанного периода счётчик выведен той же арифметикой,
    что подтвердила состав операций, и потому не догадка; у недоказанного он
    остаётся пустым — цифра без подтверждения в отчёт не идёт. Происхождение
    честное: движок `computed`, а не страница с координатами.
    """
    if not proven:
        return summary

    computed: dict[str, int] = {}

    if summary.deposits_count is None:
        computed['deposits_count'] = sum(1 for t in transactions if t.deposit is not None)

    if summary.withdrawals_count is None:
        computed['withdrawals_count'] = sum(1 for t in transactions if t.withdrawal is not None)

    if not computed:
        return summary

    sources = {**summary.sources, **{field: Source(page=page, engine='computed') for field in computed}}

    return msgspec.structs.replace(summary, **computed, sources=sources)


def _classified(best: Hypothesis, summary: Summary) -> list[UnplacedRow]:
    """Неразмещённые строки с названной причиной.

    Оракул сюда не заглядывает: его дело — арифметика. Но выдать период, из
    которого молча выпала строка с датой и суммой, для аудита недопустимо, и
    этот учёт — единственное место, где такая строка становится видимой.
    """
    return [
        row if row.kind != 'lost' else msgspec.structs.replace(row, kind=_classify(row, best.transactions, summary))
        for row in best.unplaced_rows
    ]


def _proven(verdict: Reconciliation, summary: Summary, unplaced: list[UnplacedRow]) -> bool:
    """Доказан ли состав операций, а не только их сумма.

    Цепочка подтверждает каждую строку соседями, и лишняя операция в ней не
    уместилась бы: класс A доказан целиком. Совпадение обоих напечатанных
    оборотов подтверждает суммы, но не состав: это два уравнения, и строку, не
    размещённую и не объяснённую ни маркером, ни дублем, могла погасить другая
    ошибка той же величины. Класс B доказан лишь тогда, когда каждая
    неразмещённая строка объяснена. Одного совпадения остатков мало в любом
    случае: две пропущенные строки способны погасить друг друга.
    """
    if verdict.status is not ReconStatus.RECONCILED:
        return False

    if verdict.guarantee is Guarantee.CHAIN:
        return True

    both_totals = summary.deposits_total is not None and summary.withdrawals_total is not None

    return both_totals and all(row.kind != 'lost' for row in unplaced)


def _classify(row: UnplacedRow, transactions: list[Transaction], summary: Summary) -> str:
    """Чем на самом деле была строка, не ставшая операцией.

    Сумма, равная напечатанному итогу, — это маркер остатка
    («ENDING BALANCE FROM PRIOR STATEMENT»), а не потеря. Суммы, совпавшие с уже
    размещёнными операциями, — это блок сводки, который их повторяет (Check
    Summary у Fulton). Правило безопасно там, где оно применяется: у сведённого
    периода оба оборота совпали с напечатанными, значит ещё одна операция с той
    же суммой в него просто не поместилась бы.

    Args:
        row: Неразмещённая строка.
        transactions: Операции, которые разбор всё-таки разместил.
        summary: Итоги из шапки.

    Returns:
        `marker`, `duplicate` или `lost` — и только последнее является потерей.
    """
    printed = {
        abs(value)
        for value in (
            summary.beginning_balance,
            summary.ending_balance,
            summary.deposits_total,
            summary.withdrawals_total,
        )
        if value is not None
    }
    amounts = [abs(value) for value in row.amounts]

    if any(value in printed for value in amounts):
        return 'marker'

    placed = {abs(t.signed_amount) for t in transactions}

    if amounts and all(value in placed for value in amounts):
        return 'duplicate'

    return 'lost'


def _escalate(
    ingestion: Ingestion, part: Segment, best: Hypothesis, verdict: Reconciliation
) -> tuple[Segment, Hypothesis, Reconciliation]:
    """Переспросить забракованный период на следующих ступенях распознавания.

    Эскалация адресная вдвойне: только по страницам, где разбор что-то не свёл,
    и только пока период не сведён. Цена извлечения определяется трудностью
    документа, а не его размером. Результат заменяется лишь при меньшей невязке —
    переспросить хуже, чем было, тоже возможно, и такой ответ принимать нельзя.
    """
    for level in range(1, len(ingestion.ladder)):
        suspect = best.suspect_pages & set(part.pages)
        targets = suspect or set(part.pages)

        # Ступени, которой нечего читать — у модели кончился бюджет страниц, —
        # вопрос не задаётся: перечит без единой новой страницы повторил бы
        # прежний результат и оплатил бы его лишним прогоном гипотез.
        if not any(ingestion.ladder[level].available_for(index) for index in targets):
            continue

        retried = reread(ingestion, part, level, suspect or None)
        guess, check = choose(retried)
        accepted = _better(check, verdict)

        log.info(
            'эскалация: период с %s, ступень %d, страниц %d, невязка %s -> %s, строк %d -> %d: %s',
            part.account.period.start if part.account.period else '?',
            level,
            len(targets),
            verdict.residual,
            check.residual,
            len(best.transactions),
            len(guess.transactions),
            'принято' if accepted else 'отвергнуто',
        )

        if accepted:
            part, best, verdict = retried, guess, check

        if verdict.status is ReconStatus.RECONCILED:
            break

    return part, best, verdict


def _better(candidate: Reconciliation, current: Reconciliation) -> bool:
    """Стоит ли принять перечитанный результат вместо прежнего.

    Меньшая невязка — обычный критерий, но у слепого вердикта она нулевая
    даром: без итогов в шапке или без единой строки сравнивать нечего, и дельта
    равна нулю не потому, что всё сошлось. Такой перечит не заменяет результат,
    в котором было что сверять; и наоборот, любое зрячее чтение лучше слепого.
    """
    if candidate.status is ReconStatus.RECONCILED:
        return True

    if _blind(candidate):
        return False

    if _blind(current):
        return True

    return candidate.residual < current.residual


def _blind(verdict: Reconciliation) -> bool:
    return any(d.kind in {'no_reference', 'no_rows'} for d in verdict.diagnoses)


def choose(part: Segment) -> tuple[Hypothesis, Reconciliation]:
    """Отдать гипотезы оракулу и выбрать ту, что он принял.

    Сортировка объясняет приоритеты целиком: сначала сведённые, затем те, что
    вообще что-то прочли, затем меньшая полная невязка, затем меньше разрывов
    цепочки, и лишь в конце — больше строк. Число строк стоит последним
    намеренно: жадный разбор всегда даёт их больше, и, окажись он раньше, он бы
    выигрывал у правильного.

    Пустое прочтение отдельно и раньше невязки: когда в шапке напечатаны только
    остатки, его невязка равна их разности, и почти полная цепочка с одной
    потерянной строкой проигрывала бы ему по числу. Ноль строк — не разбор, а
    его отсутствие, и выиграть он вправе только у такого же нуля.
    """
    span = (part.account.period.start, part.account.period.end) if part.account.period else None
    lines = list(part.lines)
    scored = [(guess, reconcile(part.summary, guess.transactions)) for guess in hypotheses(lines, part.summary, span)]
    scored.sort(
        key=lambda pair: (
            pair[1].status is not ReconStatus.RECONCILED,
            not pair[0].transactions,
            pair[1].residual,
            pair[1].chain_breaks,
            -len(pair[0].transactions),
        )
    )

    return scored[0]

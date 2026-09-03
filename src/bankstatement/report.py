"""Человекочитаемый отчёт по результату извлечения.

Отчёт устроен вокруг одного вопроса: чему здесь можно верить. Поэтому у каждого
периода на виду стоит не число транзакций, а вердикт оракула и класс гарантии, а
у несошедшегося периода — дельта и её диагноз. Красивую сводку, скрывающую
расхождение, для аудита писать нельзя.
"""

from bankstatement.models import ExtractionResult, Guarantee, Opinion, ReconStatus, StatementPeriod

GUARANTEE_TEXT = {
    Guarantee.CHAIN: 'A: каждая строка подтверждена цепочкой баланса',
    Guarantee.AGGREGATE: 'B: подтверждены итоги, построчной проверки нет',
    Guarantee.NONE: 'C: не подтверждено',
}

OPINION_TEXT = {
    Opinion.CLEAN: 'ПОДТВЕРЖДЕНО — все выписки сошлись с арифметикой документа',
    Opinion.QUALIFIED: 'ПОДТВЕРЖДЕНО ЧАСТИЧНО — часть выписок требует проверки',
    Opinion.ADVERSE: 'НЕ ПОДТВЕРЖДЕНО — не сошлась ни одна выписка',
    Opinion.DISCLAIMER: 'ЗАКЛЮЧЕНИЕ НЕВОЗМОЖНО — выписок в документе не нашлось',
}


def render(result: ExtractionResult) -> str:
    """Отчёт от вывода к подробностям, как это принято в аудите.

    Сначала заключение, затем объём проверки, затем список того, что требует
    человека, и только потом периоды целиком. Читатель, которому нужен один
    ответ, получает его в первой строке и дальше может не идти.
    """
    lines = [*_verdict(result), *_findings(result)]

    for index, period in enumerate(result.periods, start=1):
        lines.extend(_period_block(index, period))

    return '\n'.join(lines)


def _verdict(result: ExtractionResult) -> list[str]:
    return [
        OPINION_TEXT[result.opinion],
        *(['РАЗБОР ПРЕРВАН ПО ВРЕМЕНИ: показаны только выписки, готовые к этому моменту'] if result.truncated else []),
        '',
        f'файл: {result.source_file}',
        f'проверено: выписок {len(result.periods)}, страниц {result.pages_total}, операций {result.transactions_total}',
        f'сведено: {result.reconciled_periods} из {len(result.periods)}',
        f'распознавание: {_engines(result)}',
        '',
    ]


def _findings(result: ExtractionResult) -> list[str]:
    """Сводка расхождений — по убыванию суммы, а не по порядку в документе."""
    if not result.failures:
        return []

    lines = [f'ТРЕБУЮТ ПРОВЕРКИ ({len(result.failures)}), нераскрыто {result.unexplained:,}:']
    # Номер блока по тождеству объекта, а не по значению: два периода одного
    # месяца в подшивке различаются только содержимым, и list.index вернул бы
    # первый для обоих.
    order = {id(period): index for index, period in enumerate(result.periods, start=1)}
    width = len(str(len(result.periods)))

    for period in result.failures:
        kinds = ', '.join(d.kind for d in period.reconciliation.diagnoses) or 'причина не определена'
        lines.append(
            f'  [{order[id(period)]:>{width}}] {_span(period)}  дельта {period.reconciliation.delta:>14,}  {kinds}'
        )

    return [*lines, '']


def _span(period: StatementPeriod) -> str:
    account = period.account

    return f'{account.period.start} .. {account.period.end}' if account.period else 'период не определён'


def _engines(result: ExtractionResult) -> str:
    if not result.engine_stats:
        return 'нет данных'

    return ', '.join(f'{name}: {pages} стр.' for name, pages in sorted(result.engine_stats.items()))


def _period_block(index: int, period: StatementPeriod) -> list[str]:
    account, summary, verdict = period.account, period.summary, period.reconciliation
    span = _span(period)
    mark = '✓' if verdict.status is ReconStatus.RECONCILED else '✗'
    pages = f'{period.pages[0]}-{period.pages[-1]}' if period.pages else '—'

    block = [
        f'[{index}] {mark} {span}  стр. {pages}',
        f'    счёт: {account.bank or "банк не определён"} ****{account.account_last4 or "????"}',
        f'    начало {_money(summary.beginning_balance)}  '
        f'+{_money(summary.deposits_total)}  -{_money(summary.withdrawals_total)}  '
        f'конец {_money(summary.ending_balance)}',
        f'    транзакций: {len(period.transactions)}   гарантия {GUARANTEE_TEXT[verdict.guarantee]}',
    ]

    if verdict.status is not ReconStatus.RECONCILED:
        block.append(f'    НЕ СВЕДЕНО: дельта {verdict.delta}, полная невязка {verdict.residual}')
        block.extend(f'      - {d.kind}: {d.detail}' for d in verdict.diagnoses)

    lost = period.losses.lost

    if lost:
        # Потеря — единственное, что арифметика периода не видит: сошедшиеся
        # итоги ничего не говорят о строке, которая в них не попала.
        block.append(f'    НЕ РАЗМЕЩЕНО СТРОК: {len(lost)} (с датой и суммой, не объяснены сводкой)')

        for row in lost[:3]:
            amounts = ', '.join(f'{value:,}' for value in row.amounts)
            page = f' стр. {row.source.page}' if row.source else ''
            block.append(f'      - {row.date} {row.description[:44]} ({amounts}){page}')

    block.append('')

    return block


def _money(value: object) -> str:
    return f'{value:>14,}' if value is not None else '{:>14}'.format('—')

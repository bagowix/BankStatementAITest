"""Как монитор цен выглядит в месячном отчёте клиенту.

Сэкономленное время считается только за проверки: открыть страницу конкурента, найти
цену и перенести её в таблицу — около двух минут у человека. Изменения и «дешевле вас»
— это ценность, а не минуты, поэтому они в отчёте идут счётчиками без минут.
"""

from collections import Counter
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Final

from autostudio.events import Event
from autostudio.prices import texts
from autostudio.report import MetricSpec

METRICS: Final[list[MetricSpec]] = [
    MetricSpec(
        kind='prices.checked',
        label={'en': 'Competitor prices checked', 'ru': 'Проверено цен конкурентов'},
        minutes_saved=2.0,
    ),
    MetricSpec(kind='prices.changed', label={'en': 'Price changes caught', 'ru': 'Замечено изменений цен'}),
    MetricSpec(
        kind='prices.undercut',
        label={'en': 'Times a competitor became cheaper than you', 'ru': 'Конкурент стал дешевле вас'},
    ),
    MetricSpec(
        kind='prices.stock_changed',
        label={'en': 'Competitor stock changes', 'ru': 'Изменения наличия у конкурентов'},
    ),
    MetricSpec(
        kind='prices.currency_changed',
        label={'en': 'Competitor currency changes', 'ru': 'Смена валюты у конкурентов'},
    ),
    MetricSpec(
        kind='prices.failed',
        label={'en': 'Pages that could not be checked', 'ru': 'Страницы, которые не удалось проверить'},
    ),
]

_TEXT: Final[dict[str, dict[str, str]]] = {
    'en': {
        'changes': 'Price changes caught: {n}',
        'aggressive': 'Most active competitor: {name} — price cuts: {cuts}, cheaper than you: {under} times',
        'expensive': 'You were the most expensive for: {items}',
    },
    'ru': {
        'changes': 'Замечено изменений цен: {n}',
        'aggressive': 'Самый активный конкурент: {name} — снижений цены: {cuts}, дешевле вас: {under} раз(а)',
        'expensive': 'Вы были дороже всех по: {items}',
    },
}
_TOP: Final = 3


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def highlights(events: Sequence[Event], language: str) -> list[str]:
    """«Главное за месяц» из событий монитора.

    Args:
        events: События пакета `prices` за месяц, по времени.
        language: Язык клиента.

    Returns:
        Строки для отчёта: сколько изменений поймано, самый активный конкурент, товары,
        где клиент дороже всех (по последней проверке месяца).
    """
    t = _TEXT[texts.lang(language)]
    out: list[str] = []
    changed = [event for event in events if event.kind == 'prices.changed']
    if changed:
        out.append(t['changes'].format(n=len(changed)))

    cuts: Counter[str] = Counter()
    under: Counter[str] = Counter()
    for event in events:
        competitor = event.payload.get('competitor', '?')
        pct = _decimal(event.payload.get('pct'))
        if event.kind == 'prices.changed' and pct is not None and pct < 0:
            cuts[competitor] += 1
        elif event.kind == 'prices.undercut':
            under[competitor] += 1
    activity = cuts + under
    if activity:
        name, _ = min(activity.items(), key=lambda item: (-item[1], item[0]))
        out.append(t['aggressive'].format(name=name, cuts=cuts[name], under=under[name]))

    latest: dict[tuple[str, str], Decimal] = {}
    for event in events:
        diff = _decimal(event.payload.get('diff_vs_own_pct'))
        if event.kind == 'prices.checked' and diff is not None:
            latest[(event.payload.get('product', '?'), event.payload.get('competitor', '?'))] = diff
    by_product: dict[str, list[Decimal]] = {}
    for (product, _), diff in latest.items():
        by_product.setdefault(product, []).append(diff)
    # Дороже всех — когда каждый сравнимый конкурент дешевле; сортировка — по самому дешёвому.
    expensive = sorted((min(diffs), product) for product, diffs in by_product.items() if max(diffs) < 0)
    if expensive:
        items = ', '.join(f'{product} ({texts.show_pct(diff)}%)' for diff, product in expensive[:_TOP])
        out.append(t['expensive'].format(items=items))
    return out

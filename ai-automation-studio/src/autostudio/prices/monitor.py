"""Один проход монитора: прочитать все ссылки, сравнить с прошлым, записать, сообщить.

Порядок записи выбран так, чтобы ничего не терялось молча:

1. все ссылки проверяются (сайты — параллельно, страницы одного сайта — по очереди);
2. сравнение с прошлой ценой — чистой функцией `assess`, в `Decimal`;
3. таблица клиента: «Prices now» заменяется, «History» и «Changes» дописываются;
4. только после удачной записи таблицы — история в SQLite одной транзакцией и журнал
   событий. Упала таблица — в базе ничего не изменилось, и следующий запуск повторит ту же
   работу (а `supervised_run` сообщит оператору);
5. одно уведомление владельцу за запуск — если было что-то значимое — и отдельно
   оператору о сломанных ссылках (один раз на пороге, а не каждый запуск).

Сбой одной ссылки — даже неожиданное исключение на странной странице — не останавливает
остальные: он становится строкой «не проверено» и сигналом оператору.
"""

import asyncio
import datetime as dt
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Final, Literal, Protocol

import httpx
import msgspec

from autostudio.errors import NotifyError
from autostudio.events import EventLog
from autostudio.llm import LLMClient
from autostudio.notify import Notifier
from autostudio.prices import texts
from autostudio.prices.config import Offer, PricesConfig, PricesSection, Product
from autostudio.prices.extract import Availability, ExtractionFailure, LLMBudget, PriceReading, read_price
from autostudio.prices.fetch import FetchFailure, FetchResult
from autostudio.prices.money import Qualifier
from autostudio.prices.store import OfferKey, OfferState, PriceStore, StoredReading
from autostudio.prices.urls import origin, redact
from autostudio.sinks import Row, TableSink

logger = logging.getLogger(__name__)

PACKAGE: Final = 'prices'
# Сколько сайтов проверяется одновременно. Страницы одного сайта всё равно идут по очереди
# с паузой — параллельность ускоряет запуск, не увеличивая нагрузку ни на один сайт.
_PARALLEL_SITES: Final = 4
_SHEETS_URL: Final = 'https://docs.google.com/spreadsheets/d/{id}'
# Предупреждения удачного чтения — оператору, один раз, когда предупреждение появилось.
_WARNINGS: Final[dict[str, str]] = {
    'selector_not_found': (
        'селектор {selector!r} не находит цену — цена взята через {method}. '
        'Обновите селектор: autostudio prices probe {url}'
    ),
    'selector_no_price': (
        'селектор {selector!r} находит элемент, но цены в нём нет — цена взята через {method}. '
        'Обновите селектор: autostudio prices probe {url}'
    ),
    'schema_thousands_dot': (
        'в разметке цена записана с точкой-разделителем тысяч ({raw}); на странице видно то же число '
        'тысячами — так и прочитано. Сверьте с сайтом: {url}'
    ),
    'schema_ambiguous_decimal': (
        'в разметке цена «{raw}»: по стандарту это дробь, но может быть и тысячами, а на странице '
        'подтверждения нет. Сверьте с сайтом и при необходимости задайте селектор: {url}'
    ),
}

type ChangeKind = Literal['price', 'undercut', 'stock', 'currency']


class Fetcher(Protocol):
    """То, что монитор требует от загрузчика страниц."""

    async def fetch(self, url: str) -> FetchResult | FetchFailure: ...


class PricesDeps(msgspec.Struct, frozen=True, kw_only=True):
    """Зависимости прохода монитора.

    Attributes:
        new_fetcher: Новый загрузчик на каждый запуск (кэш robots.txt живёт один запуск).
        store: История цен.
        events: Журнал событий клиента.
        sink: Таблица клиента.
        notifier: Канал владельца бизнеса.
        operator: Канал оператора или None.
        llm: Модель для ступени без разметки или None.
    """

    new_fetcher: Callable[[], Fetcher]
    store: PriceStore
    events: EventLog
    sink: TableSink
    notifier: Notifier
    operator: Notifier | None = None
    llm: LLMClient | None = None


class CheckFailure(msgspec.Struct, frozen=True, kw_only=True):
    """Ссылку не удалось проверить."""

    reason: str
    detail: str = ''


type CheckOutcome = PriceReading | CheckFailure


class Change(msgspec.Struct, frozen=True, kw_only=True):
    """Значимое событие по ссылке — строка вкладки «Changes» и пункт уведомления.

    Attributes:
        kind: `price` — цена изменилась на порог и больше; `undercut` — конкурент стал
            дешевле клиента; `stock` — изменилось наличие; `currency` — сменилась валюта.
        product: Название товара.
        competitor: Конкурент.
        url: Страница.
        currency: Валюта новой цены.
        old_amount: Прежняя цена (для `price`, `currency`).
        new_amount: Новая цена.
        old_currency: Прежняя валюта (для `currency`).
        pct: Изменение в % (`price`) или разница с ценой клиента (`undercut`).
        own_price: Цена клиента.
        availability: Наличие сейчас (для `stock`).
    """

    kind: ChangeKind
    product: str
    competitor: str
    url: str
    currency: str | None
    old_amount: Decimal | None = None
    new_amount: Decimal | None = None
    old_currency: str | None = None
    pct: Decimal | None = None
    own_price: Decimal | None = None
    availability: Availability = Availability.UNKNOWN


class Assessment(msgspec.Struct, frozen=True, kw_only=True):
    """Итог одной ссылки в этом запуске (чистый результат `assess`).

    Attributes:
        key: Ссылка.
        product: Товар.
        offer: Страница из конфига.
        outcome: Прочитанная цена или сбой.
        previous: Прошлая цена из истории.
        changes: Значимые события.
        state: Новое состояние ссылки.
        diff_vs_own: Разница с ценой клиента в % (только в одной валюте).
        change_vs_last: Изменение к прошлой цене в % (только в одной валюте).
        new_value: Цена или наличие отличаются от прошлых (или это первое чтение).
        operator_lines: Что сказать оператору.
    """

    key: OfferKey
    product: Product
    offer: Offer
    outcome: CheckOutcome
    previous: StoredReading | None
    changes: tuple[Change, ...] = ()
    state: OfferState = OfferState()
    diff_vs_own: Decimal | None = None
    change_vs_last: Decimal | None = None
    new_value: bool = False
    operator_lines: tuple[str, ...] = ()


class RunResult(msgspec.Struct, frozen=True, kw_only=True):
    """Итог прохода для CLI, демо и тестов."""

    checked: int
    failed: int
    changes: tuple[Change, ...]
    digest: str | None
    operator_alert: str | None
    llm_pages: int
    methods: dict[str, int] = msgspec.field(default_factory=dict)
    failures: dict[str, int] = msgspec.field(default_factory=dict)


def pct_change(old: Decimal, new: Decimal) -> Decimal:
    """Изменение в процентах, целиком в `Decimal` (old > 0)."""
    return (new - old) / old * 100


def _comparable(amount_currency: str | None, product: Product) -> bool:
    return product.own_price is not None and amount_currency == product.currency


def _undercuts(product: Product, currency: str | None, amount: Decimal, availability: Availability) -> bool:
    # Цена «от» и цена товара, которого нет в наличии, клиента не подрезают.
    if not _comparable(currency, product) or availability is Availability.OUT_OF_STOCK:
        return False
    return product.own_price is not None and amount < product.own_price


def assess(
    product: Product,
    offer: Offer,
    outcome: CheckOutcome,
    *,
    previous: StoredReading | None,
    state: OfferState,
    section: PricesSection,
) -> Assessment:
    """Сравнивает результат проверки с прошлым: изменения, подрезание, наличие, сбои.

    Первое чтение — не «изменение»: сравнивать не с чем. Валюты не смешиваются: другая
    валюта — не процент изменения и не «дешевле вас», а отдельная пометка.

    Args:
        product: Товар клиента.
        offer: Страница конкурента.
        outcome: Цена или сбой этого запуска.
        previous: Последняя прочитанная цена или None.
        state: Состояние ссылки до запуска.
        section: Пороги из конфига.

    Returns:
        Итог ссылки.
    """
    key = OfferKey(product.id, offer.competitor, offer.url)
    where = f'{product.name} / {offer.competitor}'
    if isinstance(outcome, CheckFailure):
        failures = state.failures + 1
        lines: tuple[str, ...] = ()
        if failures == section.failure_alert_after:
            lines = (f'• {where}: {outcome.reason} ({outcome.detail}) — сбоев подряд: {failures}; {redact(offer.url)}',)
        new_state = OfferState(failures=failures, last_reason=outcome.reason, warning=state.warning)
        return Assessment(
            key=key,
            product=product,
            offer=offer,
            outcome=outcome,
            previous=previous,
            state=new_state,
            operator_lines=lines,
        )

    reading = outcome
    changes: list[Change] = []
    change_vs_last: Decimal | None = None
    base = Change(
        kind='price',
        product=product.name,
        competitor=offer.competitor,
        url=offer.url,
        currency=reading.currency,
        own_price=product.own_price,
    )
    replace = msgspec.structs.replace
    if previous is not None:
        if previous.currency == reading.currency and previous.amount > 0:
            change_vs_last = pct_change(previous.amount, reading.amount)
            if abs(change_vs_last) >= section.alert_change_pct:
                changes.append(replace(base, old_amount=previous.amount, new_amount=reading.amount, pct=change_vs_last))
        elif previous.currency == reading.currency:
            # Было «бесплатно» (allow_free): процента от нуля нет, но само изменение — есть.
            if reading.amount != previous.amount:
                changes.append(replace(base, old_amount=previous.amount, new_amount=reading.amount))
        elif previous.currency is not None and reading.currency is not None:
            changes.append(
                replace(
                    base,
                    kind='currency',
                    old_amount=previous.amount,
                    new_amount=reading.amount,
                    old_currency=previous.currency,
                )
            )
        # Если одна из валют не определилась — не сравниваем и не тревожим клиента впустую.
        known = Availability.UNKNOWN not in (previous.availability, reading.availability)
        if known and previous.availability != reading.availability:
            changes.append(replace(base, kind='stock', availability=reading.availability))
    diff = (
        pct_change(product.own_price, reading.amount)
        if product.own_price and _comparable(reading.currency, product)
        else None
    )
    exact = reading.qualifier is Qualifier.EXACT
    if section.alert_undercut and exact and _undercuts(product, reading.currency, reading.amount, reading.availability):
        was = previous is not None and _undercuts(product, previous.currency, previous.amount, previous.availability)
        if not was:
            changes.append(replace(base, kind='undercut', new_amount=reading.amount, pct=diff))

    warning = reading.warnings[0] if reading.warnings else None
    lines = ()
    if warning is not None and warning != state.warning:
        template = _WARNINGS.get(warning, '{warning}')
        text = template.format(
            warning=warning, selector=offer.selector, method=reading.method.value, raw=reading.raw, url=offer.url
        )
        lines += (f'• {where}: {text}',)
    if state.failures >= section.failure_alert_after:
        lines += (f'• {where}: снова читается (сбоев подряд было: {state.failures})',)
    new_value = (
        previous is None
        or section.sheet_history == 'all'
        or (previous.amount, previous.currency, previous.availability)
        != (reading.amount, reading.currency, reading.availability)
    )
    return Assessment(
        key=key,
        product=product,
        offer=offer,
        outcome=reading,
        previous=previous,
        changes=tuple(changes),
        state=OfferState(failures=0, last_reason=None, warning=warning),
        diff_vs_own=diff,
        change_vs_last=change_vs_last,
        new_value=new_value,
        operator_lines=lines,
    )


def _path_depth(url: str) -> int:
    return len([part for part in httpx.URL(url).path.split('/') if part])


async def check_offer(
    product: Product,
    offer: Offer,
    fetcher: Fetcher,
    *,
    llm: LLMClient | None,
    budget: LLMBudget,
    section: PricesSection,
) -> CheckOutcome:
    """Загружает и читает одну страницу; любой сбой превращается в `CheckFailure`.

    Args:
        product: Товар.
        offer: Страница конкурента.
        fetcher: Загрузчик.
        llm: Модель или None.
        budget: Бюджет модели на запуск.
        section: Настройки монитора.

    Returns:
        Цена или сбой.
    """
    try:
        page = await fetcher.fetch(offer.url)
        if isinstance(page, FetchFailure):
            return CheckFailure(reason=page.reason, detail=page.detail)
        if _path_depth(page.final_url) < _path_depth(offer.url):
            # Удалённый товар часто уводит редиректом в каталог, где разметка описывает
            # другой товар. Цена оттуда была бы молча неверной — лучше честный пропуск.
            return CheckFailure(reason='redirected_away', detail=f'→ {redact(page.final_url)}')
        result = await read_price(
            page.html, offer, product_name=product.name, llm=llm, budget=budget, allow_free=section.allow_free
        )
    except Exception as exc:
        # Одна странная страница не должна останавливать мониторинг остальных: трассировка
        # уходит в лог, оператор получает сбой ссылки, остальные ссылки проверяются.
        logger.exception('проверка %s / %s упала', product.id, offer.competitor)
        return CheckFailure(reason='internal_error', detail=type(exc).__name__)
    if isinstance(result, ExtractionFailure):
        return CheckFailure(reason=result.reason, detail=result.detail)
    return result


async def check_all(
    section: PricesSection, fetcher: Fetcher, *, llm: LLMClient | None, budget: LLMBudget
) -> list[tuple[Product, Offer, CheckOutcome]]:
    """Проверяет все ссылки: сайты параллельно, страницы одного сайта — последовательно.

    Args:
        section: Товары и настройки.
        fetcher: Загрузчик этого запуска.
        llm: Модель или None.
        budget: Бюджет модели.

    Returns:
        Результаты в порядке конфига (таблица и уведомления стабильны от запуска к запуску).
    """
    pairs = [(product, offer) for product in section.products for offer in product.offers]
    results: dict[int, CheckOutcome] = {}
    by_site: dict[str, list[int]] = {}
    for index, (_, offer) in enumerate(pairs):
        by_site.setdefault(origin(offer.url), []).append(index)
    gate = asyncio.Semaphore(_PARALLEL_SITES)

    async def worker(indexes: list[int]) -> None:
        async with gate:
            for index in indexes:
                product, offer = pairs[index]
                results[index] = await check_offer(product, offer, fetcher, llm=llm, budget=budget, section=section)

    await asyncio.gather(*(worker(indexes) for indexes in by_site.values()))
    return [(product, offer, results[index]) for index, (product, offer) in enumerate(pairs)]


# --- таблица -------------------------------------------------------------------------------


def _status(outcome: CheckOutcome, previous: StoredReading | None, language: str) -> str:
    t = texts.STATUS[language]
    if isinstance(outcome, CheckFailure):
        reason = texts.failure_text(outcome.reason, language)
        return t['stale'].format(reason=reason) if previous is not None else reason
    return t[outcome.qualifier.value] if outcome.qualifier is not Qualifier.EXACT else t['ok']


def now_row(item: Assessment, *, at: dt.datetime, zone: dt.tzinfo, language: str) -> Row:
    """Строка вкладки «Prices now»: текущая цена или последняя известная с пометкой."""
    product, offer = item.product, item.offer
    own = texts.cell_amount(product.own_price) if product.own_price is not None else ''
    shown: PriceReading | StoredReading | None = (
        item.outcome if isinstance(item.outcome, PriceReading) else item.previous
    )
    checked = at if isinstance(item.outcome, PriceReading) else (item.previous.at if item.previous else None)
    if shown is None:
        price = currency = diff = availability = method = ''
    else:
        price, currency = texts.cell_amount(shown.amount), shown.currency or ''
        availability = texts.AVAILABILITY[language][shown.availability.value]
        method = texts.METHODS[language][shown.method.value]
        if product.own_price is None:
            diff = ''
        elif _comparable(shown.currency, product):
            diff = texts.cell_pct(pct_change(product.own_price, shown.amount))
        else:
            diff = texts.STATUS[language]['na_currency'].format(theirs=shown.currency or '?', yours=product.currency)
    change = texts.cell_pct(item.change_vs_last) if item.change_vs_last is not None else ''
    return [
        product.name,
        own,
        offer.competitor,
        price,
        currency,
        diff,
        change,
        availability,
        method,
        texts.show_time(checked, zone) if checked else '',
        _status(item.outcome, item.previous, language),
        offer.url,
    ]


def history_row(item: Assessment, reading: PriceReading, *, at: dt.datetime, zone: dt.tzinfo, language: str) -> Row:
    """Строка вкладки «History» — с происхождением цены (источник и текст со страницы)."""
    return [
        texts.show_time(at, zone),
        item.product.name,
        item.offer.competitor,
        texts.cell_amount(reading.amount),
        reading.currency or '',
        texts.AVAILABILITY[language][reading.availability.value],
        texts.METHODS[language][reading.method.value],
        reading.raw,
        item.offer.url,
    ]


def change_row(change: Change, *, at: dt.datetime, zone: dt.tzinfo, language: str) -> Row:
    """Строка вкладки «Changes»."""
    kind = texts.CHANGE_KINDS[language][change.kind]
    if change.kind == 'stock':
        was, now = '', texts.AVAILABILITY[language][change.availability.value]
    elif change.kind == 'currency':
        was = f'{texts.cell_amount(change.old_amount or Decimal(0))} {change.old_currency or "?"}'
        now = f'{texts.cell_amount(change.new_amount or Decimal(0))} {change.currency or "?"}'
    else:
        was = texts.cell_amount(change.old_amount) if change.old_amount is not None else ''
        now = texts.cell_amount(change.new_amount) if change.new_amount is not None else ''
    return [
        texts.show_time(at, zone),
        change.product,
        change.competitor,
        kind,
        was,
        now,
        texts.cell_pct(change.pct) if change.pct is not None else '',
        texts.cell_amount(change.own_price) if change.own_price is not None else '',
        change.url,
    ]


# --- уведомления ---------------------------------------------------------------------------


def render_digest(
    changes: Sequence[Change],
    *,
    client: str,
    language: str,
    when: str,
    failed: int,
    total: int,
    sheet_url: str | None,
) -> str | None:
    """Одно сообщение владельцу за запуск, только если есть что сказать.

    Args:
        changes: Значимые события запуска.
        client: Название бизнеса.
        language: Язык клиента.
        when: Время запуска для заголовка.
        failed: Сколько ссылок не проверено.
        total: Сколько ссылок всего.
        sheet_url: Ссылка на таблицу или None.

    Returns:
        Текст или None, если значимого ничего нет.
    """
    if not changes:
        return None
    t = texts.DIGEST[language]
    out = [t['title'].format(client=client, when=when)]

    def section(kind: ChangeKind, render: Callable[[Change], str]) -> None:
        items = [render(change) for change in changes if change.kind == kind]
        if items:
            out.extend(['', t[kind], *items])

    def amount(value: Decimal | None) -> str:
        return texts.show_amount(value, language) if value is not None else '?'

    section(
        'price',
        lambda c: t['price_line'].format(
            product=c.product,
            competitor=c.competitor,
            old=amount(c.old_amount),
            new=amount(c.new_amount),
            currency=c.currency or '',
            pct=f' ({texts.show_pct(c.pct)}%)' if c.pct is not None else '',
        ),
    )
    section(
        'undercut',
        lambda c: t['undercut_line'].format(
            product=c.product,
            competitor=c.competitor,
            price=amount(c.new_amount),
            currency=c.currency or '',
            own=amount(c.own_price),
            pct=texts.show_pct(c.pct or Decimal(0)),
        ),
    )
    section(
        'stock',
        lambda c: t['stock_out' if c.availability is Availability.OUT_OF_STOCK else 'stock_back'].format(
            product=c.product, competitor=c.competitor
        ),
    )
    section(
        'currency',
        lambda c: t['currency_line'].format(
            product=c.product, competitor=c.competitor, new=c.currency or '?', old=c.old_currency or '?'
        ),
    )
    if failed:
        out.extend(['', t['unchecked'].format(failed=failed, total=total)])
    if sheet_url:
        out.append(t['sheet'].format(url=sheet_url))
    return '\n'.join(out)


def _event_payload(item: Assessment, reading: PriceReading) -> dict[str, str]:
    return {
        'product_id': item.product.id,
        'product': item.product.name,
        'competitor': item.offer.competitor,
        'amount': format(reading.amount, 'f'),
        'currency': reading.currency or '',
        'method': reading.method.value,
        'diff_vs_own_pct': texts.cell_pct(item.diff_vs_own) if item.diff_vs_own is not None else '',
    }


def _change_payload(change: Change) -> dict[str, str]:
    payload = {'product': change.product, 'competitor': change.competitor, 'currency': change.currency or ''}
    if change.old_amount is not None:
        payload['old'] = format(change.old_amount, 'f')
    if change.new_amount is not None:
        payload['new'] = format(change.new_amount, 'f')
    if change.pct is not None:
        payload['pct'] = texts.cell_pct(change.pct)
    if change.kind == 'stock':
        payload['availability'] = change.availability.value
    return payload


_CHANGE_EVENTS: Final[dict[ChangeKind, str]] = {
    'price': 'prices.changed',
    'undercut': 'prices.undercut',
    'stock': 'prices.stock_changed',
    'currency': 'prices.currency_changed',
}


async def run_once(config: PricesConfig, deps: PricesDeps, now: dt.datetime) -> RunResult:
    """Один проход монитора по всем ссылкам клиента.

    Args:
        config: Конфиг клиента.
        deps: Зависимости.
        now: Момент запуска, с часовым поясом.

    Returns:
        Итог прохода.

    Raises:
        SinkError: Таблица клиента не записалась (база при этом не изменилась).
        NotifyError: Уведомление владельцу не доставлено (данные уже записаны).
        ValueError: Время без часового пояса.
    """
    if now.tzinfo is None:
        raise ValueError('момент запуска должен быть с часовым поясом')
    section = config.prices
    language = texts.lang(config.client.language)
    zone = config.client.zone
    llm = deps.llm if section.llm_fallback else None
    budget = LLMBudget(section.llm_max_pages_per_run if llm is not None else 0)

    checks = await check_all(section, deps.new_fetcher(), llm=llm, budget=budget)
    assessments: list[Assessment] = []
    for product, offer, outcome in checks:
        key = OfferKey(product.id, offer.competitor, offer.url)
        assessments.append(
            assess(
                product,
                offer,
                outcome,
                previous=deps.store.last_reading(key),
                state=deps.store.state(key),
                section=section,
            )
        )
    readings = [(item, item.outcome) for item in assessments if isinstance(item.outcome, PriceReading)]
    changes = tuple(change for item in assessments for change in item.changes)

    tabs, headers = texts.TABS[language], texts.HEADERS[language]
    await deps.sink.replace(
        tabs['now'], headers['now'], [now_row(item, at=now, zone=zone, language=language) for item in assessments]
    )
    history = [
        history_row(item, reading, at=now, zone=zone, language=language) for item, reading in readings if item.new_value
    ]
    if history:
        await deps.sink.append(tabs['history'], headers['history'], history)
    if changes:
        await deps.sink.append(
            tabs['changes'], headers['changes'], [change_row(c, at=now, zone=zone, language=language) for c in changes]
        )

    deps.store.commit(
        now, [(item.key, reading) for item, reading in readings], [(item.key, item.state) for item in assessments]
    )
    for item, reading in readings:
        deps.events.record(PACKAGE, 'prices.checked', now, _event_payload(item, reading))
    for change in changes:
        deps.events.record(PACKAGE, _CHANGE_EVENTS[change.kind], now, _change_payload(change))
    for item in assessments:
        if isinstance(item.outcome, CheckFailure):
            deps.events.record(
                PACKAGE,
                'prices.failed',
                now,
                {'product': item.product.name, 'competitor': item.offer.competitor, 'reason': item.outcome.reason},
            )

    failed = len(assessments) - len(readings)
    sheet_url = (
        _SHEETS_URL.format(id=config.sink.spreadsheet_id)
        if config.sink.backend == 'google' and config.sink.spreadsheet_id
        else None
    )
    digest = render_digest(
        changes,
        client=config.client.name,
        language=language,
        when=texts.show_time(now, zone),
        failed=failed,
        total=len(assessments),
        sheet_url=sheet_url,
    )
    operator_lines = [line for item in assessments for line in item.operator_lines]
    if budget.denied:
        operator_lines.append(
            f'• бюджет модели ({budget.limit} стр. за запуск) исчерпан: без проверки осталось {budget.denied} стр.'
        )
    operator_alert = f'🛠 {config.client.name} / prices:\n' + '\n'.join(operator_lines) if operator_lines else None
    await _tell_operator(deps.operator, operator_alert)
    if digest is not None:
        await deps.notifier.send(digest)
    logger.info('prices: %s проверено, %s не проверено, событий %s', len(readings), failed, len(changes))
    return RunResult(
        checked=len(readings),
        failed=failed,
        changes=changes,
        digest=digest,
        operator_alert=operator_alert,
        llm_pages=budget.used,
        methods=dict(Counter(reading.method.value for _, reading in readings)),
        failures=dict(Counter(item.outcome.reason for item in assessments if isinstance(item.outcome, CheckFailure))),
    )


async def _tell_operator(operator: Notifier | None, text: str | None) -> None:
    if text is None:
        return
    if operator is None:
        logger.warning('канал оператора не настроен; сообщение оператору: %s', text)
        return
    try:
        await operator.send(text)
    except NotifyError as exc:
        # Канал оператора — служебный: его сбой не должен прятать от клиента его уведомление.
        logger.error('оператор не уведомлён: %s', exc)

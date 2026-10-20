"""Один проход монитора: прочитать все ссылки, сравнить с прошлым, записать, сообщить.

Порядок работы выбран так, чтобы ничего не терялось и не дублировалось:

1. проход берёт замок клиента: второй одновременный проход (cron + ручной `run`) пропускается
   (`RunSkipped`: не успех и не сбой — в надёжность отчёта не входит);
2. все ссылки проверяются бесплатными ступенями (сайты — параллельно, страницы одного сайта
   — по очереди), затем страницы без разметки — моделью, бюджет делится в порядке конфига;
   у прохода есть общий срок (`max_run_minutes`);
3. сравнение с прошлой ценой — чистой функцией `assess`, в `Decimal`;
4. «Prices now» заменяется целиком; упала таблица — база не меняется, следующий проход
   повторит ту же работу (а `supervised_run` сообщит оператору);
5. цены, состояние ссылок и исходящие (строки «History»/«Changes», уведомление владельцу)
   записываются в SQLite одной транзакцией, затем исходящие доставляются; недоставленное
   остаётся в очереди и уходит первым в следующий проход — без потерь и без дублей;
6. оператору — о сломанных ссылках и сомнительных ценах (один раз на пороге, а не каждый запуск).

Клиенту не сообщается то, в чём сомневается сам монитор: цена из неоднозначной разметки,
цена со страницы с несколькими товарами, скачок больше ±50% и цена, которую не с чем сверить
(первое чтение), больше чем на 50% ниже цены клиента, — последние две, пока их не подтвердило
повторное чтение. Такие чтения видны в таблице как «нужна проверка» и не становятся базой
для следующих сравнений.

Сбой одной ссылки — даже неожиданное исключение на странной странице — не останавливает
остальные: он становится строкой «не проверено» и сигналом оператору.

Сайт на паузе (`[prices.sites."…"] paused = true`) не запрашивается вовсе: его ссылки — «на
паузе» в таблице (с последней известной ценой), не сбой, не счётчик сбоев и не строка
«не удалось проверить» в уведомлении; в журнал — одно событие `prices.paused` за проход.
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

from autostudio.errors import AutoStudioError, NotifyError, SinkError
from autostudio.events import EventLog
from autostudio.llm import LLMClient
from autostudio.notify import Notifier
from autostudio.prices import texts
from autostudio.prices.config import PARALLEL_SITES, PAUSED, Offer, PricesConfig, PricesSection, Product
from autostudio.prices.extract import (
    AMBIGUOUS_DECIMAL,
    LLM_CONFIG,
    MULTIPLE_PRODUCTS,
    Availability,
    ExtractionFailure,
    LLMBudget,
    PriceReading,
    ask_model,
    budget_denied,
    extract_price,
    merge_model,
    model_text,
)
from autostudio.prices.fetch import FetchFailure, FetchResult
from autostudio.prices.money import Qualifier
from autostudio.prices.store import (
    Channel,
    OfferKey,
    OfferState,
    OutboxEntry,
    PriceStore,
    SheetRow,
    StoredReading,
)
from autostudio.prices.urls import origin, redact
from autostudio.runlog import RunSkipped
from autostudio.sinks import Cell, TableSink

logger = logging.getLogger(__name__)

PACKAGE: Final = 'prices'
_PARALLEL_MODEL: Final = 4
_SHEETS_URL: Final = 'https://docs.google.com/spreadsheets/d/{id}'
# Скачок цены больше этого — сначала подтверждение повторным чтением, потом уведомление
# владельцу: «2 499 → 2.50» чаще ошибка вёрстки или разметки, чем распродажа.
MAX_PLAUSIBLE_JUMP_PCT: Final = Decimal(50)
_BUDGET_FLAG: Final = 'llm_budget_exhausted'
_MODEL_CONFIG_FLAG: Final = 'llm_config_broken'
REDIRECTED: Final = 'redirected'
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
    AMBIGUOUS_DECIMAL: (
        'в разметке цена «{raw}»: по стандарту это дробь, но может быть и тысячами, а на странице '
        'подтверждения нет. Клиенту уведомлений по ней нет («нужна проверка»). Сверьте с сайтом и '
        'задайте селектор: autostudio prices probe {url}'
    ),
    MULTIPLE_PRODUCTS: (
        'на странице несколько товаров с разными ценами в разметке — взята первая, клиенту уведомлений '
        'по ней нет («нужна проверка»). Задайте селектор: autostudio prices probe {url}'
    ),
    REDIRECTED: (
        'ссылка теперь редиректит на более короткий адрес (товар переехал); цена прочитана, но обновите '
        'url в конфиге — итоговый адрес покажет autostudio prices probe {url}'
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


class _NeedsModel(msgspec.Struct, frozen=True, kw_only=True):
    # Бесплатные ступени не нашли цену; страница ждёт модель (и бюджет на неё).
    text: str
    failure: ExtractionFailure
    warnings: tuple[str, ...] = ()


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
        old_availability: Наличие до этого (для `stock`).
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
    old_availability: Availability = Availability.UNKNOWN


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
        diff_vs_own: Разница с ценой клиента в % (только в одной валюте и только для надёжной цены).
        change_vs_last: Изменение к прошлой цене в % (только в одной валюте).
        new_value: Цена или наличие отличаются от прошлых (или это первое чтение).
        operator_lines: Что сказать оператору.
        doubtful: Число под сомнением (неоднозначная разметка, несколько товаров).
        held: Цена ждёт подтверждения повторным чтением (скачок или «намного ниже вашей»).
        far_below: Причина задержки — не скачок к прошлой цене, а цена намного ниже цены клиента
            при том, что сверить её с прошлым чтением нельзя.
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
    doubtful: bool = False
    held: bool = False
    far_below: bool = False

    @property
    def recorded(self) -> bool:
        """Цена становится новой точкой сравнения (история, база для следующего прохода)."""
        return isinstance(self.outcome, PriceReading) and not self.doubtful and not self.held


class RunResult(msgspec.Struct, frozen=True, kw_only=True):
    """Итог прохода для CLI, демо и тестов.

    Attributes:
        failed: Не прочитано по сбою (ссылки сайтов на паузе — не сбой и сюда не входят).
        paused: Ссылок не проверялось: их сайт на паузе.
    """

    checked: int
    failed: int
    changes: tuple[Change, ...]
    digest: str | None
    operator_alert: str | None
    llm_pages: int
    methods: dict[str, int] = msgspec.field(default_factory=dict)
    failures: dict[str, int] = msgspec.field(default_factory=dict)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    paused: int = 0


def is_paused(outcome: PriceReading | CheckFailure) -> bool:
    """Ссылка не проверялась, потому что её сайт на паузе (это не сбой)."""
    return isinstance(outcome, CheckFailure) and outcome.reason == PAUSED


def pct_change(old: Decimal, new: Decimal) -> Decimal:
    """Изменение в процентах, целиком в `Decimal` (old > 0)."""
    return (new - old) / old * 100


def _comparable(currency: str | None, own: Decimal | None, product: Product) -> bool:
    return own is not None and currency == product.currency


def _undercuts(
    own: Decimal | None, product: Product, currency: str | None, amount: Decimal, availability: Availability
) -> bool:
    # Товар не в наличии клиента не подрезает; валюты не смешиваются.
    if not _comparable(currency, own, product) or availability is Availability.OUT_OF_STOCK:
        return False
    return own is not None and amount < own


def _jump_key(reading: PriceReading) -> str:
    return f'{format(reading.amount, "f")} {reading.currency or "?"}'


class _Hold(msgspec.Struct, frozen=True, kw_only=True):
    # Почему цена ждёт повторного чтения: строка для оператора и «намного ниже вашей» ли это.
    line: str
    far_below: bool


def _hold(
    where: str,
    offer: Offer,
    reading: PriceReading,
    previous: StoredReading | None,
    change_vs_last: Decimal | None,
    diff: Decimal | None,
    own: Decimal | None,
) -> _Hold | None:
    """Необычная цена, которой клиенту сразу не сообщаем, или None.

    Скачок ловится сравнением с прошлой ценой. Но цену, которую сверить не с чем (первое
    чтение, смена валюты, до этого «бесплатно»), монитор сразу сделал бы и «дешевле вас», и
    базой: «2.50 при вашей 2 599» из сломанной разметки ушло бы клиенту как −99.9%. Такая цена
    сверяется с ценой клиента — и только вниз: «дороже вас» клиенту не сообщается, а конкурент
    вдвое дороже — обычное дело. И только точная: «бесплатно» (включено оператором) ниже любой
    цены на 100%, а «от …» и диапазон — нижние границы, «дешевле вас» по ним не сообщается.
    """
    price = f'{texts.cell_amount(reading.amount)} {reading.currency or ""}'.rstrip()
    url = redact(offer.url)
    if previous is not None and change_vs_last is not None:
        if abs(change_vs_last) <= MAX_PLAUSIBLE_JUMP_PCT:
            return None
        return _Hold(
            line=(
                f'• {where}: цена {texts.cell_amount(previous.amount)} → {price} '
                f'({texts.cell_pct(change_vs_last)}%) — необычный скачок; клиенту '
                f'сообщим, если повторное чтение его подтвердит. {url}'
            ),
            far_below=False,
        )
    if diff is None or own is None or diff >= -MAX_PLAUSIBLE_JUMP_PCT or reading.qualifier is not Qualifier.EXACT:
        return None
    return _Hold(
        line=(
            f'• {where}: цена {price} — на {texts.cell_pct(-diff)}% ниже цены клиента ({texts.cell_amount(own)}), '
            f'а сверить её с прошлой ценой нельзя; клиенту сообщим, если повторное чтение её подтвердит. '
            f'Сверьте с сайтом: {url}'
        ),
        far_below=True,
    )


def _failed(
    product: Product,
    offer: Offer,
    outcome: CheckFailure,
    *,
    previous: StoredReading | None,
    state: OfferState,
    section: PricesSection,
) -> Assessment:
    key = OfferKey(product.id, offer.competitor, offer.url)
    if outcome.reason == PAUSED:
        # Пауза — решение оператора, а не поломка: счётчик сбоев и предупреждения не трогаем.
        return Assessment(key=key, product=product, offer=offer, outcome=outcome, previous=previous, state=state)
    failures = state.failures + 1
    lines: tuple[str, ...] = ()
    if failures == section.failure_alert_after:
        where = f'{product.name} / {offer.competitor}'
        lines = (f'• {where}: {outcome.reason} ({outcome.detail}) — сбоев подряд: {failures}; {redact(offer.url)}',)
    return Assessment(
        key=key,
        product=product,
        offer=offer,
        outcome=outcome,
        previous=previous,
        state=msgspec.structs.replace(state, failures=failures, last_reason=outcome.reason),
        operator_lines=lines,
    )


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
    валюта — не процент изменения и не «дешевле вас», а отдельная пометка. Сомнительная
    цена (`PriceReading.doubtful`) и неподтверждённый скачок больше `MAX_PLAUSIBLE_JUMP_PCT`
    не дают клиенту уведомлений и не становятся базой сравнения. Цена, которую не с чем
    сверить (первое чтение, смена валюты, до этого «бесплатно»), сверяется с ценой клиента:
    ниже неё больше чем на `MAX_PLAUSIBLE_JUMP_PCT` — тоже ждёт повторного чтения.

    Args:
        product: Товар клиента.
        offer: Страница конкурента.
        outcome: Цена или сбой этого запуска.
        previous: Последняя записанная цена или None.
        state: Состояние ссылки до запуска.
        section: Пороги из конфига.

    Returns:
        Итог ссылки.
    """
    if isinstance(outcome, CheckFailure):
        return _failed(product, offer, outcome, previous=previous, state=state, section=section)

    key = OfferKey(product.id, offer.competitor, offer.url)
    where = f'{product.name} / {offer.competitor}'
    reading, own = outcome, product.own_price
    doubtful = reading.doubtful
    same_currency = previous is not None and previous.currency == reading.currency
    change_vs_last = (
        pct_change(previous.amount, reading.amount)
        if previous is not None and same_currency and previous.amount > 0 and not doubtful
        else None
    )
    diff = pct_change(own, reading.amount) if own and _comparable(reading.currency, own, product) else None
    jump = _jump_key(reading)
    # Сомнительная цена и так не уходит клиенту; необычная ждёт, пока повторное чтение не
    # увидит ту же цену (`state.pending`).
    hold = None if doubtful else _hold(where, offer, reading, previous, change_vs_last, diff, own)
    if hold is not None and state.pending == jump:
        hold = None

    lines: list[str] = []
    for warning in dict.fromkeys(reading.warnings):
        if warning not in state.warnings:
            template = _WARNINGS.get(warning, '{warning}')
            text = template.format(
                warning=warning, selector=offer.selector, method=reading.method.value, raw=reading.raw, url=offer.url
            )
            lines.append(f'• {where}: {text}')
    if state.failures >= section.failure_alert_after:
        lines.append(f'• {where}: снова читается (сбоев подряд было: {state.failures})')
    new_state = OfferState(
        failures=0, last_reason=None, warning=','.join(dict.fromkeys(reading.warnings)) or None, pending=None
    )

    if doubtful or hold is not None:
        if hold is not None:
            new_state = msgspec.structs.replace(new_state, pending=jump)
            lines.append(hold.line)
        # Разница с ценой клиента у такой цены не пишется ни в таблицу, ни в журнал: из журнала
        # строится месячный отчёт, и «−99.9%» попало бы туда в обход уведомлений.
        return Assessment(
            key=key,
            product=product,
            offer=offer,
            outcome=reading,
            previous=previous,
            state=new_state,
            change_vs_last=change_vs_last if hold is not None else None,
            operator_lines=tuple(lines),
            doubtful=doubtful,
            held=hold is not None,
            far_below=hold is not None and hold.far_below,
        )

    changes: list[Change] = []
    base = Change(
        kind='price',
        product=product.name,
        competitor=offer.competitor,
        url=offer.url,
        currency=reading.currency,
        own_price=own,
    )
    replace = msgspec.structs.replace
    if previous is not None:
        if change_vs_last is not None:
            if abs(change_vs_last) >= section.alert_change_pct:
                changes.append(replace(base, old_amount=previous.amount, new_amount=reading.amount, pct=change_vs_last))
        elif same_currency:
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
            changes.append(
                replace(base, kind='stock', availability=reading.availability, old_availability=previous.availability)
            )
    exact = reading.qualifier is Qualifier.EXACT
    if (
        section.alert_undercut
        and exact
        and _undercuts(own, product, reading.currency, reading.amount, reading.availability)
    ):
        # «Было» — то, что тогда вызвало бы уведомление: точная цена ниже тогдашней цены клиента.
        was = (
            previous is not None
            and previous.qualifier is Qualifier.EXACT
            and _undercuts(
                previous.own_price if previous.own_price is not None else own,
                product,
                previous.currency,
                previous.amount,
                previous.availability,
            )
        )
        if not was:
            changes.append(replace(base, kind='undercut', new_amount=reading.amount, pct=diff))

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
        state=new_state,
        diff_vs_own=diff,
        change_vs_last=change_vs_last,
        new_value=new_value,
        operator_lines=tuple(lines),
    )


def _path_depth(url: str) -> int:
    return len([part for part in httpx.URL(url).path.split('/') if part])


def _redirect(configured: str, final: str) -> Literal['away', 'moved'] | None:
    """Редирект на более короткий адрес: в корень/раздел (`away`) или просто переезд (`moved`)."""
    depth = _path_depth(final)
    if depth >= _path_depth(configured):
        return None
    # Удалённый товар уводит в корень сайта или раздел каталога, где разметка описывает чужие
    # товары: цена оттуда была бы молча неверной. Канонический адрес без языкового префикса
    # или без «/product/» — обычный переезд: цену читаем, оператору — обновить url.
    return 'away' if depth <= 1 else 'moved'


async def check_offer(
    product: Product, offer: Offer, fetcher: Fetcher, *, section: PricesSection, model: bool
) -> PriceReading | CheckFailure | _NeedsModel:
    """Загружает и читает одну страницу бесплатными ступенями; любой сбой — `CheckFailure`.

    Args:
        product: Товар.
        offer: Страница конкурента.
        fetcher: Загрузчик.
        section: Настройки монитора.
        model: Включена ли платная ступень (тогда страница без цены ждёт модель).

    Returns:
        Цена, сбой или страница для модели.
    """
    try:
        page = await fetcher.fetch(offer.url)
        if isinstance(page, FetchFailure):
            return CheckFailure(reason=page.reason, detail=page.detail)
        redirect = _redirect(offer.url, page.final_url)
        if redirect == 'away':
            return CheckFailure(reason='redirected_away', detail=f'→ {redact(page.final_url)}')
        warnings = (REDIRECTED,) if redirect == 'moved' else ()
        result = extract_price(page.html, offer, allow_free=section.allow_free, page_url=page.final_url)
        if isinstance(result, ExtractionFailure) and model:
            return _NeedsModel(text=model_text(page.html), failure=result, warnings=warnings)
    except Exception as exc:
        # Одна странная страница не должна останавливать мониторинг остальных: трассировка
        # уходит в лог, оператор получает сбой ссылки, остальные ссылки проверяются.
        logger.exception('проверка %s / %s упала', product.id, offer.competitor)
        return CheckFailure(reason='internal_error', detail=type(exc).__name__)
    return _settle(result, warnings)


def _settle(result: PriceReading | ExtractionFailure, warnings: tuple[str, ...]) -> CheckOutcome:
    if isinstance(result, ExtractionFailure):
        return CheckFailure(reason=result.reason, detail=result.detail)
    return msgspec.structs.replace(result, warnings=(*result.warnings, *warnings)) if warnings else result


async def check_all(
    section: PricesSection,
    fetcher: Fetcher,
    *,
    llm: LLMClient | None,
    budget: LLMBudget,
    deadline_s: float | None = None,
) -> list[tuple[Product, Offer, CheckOutcome]]:
    """Проверяет все ссылки: сайты параллельно, страницы одного сайта — последовательно.

    Сначала все страницы читаются бесплатными ступенями, затем страницы без цены — моделью:
    бюджет делится в порядке конфига, поэтому одни и те же ссылки получают модель от запуска
    к запуску (а не те, чей сайт ответил быстрее). Что не уложилось в срок прохода, — сбой
    `run_timeout` для этой ссылки, а не зависший проход. Ссылки сайтов на паузе не
    загружаются: их итог — `paused`.

    Args:
        section: Товары и настройки.
        fetcher: Загрузчик этого запуска.
        llm: Модель или None.
        budget: Бюджет модели.
        deadline_s: Срок прохода в секундах (по умолчанию `max_run_minutes`).

    Returns:
        Результаты в порядке конфига (таблица и уведомления стабильны от запуска к запуску).
    """
    pairs = [(product, offer) for product in section.products for offer in product.offers]
    first: dict[int, PriceReading | CheckFailure | _NeedsModel] = {}
    by_site: dict[str, list[int]] = {}
    for index, (_, offer) in enumerate(pairs):
        paused = section.paused_site(offer.url)
        if paused is not None:
            # Претензия владельца сайта — стоп сразу: ни страницы, ни robots.txt, ни DNS.
            first[index] = CheckFailure(reason=PAUSED, detail=paused)
        else:
            by_site.setdefault(origin(offer.url), []).append(index)
    loop = asyncio.get_running_loop()
    limit_s = deadline_s if deadline_s is not None else section.max_run_minutes * 60
    deadline = loop.time() + limit_s
    late = CheckFailure(reason='run_timeout', detail=f'проход не уложился в {limit_s:g} с')
    gate = asyncio.Semaphore(PARALLEL_SITES)

    async def worker(indexes: list[int]) -> None:
        async with gate:
            for index in indexes:
                product, offer = pairs[index]
                remaining = deadline - loop.time()
                if remaining <= 0:
                    first[index] = late
                    continue
                try:
                    async with asyncio.timeout(remaining):
                        first[index] = await check_offer(
                            product, offer, fetcher, section=section, model=llm is not None
                        )
                except TimeoutError:
                    first[index] = late

    await asyncio.gather(*(worker(indexes) for indexes in by_site.values()))

    final: dict[int, CheckOutcome] = {}
    granted: list[tuple[int, _NeedsModel]] = []
    for index in range(len(pairs)):
        outcome = first[index]
        if not isinstance(outcome, _NeedsModel):
            final[index] = outcome
        elif llm is not None and budget.take():
            granted.append((index, outcome))
        else:
            final[index] = _settle(budget_denied(outcome.failure), ())
    if llm is not None and granted:
        final.update(await _ask_all(pairs, granted, llm=llm, section=section, deadline=deadline, late=late))
    return [(product, offer, final[index]) for index, (product, offer) in enumerate(pairs)]


async def _ask_all(
    pairs: list[tuple[Product, Offer]],
    granted: list[tuple[int, _NeedsModel]],
    *,
    llm: LLMClient,
    section: PricesSection,
    deadline: float,
    late: CheckFailure,
) -> dict[int, CheckOutcome]:
    loop = asyncio.get_running_loop()
    gate = asyncio.Semaphore(_PARALLEL_MODEL)

    async def ask(index: int, waiting: _NeedsModel) -> tuple[int, CheckOutcome]:
        product, offer = pairs[index]
        remaining = deadline - loop.time()
        if remaining <= 0:
            return index, late
        try:
            async with gate, asyncio.timeout(remaining):
                answer = await ask_model(
                    waiting.text, offer, product_name=product.name, llm=llm, allow_free=section.allow_free
                )
        except TimeoutError:
            return index, late
        except Exception as exc:
            # Неожиданный сбой SDK на одной странице не роняет проход: это сбой ссылки.
            logger.exception('модель на %s / %s упала', product.id, offer.competitor)
            return index, CheckFailure(reason='internal_error', detail=type(exc).__name__)
        return index, _settle(merge_model(waiting.failure, answer), waiting.warnings)

    return dict(await asyncio.gather(*(ask(index, waiting) for index, waiting in granted)))


# --- таблица -------------------------------------------------------------------------------
# Суммы и проценты уходят в таблицу числами (`Decimal`), подписи — текстом: клиент сортирует
# по «Разница с вашей, %» и строит графики по «History» без VALUE() и «Формат → Число».


def _status(item: Assessment, language: str) -> str:
    t = texts.STATUS[language]
    outcome = item.outcome
    if isinstance(outcome, CheckFailure):
        reason = texts.failure_text(outcome.reason, language)
        return t['stale'].format(reason=reason) if item.previous is not None else reason
    if item.held:
        return t['far_below'] if item.far_below else t['jump']
    if item.doubtful:
        return t['multiple'] if MULTIPLE_PRODUCTS in outcome.warnings else t['ambiguous']
    return t[outcome.qualifier.value] if outcome.qualifier is not Qualifier.EXACT else t['ok']


def _diff_cell(product: Product, currency: str | None, diff: Decimal | None, language: str) -> Cell:
    if product.own_price is None:
        return ''
    if currency != product.currency:
        return texts.STATUS[language]['na_currency'].format(theirs=currency or '?', yours=product.currency)
    return texts.num_pct(diff) if diff is not None else ''


def _amount_cell(value: Decimal | None) -> Cell:
    return texts.num_amount(value) if value is not None else ''


def _pct_cell(value: Decimal | None) -> Cell:
    return texts.num_pct(value) if value is not None else ''


def now_row(item: Assessment, *, at: dt.datetime, zone: dt.tzinfo, language: str) -> SheetRow:
    """Строка вкладки «Prices now»: текущая цена или последняя известная с пометкой."""
    product, offer = item.product, item.offer
    shown: PriceReading | StoredReading | None
    checked: dt.datetime | None
    if isinstance(item.outcome, PriceReading):
        shown, checked, diff = item.outcome, at, item.diff_vs_own
    else:
        shown = item.previous
        checked = item.previous.at if item.previous else None
        diff = (
            pct_change(product.own_price, shown.amount)
            if shown is not None and product.own_price and _comparable(shown.currency, product.own_price, product)
            else None
        )
    price: Cell = ''
    diff_cell: Cell = ''
    currency = availability = method = ''
    if shown is not None:
        price, currency = texts.num_amount(shown.amount), shown.currency or ''
        availability = texts.AVAILABILITY[language][shown.availability.value]
        method = texts.METHODS[language][shown.method.value]
        diff_cell = _diff_cell(product, shown.currency, diff, language)
    return [
        product.name,
        _amount_cell(product.own_price),
        offer.competitor,
        price,
        currency,
        diff_cell,
        _pct_cell(item.change_vs_last),
        availability,
        method,
        texts.show_time(checked, zone) if checked else '',
        _status(item, language),
        offer.url,
    ]


def history_row(
    item: Assessment, reading: PriceReading, *, at: dt.datetime, zone: dt.tzinfo, language: str
) -> SheetRow:
    """Строка вкладки «History» — с происхождением цены (источник и текст со страницы)."""
    return [
        texts.show_time(at, zone),
        item.product.name,
        item.offer.competitor,
        texts.num_amount(reading.amount),
        reading.currency or '',
        texts.AVAILABILITY[language][reading.availability.value],
        texts.METHODS[language][reading.method.value],
        reading.raw,
        item.offer.url,
    ]


def change_row(change: Change, *, at: dt.datetime, zone: dt.tzinfo, language: str) -> SheetRow:
    """Строка вкладки «Changes»: «Было»/«Стало» — числа у цены, текст у наличия и смены валюты."""
    kind = texts.CHANGE_KINDS[language][change.kind]
    was: Cell
    now: Cell
    if change.kind == 'stock':
        was = texts.AVAILABILITY[language][change.old_availability.value]
        now = texts.AVAILABILITY[language][change.availability.value]
    elif change.kind == 'currency':
        # Суммы в разных валютах в одной числовой колонке сравнивались бы как одна валюта.
        was = f'{texts.cell_amount(change.old_amount or Decimal(0))} {change.old_currency or "?"}'
        now = f'{texts.cell_amount(change.new_amount or Decimal(0))} {change.currency or "?"}'
    else:
        was, now = _amount_cell(change.old_amount), _amount_cell(change.new_amount)
    return [
        texts.show_time(at, zone),
        change.product,
        change.competitor,
        kind,
        was,
        now,
        _pct_cell(change.pct),
        _amount_cell(change.own_price),
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
            pct=f' ({texts.show_pct(c.pct, language)}%)' if c.pct is not None else '',
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
            pct=texts.show_pct(c.pct or Decimal(0), language),
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
        RunSkipped: Другой проход по этому клиенту ещё идёт (cron + ручной `run`): этот не
            делал ничего. Не успех и не сбой — `supervised_run` так его и запишет.
        SinkError: Таблица клиента не записалась. Если не заменилась «Prices now» — база не
            изменилась; если не дописались строки — они ждут в очереди следующего прохода.
        NotifyError: Уведомление владельцу не доставлено (оно ждёт в очереди следующего прохода).
        ValueError: Время без часового пояса.
    """
    if now.tzinfo is None:
        raise ValueError('момент запуска должен быть с часовым поясом')
    with deps.store.exclusive() as locked:
        if not locked:
            logger.warning('prices %s: предыдущий проход ещё идёт — этот пропущен', config.client.id)
            raise RunSkipped('предыдущий проход по этому клиенту ещё идёт')
        return await _run_locked(config, deps, now)


async def _run_locked(config: PricesConfig, deps: PricesDeps, now: dt.datetime) -> RunResult:
    section = config.prices
    language = texts.lang(config.client.language)
    zone = config.client.zone
    llm = deps.llm if section.llm_fallback else None
    budget = LLMBudget(section.llm_max_pages_per_run if llm is not None else 0)
    tokens_before = (llm.usage.input_tokens, llm.usage.output_tokens) if llm is not None else (0, 0)

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
    recorded = [(item, reading) for item, reading in readings if item.recorded]
    changes = tuple(change for item in assessments for change in item.changes)
    paused = [item for item in assessments if is_paused(item.outcome)]
    failures = [
        item.outcome for item in assessments if isinstance(item.outcome, CheckFailure) and not is_paused(item.outcome)
    ]

    tabs, headers = texts.TABS[language], texts.HEADERS[language]
    await deps.sink.replace(
        tabs['now'], headers['now'], [now_row(item, at=now, zone=zone, language=language) for item in assessments]
    )
    failed = len(failures)
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
        total=len(assessments) - len(paused),
        sheet_url=sheet_url,
    )
    outbox: list[OutboxEntry] = []
    history = [
        history_row(item, reading, at=now, zone=zone, language=language) for item, reading in recorded if item.new_value
    ]
    if history:
        outbox.append(OutboxEntry(channel='sheet', tab=tabs['history'], header=headers['history'], rows=history))
    if changes:
        rows = [change_row(change, at=now, zone=zone, language=language) for change in changes]
        outbox.append(OutboxEntry(channel='sheet', tab=tabs['changes'], header=headers['changes'], rows=rows))
    if digest is not None:
        outbox.append(OutboxEntry(channel='owner', text=digest))

    deps.store.commit(
        now,
        [(item.key, reading) for item, reading in recorded],
        [(item.key, item.state) for item in assessments],
        own_prices={item.key: item.product.own_price for item, _ in recorded},
        outbox=outbox,
    )
    for item, reading in readings:
        deps.events.record(PACKAGE, 'prices.checked', now, _event_payload(item, reading))
    for change in changes:
        deps.events.record(PACKAGE, _CHANGE_EVENTS[change.kind], now, _change_payload(change))
    for item in assessments:
        if isinstance(item.outcome, CheckFailure) and not is_paused(item.outcome):
            deps.events.record(
                PACKAGE,
                'prices.failed',
                now,
                {'product': item.product.name, 'competitor': item.offer.competitor, 'reason': item.outcome.reason},
            )
    if paused:
        # Одно событие за проход: в отчёте видно, что сайт не проверялся, а журнал не растёт
        # на каждую ссылку каждого прохода.
        sites = sorted({item.outcome.detail for item in paused if isinstance(item.outcome, CheckFailure)})
        deps.events.record(PACKAGE, 'prices.paused', now, {'sites': ', '.join(sites), 'offers': str(len(paused))})

    operator_lines = [line for item in assessments for line in item.operator_lines]
    operator_lines += _budget_lines(deps.store, budget, now)
    operator_lines += _model_config_lines(deps.store, failures, asked=budget.used > 0, now=now)
    operator_alert = f'🛠 {config.client.name} / prices:\n' + '\n'.join(operator_lines) if operator_lines else None
    await _tell_operator(deps.operator, operator_alert)
    logger.info(
        'prices: %s проверено, %s не проверено, на паузе %s, событий %s',
        len(readings),
        failed,
        len(paused),
        len(changes),
    )
    result = RunResult(
        checked=len(readings),
        failed=failed,
        changes=changes,
        digest=digest,
        operator_alert=operator_alert,
        llm_pages=budget.used,
        methods=dict(Counter(reading.method.value for _, reading in readings)),
        failures=dict(Counter(failure.reason for failure in failures)),
        llm_input_tokens=(llm.usage.input_tokens - tokens_before[0]) if llm is not None else 0,
        llm_output_tokens=(llm.usage.output_tokens - tokens_before[1]) if llm is not None else 0,
        paused=len(paused),
    )
    await deliver(deps)
    return result


def _budget_lines(store: PriceStore, budget: LLMBudget, now: dt.datetime) -> list[str]:
    # Об исчерпанном бюджете — один раз, когда это началось, и один раз, когда прошло;
    # ежечасный Premium-клиент иначе слал бы оператору 24 одинаковых сообщения в день.
    exhausted = store.flag(_BUDGET_FLAG) is not None
    if budget.denied and not exhausted:
        store.set_flag(_BUDGET_FLAG, now.isoformat())
        return [
            f'• бюджет модели ({budget.limit} стр. за запуск) исчерпан: без проверки осталось {budget.denied} стр. '
            '— поднимите llm_max_pages_per_run или задайте селекторы'
        ]
    if not budget.denied and exhausted:
        store.set_flag(_BUDGET_FLAG, None)
        return ['• бюджета модели снова хватает на все страницы'] if budget.limit else []
    return []


def _model_config_lines(
    store: PriceStore, failures: Sequence[CheckFailure], *, asked: bool, now: dt.datetime
) -> list[str]:
    # Ключ, права или имя модели (`LLMError.kind == 'config'`) не чинятся повтором: оператору —
    # сразу и один раз, а не через `failure_alert_after` проходов по строке на каждую ссылку.
    broken = [failure for failure in failures if failure.reason == LLM_CONFIG]
    flagged = store.flag(_MODEL_CONFIG_FLAG) is not None
    if broken and not flagged:
        store.set_flag(_MODEL_CONFIG_FLAG, now.isoformat())
        return [
            f'• модель не отвечает из-за настроек (ключ, права или имя модели): {broken[0].detail}. '
            f'Страниц без цены в этом проходе: {len(broken)}, клиенту — «в этот раз не проверено». '
            'Проверьте AUTOSTUDIO_LLM_PROVIDER, AUTOSTUDIO_LLM_MODEL и ключ провайдера'
        ]
    if flagged and asked and not broken:
        store.set_flag(_MODEL_CONFIG_FLAG, None)
        return ['• модель снова отвечает']
    return []


async def deliver(deps: PricesDeps) -> None:
    """Доставляет очередь: строки вкладок по порядку, уведомления владельцу по порядку.

    Сбой одного канала не держит другой (таблица легла — уведомление всё равно уходит), а
    внутри канала порядок сохраняется: после сбоя остальное ждёт следующего прохода.

    Args:
        deps: Зависимости прохода.

    Raises:
        SinkError: Строки не дописались (остались в очереди).
        NotifyError: Уведомление не доставлено (осталось в очереди).
    """
    errors: list[AutoStudioError] = []
    blocked: set[Channel] = set()
    for entry in deps.store.pending():
        if entry.channel in blocked:
            continue
        try:
            if entry.channel == 'sheet':
                await deps.sink.append(entry.tab, entry.header, entry.rows)
            else:
                await deps.notifier.send(entry.text)
        except (SinkError, NotifyError) as exc:
            blocked.add(entry.channel)
            errors.append(exc)
            continue
        deps.store.delivered(entry.id)
    if errors:
        raise errors[0]


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

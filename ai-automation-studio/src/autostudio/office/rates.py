"""Официальный курс НБГ на дату получения: вручную (`--rate`) или с сайта nbg.gov.ge.

База налога малого бизнеса — оборот в лари по официальному курсу Национального банка
Грузии на дату получения (бриф 6.2). Курс вручную разрешён всегда и хранится вместе с
поступлением: оператор мог взять его из выписки банка или с сайта НБГ.

Формат ответа НБГ не удалось проверить из среды разработки (сайт не открылся) — он
взят из описания задачи: JSON-массив, у первого элемента `date` и `currencies`
(объекты с `code`, `quantity`, `rate`; курс указан за `quantity` единиц). Разбор
терпит лишние поля, а при любом сбое просит `--rate`, а не гадает.
"""

import datetime as dt
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Final, Literal, Protocol

import httpx
import msgspec

from autostudio.errors import ConfigError

logger = logging.getLogger(__name__)

type RateSource = Literal['manual', 'nbg', 'gel']

NBG_URL: Final = 'https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/'
NBG_TIMEOUT_S: Final = 10.0
# На выходных и в праздники НБГ курс не устанавливает и действует последний. Больше
# недели разницы между запрошенной датой и датой курса — это уже не тот курс.
MAX_RATE_AGE_DAYS: Final = 7
_DATE_LENGTH: Final = 10


class RateUnavailableError(ConfigError):
    """Курса НБГ нет: сайт недоступен, ответ непонятен или в нём нет валюты. Нужен `--rate`."""


class Rate(msgspec.Struct, frozen=True, kw_only=True):
    """Курс лари за одну единицу валюты на дату.

    Attributes:
        currency: Код валюты.
        on_date: Дата, на которую курс запрошен (дата получения денег).
        gel_per_unit: Лари за 1 единицу валюты (уже поделено на `quantity`).
        quantity: За сколько единиц НБГ публикует курс (1, 10, 100, 1000).
        published_rate: Курс как опубликован, за `quantity` единиц.
        source_date: Дата курса в ответе НБГ.
    """

    currency: str
    on_date: dt.date
    gel_per_unit: Decimal
    quantity: Decimal
    published_rate: Decimal
    source_date: dt.date


class _NbgCurrency(msgspec.Struct, frozen=True):
    code: str
    quantity: Decimal
    rate: Decimal


class _NbgDay(msgspec.Struct, frozen=True):
    date: str
    currencies: list[_NbgCurrency]


def _unavailable(currency: str, on_date: dt.date, why: str) -> RateUnavailableError:
    return RateUnavailableError(
        f'курс НБГ {currency} на {on_date.isoformat()}: {why}. Задайте курс вручную: --rate <лари за 1 {currency}> '
        '(официальный курс НБГ на дату получения: nbg.gov.ge → Monetary policy → Currency rates)'
    )


def parse_nbg_response(content: bytes, currency: str, on_date: dt.date) -> Rate:
    """Разбирает ответ НБГ: числа сразу в Decimal, лишние поля пропускаются.

    Args:
        content: Тело ответа.
        currency: Нужная валюта.
        on_date: Запрошенная дата.

    Returns:
        Курс за одну единицу.

    Raises:
        RateUnavailableError: Не JSON, не та форма, нет валюты, неположительный курс или курс не на ту дату.
    """
    try:
        days = msgspec.json.decode(content, type=list[_NbgDay], strict=False)
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        raise _unavailable(currency, on_date, f'неожиданный ответ ({exc})') from None
    if not days:
        raise _unavailable(currency, on_date, 'пустой ответ')
    day = days[0]
    try:
        source_date = dt.date.fromisoformat(day.date[:_DATE_LENGTH])
    except ValueError:
        raise _unavailable(currency, on_date, f'непонятная дата {day.date!r}') from None
    if source_date > on_date or (on_date - source_date).days > MAX_RATE_AGE_DAYS:
        raise _unavailable(currency, on_date, f'в ответе курс на {source_date.isoformat()}')
    found = next((item for item in day.currencies if item.code.strip().upper() == currency), None)
    if found is None:
        raise _unavailable(currency, on_date, 'валюты нет в ответе')
    if not (found.quantity.is_finite() and found.rate.is_finite()) or found.quantity <= 0 or found.rate <= 0:
        raise _unavailable(currency, on_date, f'неположительный курс {found.rate} за {found.quantity}')
    return Rate(
        currency=currency,
        on_date=on_date,
        gel_per_unit=found.rate / found.quantity,
        quantity=found.quantity,
        published_rate=found.rate,
        source_date=source_date,
    )


def fetch_nbg_rate(client: httpx.Client, currency: str, on_date: dt.date) -> Rate:
    """Запрашивает курс у НБГ.

    Args:
        client: HTTP-клиент (в тестах — под respx).
        currency: Код валюты.
        on_date: Дата получения.

    Returns:
        Курс за одну единицу.

    Raises:
        RateUnavailableError: Сеть, HTTP-ошибка или непонятный ответ.
    """
    try:
        response = client.get(
            NBG_URL, params={'currencies': currency, 'date': on_date.isoformat()}, timeout=NBG_TIMEOUT_S
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # В тексте только тип ошибки: httpx кладёт в исключение URL целиком.
        logger.warning('НБГ: запрос курса не удался: %s', type(exc).__name__)
        raise _unavailable(currency, on_date, f'nbg.gov.ge не ответил ({type(exc).__name__})') from None
    return parse_nbg_response(response.content, currency, on_date)


class RateLookup(msgspec.Struct, frozen=True, kw_only=True):
    """Каким курсом пересчитано поступление.

    Attributes:
        gel_per_unit: Лари за 1 единицу.
        source: `manual` — задан оператором, `nbg` — с сайта НБГ (или из кэша), `gel` — платёж в лари.
        source_date: Дата курса НБГ или None.
    """

    gel_per_unit: Decimal
    source: RateSource
    source_date: dt.date | None = None


class RateCache(Protocol):
    """Кэш курсов НБГ: логика пересчёта не знает, что он в SQLite."""

    def get_rate(self, currency: str, on_date: dt.date) -> Rate | None:
        """Курс из кэша или None."""
        ...

    def put_rate(self, rate: Rate) -> None:
        """Сохраняет курс."""
        ...


def resolve_rate(
    currency: str,
    on_date: dt.date,
    *,
    today: dt.date,
    manual: Decimal | None,
    cache: RateCache,
    client_factory: Callable[[], httpx.Client] | None,
) -> RateLookup:
    """Курс для поступления: ручной, из кэша, с сайта НБГ — или ясная просьба задать `--rate`.

    Args:
        currency: Валюта поступления.
        on_date: Дата получения.
        today: Сегодня в Тбилиси: курса на будущую дату не бывает.
        manual: Курс из `--rate` или None.
        cache: Кэш курсов.
        client_factory: Как создать HTTP-клиент; None — в сеть не ходить.

    Returns:
        Курс и его источник.

    Raises:
        ConfigError: Дата в будущем, курс для лари не 1 или курс неположительный.
        RateUnavailableError: Курса нет ни в кэше, ни у НБГ.
    """
    if on_date > today:
        raise ConfigError(f'дата получения {on_date.isoformat()} в будущем — проверьте --date')
    if currency == 'GEL':
        if manual is not None and manual != 1:
            raise ConfigError('поступление в лари: курс всегда 1, --rate не нужен')
        return RateLookup(gel_per_unit=Decimal(1), source='gel')
    if manual is not None:
        if manual <= 0 or not manual.is_finite():
            raise ConfigError('--rate: курс должен быть больше нуля')
        return RateLookup(gel_per_unit=manual, source='manual')
    cached = cache.get_rate(currency, on_date)
    if cached is not None:
        return RateLookup(gel_per_unit=cached.gel_per_unit, source='nbg', source_date=cached.source_date)
    if client_factory is None:
        raise _unavailable(currency, on_date, 'в кэше нет, а запрос к НБГ выключен (--no-fetch)')
    with client_factory() as client:
        rate = fetch_nbg_rate(client, currency, on_date)
    cache.put_rate(rate)
    return RateLookup(gel_per_unit=rate.gel_per_unit, source='nbg', source_date=rate.source_date)

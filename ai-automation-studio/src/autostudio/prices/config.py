"""Конфиг клиента монитора цен: товары, ссылки конкурентов, пороги и правила вежливости."""

import re
from decimal import Decimal
from typing import Final, Literal

import httpx
import msgspec
from selectolax.lexbor import LexborHTMLParser, SelectolaxError

from autostudio.clientconf import ClientInfo, NotifyConfig, SinkConfig
from autostudio.errors import ConfigError
from autostudio.prices.money import MAX_AMOUNT, MAX_DECIMALS, bounded_amount
from autostudio.prices.urls import covering_site, host_problem, url_problem, wire_host

_PRODUCT_ID: Final = re.compile(r'^[A-Za-z0-9][\w.-]{0,63}$')
_CURRENCY: Final = re.compile(r'^[A-Z]{3}$')
_ATTR: Final = re.compile(r'^[A-Za-z_:][-\w:.]{0,63}$')
_EMAIL: Final = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_PLACEHOLDERS: Final = frozenset({'contact_email', 'token', 'version'})
_MIN_CHANGE_PCT: Final = Decimal('0.1')
_MAX_CHANGE_PCT: Final = Decimal(100)
_MAX_REASON: Final = 200

# Токен, по которому robots.txt сайта может обратиться именно к нам (`User-agent: autostudio-prices`).
ROBOTS_AGENT: Final = 'autostudio-prices'
DEFAULT_USER_AGENT: Final = 'Mozilla/5.0 (compatible; {token}/{version}; +mailto:{contact_email})'
# Сколько сайтов проверяется одновременно. Страницы одного сайта всё равно идут по очереди
# с паузой — параллельность ускоряет проход, не увеличивая нагрузку ни на один сайт.
PARALLEL_SITES: Final = 4
# Причина «не проверено», которая сбоем не считается: сайт поставлен на паузу в конфиге.
PAUSED: Final = 'paused'


class ConfigFieldError(ConfigError, ValueError):
    """Ошибка поля конфига.

    Наследует и `ValueError`: msgspec превращает её в ошибку проверки с путём до поля
    (`$.prices.products[2].offers[0]`), так что оператор видит, какой товар и какая ссылка
    сломаны, а не только «селектор не разбирается».
    """


class Offer(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Страница товара у конкурента.

    Attributes:
        competitor: Название конкурента, как его увидит клиент в таблице.
        url: Публичная страница товара (http/https).
        selector: CSS-селектор элемента цены — проверенный оператором через `probe`.
        attr: Читать атрибут элемента (`content`, `data-price`) вместо его текста.
        currency: Валюта магазина: для страниц, где валюта не названа или названа знаком «$».
    """

    competitor: str
    url: str
    selector: str | None = None
    attr: str | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if not self.competitor.strip() or len(self.competitor) > 80 or '\n' in self.competitor:
            raise ConfigFieldError(f'offer.competitor={self.competitor!r}: непустая строка до 80 символов')
        where = f'offer «{self.competitor}»'
        problem = url_problem(self.url)
        if problem is not None:
            raise ConfigFieldError(f'{where}: url={self.url!r}: {problem}')
        if self.selector is not None:
            _check_selector(self.selector, where)
        if self.attr is not None:
            if self.selector is None:
                raise ConfigFieldError(f'{where}: offer.attr={self.attr!r} без offer.selector: нечего читать')
            if not _ATTR.match(self.attr):
                raise ConfigFieldError(f'{where}: offer.attr={self.attr!r}: недопустимое имя атрибута')
        if self.currency is not None and not _CURRENCY.match(self.currency):
            raise ConfigFieldError(f'{where}: offer.currency={self.currency!r}: код ISO 4217 заглавными, например GEL')


def _check_selector(selector: str, where: str) -> None:
    # Опечатка в селекторе должна всплыть на `check`, а не ночью на боевом запуске.
    if not selector.strip():
        raise ConfigFieldError(f'{where}: offer.selector пуст')
    try:
        LexborHTMLParser('<p></p>').css(selector)
    except SelectolaxError:
        raise ConfigFieldError(f'{where}: offer.selector={selector!r}: CSS-селектор не разбирается') from None


class Product(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Товар клиента и его страницы у конкурентов.

    Attributes:
        id: Стабильный идентификатор (артикул): по нему хранится история цен.
        name: Название для таблицы и уведомлений.
        own_price: Цена клиента; без неё нет сравнения «дешевле вас».
        currency: Валюта цены клиента.
        offers: Страницы конкурентов.
    """

    id: str
    name: str
    own_price: Decimal | None = None
    currency: str = 'GEL'
    offers: list[Offer] = msgspec.field(default_factory=list)

    def __post_init__(self) -> None:
        if not _PRODUCT_ID.match(self.id):
            raise ConfigFieldError(f'product.id={self.id!r}: латиница, цифры, «-», «_», «.», до 64 символов')
        if not self.name.strip():
            raise ConfigFieldError(f'product {self.id}: пустое name')
        if self.own_price is not None and bounded_amount(self.own_price) != self.own_price:
            raise ConfigFieldError(
                f'product {self.id}: own_price — больше нуля, до {MAX_AMOUNT}, не больше {MAX_DECIMALS} знаков '
                'после точки'
            )
        if not _CURRENCY.match(self.currency):
            raise ConfigFieldError(f'product {self.id}: currency={self.currency!r} — код ISO 4217, например GEL')
        competitors = [offer.competitor.strip().lower() for offer in self.offers]
        if len(set(competitors)) != len(competitors):
            raise ConfigFieldError(f'product {self.id}: один конкурент указан дважды — уведомления станут неоднозначны')


class SiteRule(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Правила одного сайта конкурента: `[prices.sites."shop.ge"]`.

    Ключ — домен; правило действует и на его поддомены (`www.shop.ge`, `m.shop.ge`).

    Attributes:
        paused: Не обращаться к сайту совсем — ни к страницам, ни к robots.txt, ни после
            редиректа с другого сайта (страницы или robots.txt). Ставится в день претензии
            владельца сайта (бриф 5.8) и остаётся, когда его ссылки удалены из конфига.
        paused_reason: Почему (видит оператор в `check`): «письмо владельца 2026-10-20».
    """

    paused: bool = False
    paused_reason: str | None = None

    def __post_init__(self) -> None:
        reason = self.paused_reason
        if reason is not None and (not reason.strip() or len(reason) > _MAX_REASON or '\n' in reason):
            raise ConfigFieldError(f'paused_reason={reason!r}: непустая строка до {_MAX_REASON} символов')


class PricesSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[prices]`.

    Attributes:
        products: Товары клиента.
        contact_email: Адрес оператора в User-Agent: владелец сайта должен знать, кому написать.
        alert_change_pct: Порог изменения цены для уведомления, в процентах.
        alert_undercut: Сообщать, когда конкурент стал дешевле клиента.
        user_agent: Шаблон User-Agent с `{contact_email}`, `{token}`, `{version}`.
        respect_robots: Соблюдать robots.txt (выключать — только по письменному разрешению сайта).
        min_delay_s: Пауза между запросами к одному сайту, секунды.
        timeout_s: Таймаут одного запроса.
        max_retries: Повторы на 429/5xx/таймаут.
        max_page_bytes: Предел размера страницы.
        allow_free: Считать «Бесплатно»/0 ценой 0 (иначе это «цена не найдена»).
        llm_fallback: Разрешить платную ступень — модель для страниц без разметки.
        llm_max_pages_per_run: Сколько страниц за запуск может прочитать модель.
        failure_alert_after: После скольких сбоев подряд одной ссылки звать оператора.
        sheet_history: `changes` — в «History» только новые значения; `all` — каждая проверка.
        max_run_minutes: Предел длины прохода: что не успели проверить — «не проверено» в этот раз,
            а не зависший проход, который держит и следующие запуски таймера.
        sites: Правила по сайтам (домен → `SiteRule`): например, пауза по претензии владельца.
            Ключ можно писать и в punycode (`xn--…`), и в Unicode: сравнение идёт в IDNA-записи.
            Пауза действует и без ссылок на этот сайт в конфиге: как запрет, в том числе для
            редиректов.
    """

    products: list[Product]
    contact_email: str
    alert_change_pct: Decimal = Decimal(3)
    alert_undercut: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    respect_robots: bool = True
    min_delay_s: float = 5.0
    timeout_s: float = 20.0
    max_retries: int = 2
    max_page_bytes: int = 2_000_000
    allow_free: bool = False
    llm_fallback: bool = False
    llm_max_pages_per_run: int = 20
    failure_alert_after: int = 3
    sheet_history: Literal['changes', 'all'] = 'changes'
    max_run_minutes: float = 30.0
    sites: dict[str, SiteRule] = msgspec.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.products:
            raise ConfigError('prices.products пуст: нечего мониторить')
        ids = [product.id for product in self.products]
        duplicates = sorted({pid for pid in ids if ids.count(pid) > 1})
        if duplicates:
            raise ConfigError(f'повторяются product.id: {", ".join(duplicates)}')
        if not _EMAIL.match(self.contact_email):
            raise ConfigError(f'prices.contact_email={self.contact_email!r}: нужен рабочий адрес оператора')
        _check_user_agent(self.user_agent)
        pct = self.alert_change_pct
        if not pct.is_finite() or not _MIN_CHANGE_PCT <= pct <= _MAX_CHANGE_PCT:
            raise ConfigError(f'prices.alert_change_pct={pct}: допустимо от {_MIN_CHANGE_PCT} до {_MAX_CHANGE_PCT}')
        _check_range('min_delay_s', self.min_delay_s, 1, 600)
        _check_range('timeout_s', self.timeout_s, 1, 120)
        _check_range('max_retries', self.max_retries, 0, 5)
        _check_range('max_page_bytes', self.max_page_bytes, 10_000, 20_000_000)
        _check_range('llm_max_pages_per_run', self.llm_max_pages_per_run, 0, 1000)
        _check_range('failure_alert_after', self.failure_alert_after, 1, 100)
        _check_range('max_run_minutes', self.max_run_minutes, 1, 360)
        _check_sites(self.sites)

    @property
    def offer_count(self) -> int:
        return sum(len(product.offers) for product in self.products)

    @property
    def paused_sites(self) -> frozenset[str]:
        """Домены на паузе (ключи `[prices.sites]` с `paused = true`)."""
        return frozenset(site for site, rule in self.sites.items() if rule.paused)

    def paused_site(self, url: str) -> str | None:
        """Домен на паузе, под который попадает адрес, или None.

        Args:
            url: Адрес страницы (уже проверенный `url_problem`).

        Returns:
            Ключ `[prices.sites]` или None.
        """
        return covering_site(httpx.URL(url).host, self.paused_sites)

    def render_user_agent(self, version: str) -> str:
        """User-Agent, который увидит сайт конкурента."""
        return self.user_agent.format(contact_email=self.contact_email, token=ROBOTS_AGENT, version=version)


def _check_sites(sites: dict[str, SiteRule]) -> None:
    # Правило без ссылок на этот сайт — не ошибка: после претензии ссылки удаляют из конфига, а
    # пауза остаётся запретом (и для редиректов с других сайтов). Опечатку в домене видно в
    # `check`: «ссылок на него в конфиге нет».
    seen: dict[str, str] = {}
    for site in sites:
        where = f'prices.sites."{site}"'
        if site != site.strip().rstrip('.').lower():
            raise ConfigError(f'{where}: домен строчными, без пробелов и точки в конце')
        problem = host_problem(site)
        if problem is not None:
            raise ConfigError(f'{where}: {problem}')
        literal = f'[{site}]' if ':' in site else site
        if url_problem(f'https://{literal}/') is not None:
            # Ключ годится, только если годилась бы и ссылка на этот хост (битый punycode, имя не по IDNA).
            raise ConfigError(f'{where}: не домен (битый punycode или недопустимые символы)')
        # Сравнение с хостами ссылок идёт в IDNA-записи: ключ в punycode из письма владельца
        # и Unicode-ключ — один и тот же сайт.
        wire = wire_host(site)
        if wire in seen:
            raise ConfigError(f'{where}: тот же домен, что и "{seen[wire]}" (punycode и Unicode-запись)')
        seen[wire] = site


def _check_range(name: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ConfigError(f'prices.{name}={value}: допустимо от {low} до {high}')


def _check_user_agent(template: str) -> None:
    if '\r' in template or '\n' in template:
        raise ConfigError('prices.user_agent: перевод строки в заголовке запрещён')
    if '{contact_email}' not in template:
        # Скрытый бот — это и неэтично, и первый повод для бана: сайт должен видеть, кто мы.
        raise ConfigError('prices.user_agent должен содержать {contact_email}')
    try:
        template.format(**dict.fromkeys(_PLACEHOLDERS, 'x'))
    except (KeyError, IndexError, ValueError) as exc:
        raise ConfigError(f'prices.user_agent: неизвестная подстановка {exc}') from None


class PricesConfig(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Полный конфиг клиента монитора цен."""

    client: ClientInfo
    notify: NotifyConfig = NotifyConfig()
    sink: SinkConfig = SinkConfig()
    prices: PricesSection

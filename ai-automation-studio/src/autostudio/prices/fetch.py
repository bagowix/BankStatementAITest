"""Вежливая загрузка страниц конкурентов: robots.txt, паузы, повторы, пределы и защита от SSRF.

Вежливость здесь — не украшение, а условие работы: сайт, который мы перегрузили или
обошли его robots.txt, банит IP оператора, и клиент остаётся без цен. Поэтому:

- к одному сайту — не чаще раза в `min_delay_s`, и не больше одного запроса одновременно;
  «сайт» — домен без `www.` (`site_key`): `www.shop.ge` и `shop.ge`, http и https делят одну
  очередь, как того требует бриф 5.8 (запросы в час к домену);
- robots.txt читается один раз за запуск на origin и разбирается по RFC 9309 (protego:
  `*`, `$`, самое длинное совпадение — одинаково на всех версиях Python). `urllib.robotparser`
  зависит от версии: на 3.12.3 он верен в 8 из 21 случая тестов пакета и в основном
  разрешает то, что сайт закрыл; на 3.13.16 — во всех (`var/review_prices/p11_…`). BOM в
  начале файла отбрасывается (иначе первая группа правил молча пропала бы). Нет файла (4xx,
  кроме 429) — можно; 5xx и 429 — сервер перегружен (RFC 9309 §2.3.1.3), и адрес robots.txt
  не прошёл проверку — в этот запуск сайт не трогаем (`robots_unavailable`, а не «сайт
  запретил»). Таймаут и сетевой сбой на robots.txt — можно: это сознательное отступление от
  RFC 9309 §2.3.1.4 (решение владельца, записано в docs/packages/prices.md, «Право и этика»);
- 429/5xx/таймаут — повтор с экспоненциальной паузой; `Retry-After` соблюдается, а если
  сайт просит подождать дольше предела, страница откладывается до следующего запуска;
- страница больше `max_page_bytes` обрывается на потоке, не дочитываясь в память, а у каждой
  попытки есть общий срок (`timeout_s` у httpx — это предел на каждый шаг чтения, и сайт,
  отдающий по байту, иначе держал бы проход часами);
- редиректы проходятся вручную (`follow_redirects=False` у каждого запроса), и каждый адрес
  заново проверяется на SSRF (включая адреса, в которые резолвится имя — то самое
  punycode-имя, к которому подключится httpx), на паузу сайта и на robots.txt;
- сайт на паузе (`[prices.sites."…"] paused = true`, претензия владельца) не получает от нас
  ни одного запроса — ни страницы, ни robots.txt, ни DNS-запроса за его адресом, в том числе
  после редиректа страницы или robots.txt с другого сайта. Сайт, чей robots.txt уводит на сайт
  на паузе (алиас домена того, кто просил остановиться), в этот запуск тоже не трогаем (`paused`).

Ограничение: между нашей проверкой DNS и соединением httpx имя резолвится ещё раз
(DNS rebinding). Для полной защиты сервер оператора закрывает исходящие соединения во
внутренние сети на уровне файрвола — это записано в руководстве по развёртыванию.
"""

import asyncio
import codecs
import datetime as dt
import email.utils
import ipaddress
import logging
import math
import re
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Final, Self

import httpx
import msgspec
from protego import Protego

from autostudio import __version__
from autostudio.clock import Clock, system_now
from autostudio.prices.config import PAUSED, ROBOTS_AGENT, PricesSection
from autostudio.prices.urls import covering_site, ip_is_public, origin, redact, site_key, url_problem

logger = logging.getLogger(__name__)

type Resolver = Callable[[str, int], Awaitable[list[str]]]
type Sleeper = Callable[[float], Awaitable[None]]
type Monotonic = Callable[[], float]

_HTML_TYPES: Final = frozenset({'', 'text/html', 'application/xhtml+xml'})
# RFC 9309: robots.txt разбирается минимум на первые 500 KiB, хвост можно отбросить.
_ROBOTS_LIMIT: Final = 512_000
_META_CHARSET: Final = re.compile(rb'<meta[^>]+charset=["\']?([A-Za-z0-9_-]+)', re.IGNORECASE)
_ACCEPT: Final = 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.5'
# Crawl-delay из robots.txt соблюдается, но не больше минуты: иначе один сайт растянул бы
# проход на часы. Дольше — значит, сайт не рад частым визитам, и частоту стоит обсудить.
_MAX_CRAWL_DELAY_S: Final = 60.0
# Общий срок одной попытки — во столько раз больше `timeout_s` (предела на шаг чтения).
_ATTEMPT_FACTOR: Final = 3.0
_MAX_RETRY_DIGITS: Final = 9
# robots.txt не прочитан по вине сайта (5xx, 429, адрес не проверен): сайт в этот запуск не
# трогаем. Это не «сайт запретил» (`robots_disallowed`): клиенту — «сайт не ответил».
ROBOTS_UNAVAILABLE: Final = 'robots_unavailable'
# Статусы robots.txt, при которых сервер просит не нагружать его (RFC 9309 §2.3.1.3, Google).
_ROBOTS_OVERLOADED: Final = frozenset({'server_error', 'rate_limited'})


class FetchResult(msgspec.Struct, frozen=True, kw_only=True):
    """Страница загружена.

    Attributes:
        html: Текст страницы.
        final_url: Адрес после редиректов.
        status: HTTP-статус.
    """

    html: str
    final_url: str
    status: int


class FetchFailure(msgspec.Struct, frozen=True, kw_only=True):
    """Страницу загрузить нельзя или не удалось.

    Attributes:
        reason: `robots_disallowed`, `robots_unavailable`, `blocked_url`, `http_error`,
            `rate_limited`, `server_error`, `timeout`, `network`, `dns`, `too_large`, `not_html`,
            `too_many_redirects`, `paused`.
        detail: Пояснение для оператора (без query-строк и без текста исключений httpx); для
            `paused` — домен на паузе.
        status: HTTP-статус, если ответ был.
    """

    reason: str
    detail: str = ''
    status: int | None = None


class FetchSettings(msgspec.Struct, frozen=True, kw_only=True):
    """Правила вежливости и пределы одного загрузчика.

    Attributes:
        paused_sites: Домены на паузе: к ним (и к их поддоменам) запросов нет совсем.
    """

    user_agent: str
    robots_agent: str = ROBOTS_AGENT
    min_delay_s: float = 5.0
    timeout_s: float = 20.0
    max_retries: int = 2
    max_page_bytes: int = 2_000_000
    respect_robots: bool = True
    max_redirects: int = 5
    max_retry_after_s: float = 60.0
    backoff_base_s: float = 2.0
    attempt_timeout_s: float | None = None
    paused_sites: frozenset[str] = frozenset()

    @property
    def attempt_deadline_s(self) -> float:
        """Общий срок одной попытки: соединение, заголовки и всё тело страницы."""
        return self.attempt_timeout_s or self.timeout_s * _ATTEMPT_FACTOR

    @classmethod
    def from_section(cls, section: PricesSection, version: str = __version__) -> Self:
        """Правила из секции `[prices]` конфига клиента."""
        return cls(
            user_agent=section.render_user_agent(version),
            min_delay_s=section.min_delay_s,
            timeout_s=section.timeout_s,
            max_retries=section.max_retries,
            max_page_bytes=section.max_page_bytes,
            respect_robots=section.respect_robots,
            paused_sites=section.paused_sites,
        )


async def system_resolver(host: str, port: int) -> list[str]:
    """Адреса, в которые резолвится имя (системный резолвер, без блокировки цикла)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class _Redirect(msgspec.Struct, frozen=True):
    location: str


class _Transient(msgspec.Struct, frozen=True, kw_only=True):
    # Временный сбой: повторить можно, если позволяет бюджет повторов.
    reason: str
    status: int | None = None
    retry_after: float | None = None
    detail: str = ''

    def failure(self) -> FetchFailure:
        return FetchFailure(reason=self.reason, detail=self.detail, status=self.status)


class _Body(msgspec.Struct, frozen=True, kw_only=True):
    body: bytes
    status: int
    charset: str | None


type _Outcome = _Body | _Redirect | _Transient | FetchFailure


class _RobotsRules:
    """Правила robots.txt одного origin: «ограничений нет» (None) или разобранный файл."""

    def __init__(self, rules: Protego | None) -> None:
        self._rules = rules

    @classmethod
    def from_body(cls, body: bytes) -> Self:
        # utf-8-sig отбрасывает BOM: protego его не снимает (str.strip() не считает U+FEFF
        # пробелом), и «\ufeffUser-agent: *» стал бы неизвестной строкой — а с ней пропали бы
        # Disallow и Crawl-delay первой группы. Google тоже игнорирует BOM в robots.txt.
        return cls(Protego.parse(body.decode('utf-8-sig', errors='replace')))

    def allows(self, agent: str, url: str) -> bool:
        if self._rules is None:
            return True
        return self._rules.can_fetch(url, agent)

    def crawl_delay(self, agent: str) -> float:
        if self._rules is None:
            return 0.0
        delay = self._rules.crawl_delay(agent)
        # Мусор вроде «Crawl-delay: 1e400» паузой не считается.
        return delay if delay is not None and math.isfinite(delay) and delay > 0 else 0.0


def decode_html(body: bytes, charset: str | None) -> str:
    """Байты страницы в текст: BOM, затем charset из заголовка, затем `<meta charset>`, затем UTF-8.

    Args:
        body: Тело ответа.
        charset: Кодировка из Content-Type или None.

    Returns:
        Текст; нераспознанные байты заменяются, а не роняют разбор.
    """
    if body.startswith(codecs.BOM_UTF8):
        return body[len(codecs.BOM_UTF8) :].decode('utf-8', errors='replace')
    candidate = charset
    if not candidate:
        match = _META_CHARSET.search(body[:4096])
        candidate = match.group(1).decode('ascii') if match else None
    try:
        return body.decode(codecs.lookup(candidate or 'utf-8').name, errors='replace')
    except (LookupError, UnicodeError, ValueError):
        # «charset=hex», «base64», «idna» — кодеки Python, но не кодировки текста: страницу
        # читаем как UTF-8, а не падаем на чужом заголовке.
        return body.decode('utf-8', errors='replace')


class PoliteFetcher:
    """Загрузчик одного запуска: кэш robots.txt и паузы живут, пока живёт объект.

    Args:
        http: Общий httpx-клиент (его редиректы не используются — проходим их сами).
        settings: Правила вежливости.
        sleep: Пауза (в демо и тестах — мгновенная).
        monotonic: Монотонные часы для пауз между запросами.
        resolver: DNS-резолвер (в демо и тестах — словарь).
        now: Часы для `Retry-After` в виде даты.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: FetchSettings,
        *,
        sleep: Sleeper = asyncio.sleep,
        monotonic: Monotonic = time.monotonic,
        resolver: Resolver = system_resolver,
        now: Clock = system_now,
    ) -> None:
        self._http = http
        self._s = settings
        self._sleep = sleep
        self._monotonic = monotonic
        self._resolve = resolver
        self._now = now
        self._headers = {'User-Agent': settings.user_agent, 'Accept': _ACCEPT}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Правила robots.txt по origin — или отказ сайту на этот запуск (`robots_unavailable`, `paused`).
        self._robots: dict[str, _RobotsRules | FetchFailure] = {}
        self._crawl_delay: dict[str, float] = {}

    async def fetch(self, url: str) -> FetchResult | FetchFailure:
        """Загружает страницу по всем правилам.

        Args:
            url: Адрес страницы товара.

        Returns:
            Страница или причина, по которой её нет.
        """
        current = url
        for _ in range(self._s.max_redirects + 1):
            paused = self._paused(current)
            if paused is not None:
                logger.info('сайт %s на паузе — %s не запрашивается', paused, redact(current))
                return FetchFailure(reason=PAUSED, detail=paused)
            blocked = await self._check_address(current)
            if blocked is not None:
                return blocked
            if self._s.respect_robots:
                refused = await self._robots_refusal(current, redirected=current != url)
                if refused is not None:
                    return refused
            outcome = await self._with_retries(current)
            if isinstance(outcome, _Redirect):
                current = outcome.location
                continue
            if isinstance(outcome, FetchFailure):
                logger.info('страница не загружена %s: %s %s', redact(current), outcome.reason, outcome.detail)
                return outcome
            return FetchResult(
                html=decode_html(outcome.body, outcome.charset), final_url=current, status=outcome.status
            )
        return FetchFailure(reason='too_many_redirects', detail=f'больше {self._s.max_redirects} редиректов')

    def _paused(self, url: str) -> str | None:
        if not self._s.paused_sites:
            return None
        try:
            # covering_site сравнивает хосты в IDNA-записи — и кодирование тоже под защитой:
            # адрес редиректа пишет чужой сайт.
            return covering_site(httpx.URL(url).host, self._s.paused_sites)
        except (httpx.InvalidURL, ValueError):
            return None  # битый адрес отклонит `_check_address`

    async def _check_address(self, url: str) -> FetchFailure | None:
        problem = url_problem(url)
        if problem is not None:
            return FetchFailure(reason='blocked_url', detail=problem)
        parsed = httpx.URL(url)
        host = parsed.host
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None  # IP-литерал уже проверен url_problem
        # Резолвим ровно то имя, к которому подключится httpx (IDNA 2008, punycode): системный
        # резолвер кодирует Unicode-имя по IDNA 2003, и «faß.example» превратился бы в другое
        # имя — «fass.example» — с другими адресами.
        wire_host = parsed.raw_host.decode('ascii', errors='replace')
        try:
            addresses = await self._resolve(wire_host, parsed.port or (443 if parsed.scheme == 'https' else 80))
        except OSError as exc:
            return FetchFailure(reason='dns', detail=f'{host}: {type(exc).__name__}')
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address.split('%', 1)[0])
            except ValueError:
                return FetchFailure(reason='blocked_url', detail=f'{host}: непонятный адрес {address!r}')
            if not ip_is_public(ip):
                # Хватит одного внутреннего адреса: httpx может подключиться к любому из списка.
                return FetchFailure(reason='blocked_url', detail=f'{host} указывает на внутренний адрес {ip}')
        if not addresses:
            return FetchFailure(reason='dns', detail=f'{host}: нет адресов')
        return None

    async def _robots_refusal(self, url: str, *, redirected: bool) -> FetchFailure | None:
        """Отказ по robots.txt (запрет, сайт недоступен, robots.txt на сайте на паузе) или None."""
        key = origin(url)
        async with self._lock_for(f'robots:{key}'):
            rules = self._robots.get(key)
            if rules is None:
                rules = await self._load_robots(key)
                self._robots[key] = rules
                if isinstance(rules, _RobotsRules):
                    self._note_crawl_delay(key, site_key(url), rules.crawl_delay(self._s.robots_agent))
        if isinstance(rules, FetchFailure):
            return rules
        if rules.allows(self._s.robots_agent, url):
            return None
        logger.info('robots.txt запрещает %s', redact(url))
        # Адрес в пояснении нужен, только если запрет встретился после редиректа.
        detail = f'robots.txt, после редиректа на {redact(url)}' if redirected else 'robots.txt'
        return FetchFailure(reason='robots_disallowed', detail=detail)

    def _note_crawl_delay(self, robots_origin: str, site: str, delay: float) -> None:
        if delay > _MAX_CRAWL_DELAY_S:
            logger.warning('%s просит Crawl-delay %s с — соблюдаем %s с', robots_origin, delay, _MAX_CRAWL_DELAY_S)
        # Очередь одна на домен, а robots.txt у http и https свои: соблюдаем больший Crawl-delay.
        self._crawl_delay[site] = max(self._crawl_delay.get(site, 0.0), min(delay, _MAX_CRAWL_DELAY_S))

    async def _load_robots(self, site: str) -> _RobotsRules | FetchFailure:
        current = f'{site}/robots.txt'
        for hop in range(self._s.max_redirects + 1):
            paused = self._paused(current)
            if paused is not None:
                # robots.txt уводит на сайт на паузе (обычно алиас домена того, кто просил
                # остановиться): к нему — ни DNS, ни запроса, и правила этого сайта лежат у него —
                # значит, и сам сайт в этот запуск не трогаем.
                logger.info('robots.txt %s уводит на сайт на паузе %s — сайт не проверяется', site, paused)
                return FetchFailure(reason=PAUSED, detail=paused)
            blocked = await self._check_address(current)
            if blocked is not None and hop == 0:
                # robots.txt того же сайта, чья страница только что прошла проверку, — не «файла
                # нет», а сбой (адрес не собрался, DNS моргнул): в этот запуск сайт не трогаем.
                logger.warning('robots.txt %s не проверен (%s) — сайт пропущен в этом запуске', site, blocked.reason)
                return FetchFailure(reason=ROBOTS_UNAVAILABLE, detail=f'robots.txt: {blocked.reason} {blocked.detail}')
            if blocked is not None:
                # Редирект robots.txt во внутреннюю сеть — как отсутствие файла (RFC 9309 §2.3.1.2).
                return _RobotsRules(None)
            outcome = await self._request(current, limit=_ROBOTS_LIMIT, html_only=False, truncate=True)
            if isinstance(outcome, _Redirect):
                current = outcome.location
                continue
            if isinstance(outcome, _Body):
                return _RobotsRules.from_body(outcome.body)
            if isinstance(outcome, _Transient) and outcome.reason in _ROBOTS_OVERLOADED:
                # 5xx и 429 — сервер просит не нагружать его: страницы сайта в этот запуск не
                # запрашиваем. Retry-After — в лог: следующий проход и так не раньше чем через час.
                wait = f', Retry-After {outcome.retry_after:g} с' if outcome.retry_after is not None else ''
                logger.warning(
                    'robots.txt %s отвечает HTTP %s%s — сайт пропущен в этом запуске', site, outcome.status, wait
                )
                return FetchFailure(
                    reason=ROBOTS_UNAVAILABLE, detail=f'robots.txt: HTTP {outcome.status}{wait}', status=outcome.status
                )
            # 4xx — файла нет: ограничений нет. Таймаут и сетевой сбой — тоже «можно»: сознательное
            # отступление от RFC 9309 §2.3.1.4 (решение владельца, docs/packages/prices.md): страница
            # всё равно идёт с паузой и повторами, а лёгший сайт так же не ответит и на неё.
            return _RobotsRules(None)
        return _RobotsRules(None)

    async def _with_retries(self, url: str) -> _Body | _Redirect | FetchFailure:
        attempt = 0
        while True:
            outcome = await self._request(url, limit=self._s.max_page_bytes, html_only=True, truncate=False)
            if not isinstance(outcome, _Transient):
                return outcome
            pause = self._pause(outcome, attempt)
            if pause is None:
                return outcome.failure()
            logger.info('%s: %s, повтор через %.1f с', redact(url), outcome.reason, pause)
            await self._sleep(pause)
            attempt += 1

    def _pause(self, transient: _Transient, attempt: int) -> float | None:
        if attempt >= self._s.max_retries:
            return None
        if transient.retry_after is not None:
            # Сайт просит подождать дольше нашего предела — значит, придём в следующий запуск,
            # а не будем стучаться раньше, чем нас попросили.
            return transient.retry_after if transient.retry_after <= self._s.max_retry_after_s else None
        return float(self._s.backoff_base_s * 2**attempt)

    def _lock_for(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def _wait_turn(self, site: str) -> None:
        last = self._last.get(site)
        if last is not None:
            pause = max(self._s.min_delay_s, self._crawl_delay.get(site, 0.0))
            wait = pause - (self._monotonic() - last)
            if wait > 0:
                await self._sleep(wait)

    async def _request(self, url: str, *, limit: int, html_only: bool, truncate: bool) -> _Outcome:
        site = site_key(url)
        async with self._lock_for(site):
            await self._wait_turn(site)
            try:
                # follow_redirects=False — у каждого запроса, а не у клиента: общий httpx-клиент
                # из CoreProvider ходит по редиректам сам (так нужно Telegram и вебхукам), а здесь
                # каждый следующий адрес обязан заново пройти SSRF, паузу сайта и robots.txt.
                async with (
                    asyncio.timeout(self._s.attempt_deadline_s),
                    self._http.stream(
                        'GET', url, headers=self._headers, timeout=self._s.timeout_s, follow_redirects=False
                    ) as response,
                ):
                    return await self._consume(response, limit=limit, html_only=html_only, truncate=truncate)
            except httpx.TimeoutException:
                return _Transient(reason='timeout', detail=f'дольше {self._s.timeout_s:g} с')
            except TimeoutError:
                return _Transient(
                    reason='timeout', detail=f'страница грузилась дольше {self._s.attempt_deadline_s:g} с'
                )
            except httpx.TransportError as exc:
                # В тексте исключения httpx — полный URL; наружу идёт только тип.
                return _Transient(reason='network', detail=type(exc).__name__)
            except httpx.HTTPError as exc:  # pragma: no cover - прочие ошибки httpx при GET не возникают
                return FetchFailure(reason='network', detail=type(exc).__name__)
            except (httpx.InvalidURL, ValueError) as exc:
                # httpx строит запрос редиректа и при follow_redirects=False, а хост из Location
                # декодирует IDNA: «https://xn--/p» роняет его IDNAError (это ValueError, не
                # httpx.HTTPError). Такой адрес мы бы и так не открыли — отказ, а не трассировка.
                return FetchFailure(reason='blocked_url', detail=f'битый адрес редиректа: {type(exc).__name__}')
            finally:
                self._last[site] = self._monotonic()

    async def _consume(self, response: httpx.Response, *, limit: int, html_only: bool, truncate: bool) -> _Outcome:
        status = response.status_code
        if response.has_redirect_location:
            return _Redirect(str(response.url.join(response.headers['location'])))
        if status == httpx.codes.TOO_MANY_REQUESTS or status >= httpx.codes.INTERNAL_SERVER_ERROR:
            reason = 'rate_limited' if status == httpx.codes.TOO_MANY_REQUESTS else 'server_error'
            return _Transient(
                reason=reason, status=status, retry_after=self._retry_after(response), detail=f'HTTP {status}'
            )
        if not httpx.codes.is_success(status):
            return FetchFailure(reason='http_error', detail=f'HTTP {status}', status=status)
        mime = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if html_only and mime not in _HTML_TYPES:
            return FetchFailure(reason='not_html', detail=mime, status=status)
        declared = response.headers.get('content-length', '')
        if not truncate and declared.isdigit() and int(declared) > limit:
            return FetchFailure(reason='too_large', detail=f'{declared} байт > {limit}', status=status)
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > limit:
                if truncate:
                    del body[limit:]
                    break
                return FetchFailure(reason='too_large', detail=f'больше {limit} байт', status=status)
        return _Body(body=bytes(body), status=status, charset=response.charset_encoding)

    def _retry_after(self, response: httpx.Response) -> float | None:
        value = response.headers.get('retry-after', '').strip()
        if not value:
            return None
        if value.isascii() and value.isdigit():
            # isdigit() без isascii() пропустил бы «²» — float() на нём падает. Число в сотни
            # цифр — просто «очень долго»: страница отложится до следующего запуска.
            return float(value) if len(value) <= _MAX_RETRY_DIGITS else math.inf
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        return max(0.0, (when - self._now()).total_seconds())

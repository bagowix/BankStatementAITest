import asyncio
import datetime as dt
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from autostudio.prices.config import PricesSection, Product
from autostudio.prices.fetch import (
    FetchFailure,
    FetchResult,
    FetchSettings,
    PoliteFetcher,
    decode_html,
    system_resolver,
)

PUBLIC = '93.184.215.14'
HTML = {'content-type': 'text/html; charset=utf-8'}

type Handler = Callable[[httpx.Request], httpx.Response]


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


def resolver(mapping: dict[str, list[str]] | None = None):
    async def resolve(host: str, port: int) -> list[str]:
        if mapping and host in mapping:
            return mapping[host]
        if host.endswith('.example'):
            return [PUBLIC]
        raise OSError('no such host')

    return resolve


class Site:
    """Маленький интернет из словаря: путь → ответ; всё обращения записываются."""

    def __init__(self, routes: dict[str, httpx.Response | Handler]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f'{request.url.host}{request.url.path}'
        route = self.routes.get(key)
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    def paths(self) -> list[str]:
        return [f'{r.url.host}{r.url.path}' for r in self.requests]


def make(site: Site, time: FakeTime | None = None, **overrides) -> tuple[PoliteFetcher, httpx.AsyncClient, FakeTime]:
    clock = time or FakeTime()
    dns = overrides.pop('dns', None)
    settings = FetchSettings(
        user_agent='Mozilla/5.0 (compatible; autostudio-prices/0.1; +mailto:ops@studio.example)',
        **{'min_delay_s': 5.0, 'max_retries': 2, 'max_page_bytes': 10_000, **overrides},
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(site))
    fetcher = PoliteFetcher(
        http,
        settings,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        resolver=resolver(dns),
        now=lambda: dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC),
    )
    return fetcher, http, clock


def ok(body: str = '<p>1 ₾</p>') -> httpx.Response:
    return httpx.Response(200, headers=HTML, content=body.encode())


async def test__fetch__plain_page__html_and_identifying_headers():
    site = Site({'a.example/p': ok('<p>2 999 ₾</p>')})
    fetcher, http, _ = make(site)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchResult)
    assert result.html == '<p>2 999 ₾</p>'
    assert result.status == 200
    page_request = site.requests[-1]
    assert 'ops@studio.example' in page_request.headers['user-agent']
    assert 'text/html' in page_request.headers['accept']
    assert site.paths() == ['a.example/robots.txt', 'a.example/p']


async def test__fetch__robots_disallow__skipped_without_touching_page():
    site = Site(
        {
            'a.example/robots.txt': httpx.Response(200, text='User-agent: *\nDisallow: /private/\n'),
            'a.example/private/x': ok(),
        }
    )
    fetcher, http, _ = make(site)
    async with http:
        result = await fetcher.fetch('https://a.example/private/x')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'robots_disallowed'
    assert site.paths() == ['a.example/robots.txt']


async def test__fetch__robots_rule_for_our_agent__respected():
    rules = 'User-agent: *\nAllow: /\n\nUser-agent: autostudio-prices\nDisallow: /\n'
    site = Site({'a.example/robots.txt': httpx.Response(200, text=rules), 'a.example/p': ok()})
    fetcher, http, _ = make(site)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'robots_disallowed'


async def test__fetch__robots_cached_per_site_per_run():
    site = Site({'a.example/p1': ok(), 'a.example/p2': ok()})  # robots.txt → 404 → можно
    fetcher, http, _ = make(site)
    async with http:
        assert isinstance(await fetcher.fetch('https://a.example/p1'), FetchResult)
        assert isinstance(await fetcher.fetch('https://a.example/p2'), FetchResult)
    assert site.paths().count('a.example/robots.txt') == 1


async def test__fetch__robots_5xx__site_skipped_this_run():
    site = Site({'a.example/robots.txt': httpx.Response(503), 'a.example/p': ok()})
    fetcher, http, _ = make(site)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'robots_disallowed'


async def test__fetch__robots_unreachable__allowed():
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused', request=request)

    site = Site({'a.example/robots.txt': broken, 'a.example/p': ok()})
    fetcher, http, _ = make(site)
    async with http:
        assert isinstance(await fetcher.fetch('https://a.example/p'), FetchResult)


async def test__fetch__robots_redirect_followed_and_large_file_truncated():
    rules = 'User-agent: *\nDisallow: /private/\n' + '#' * 600_000
    site = Site(
        {
            'a.example/robots.txt': httpx.Response(301, headers={'location': 'https://cdn.example/robots.txt'}),
            'cdn.example/robots.txt': httpx.Response(200, text=rules),
        }
    )
    fetcher, http, _ = make(site)
    async with http:
        result = await fetcher.fetch('https://a.example/private/1')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'robots_disallowed'


async def test__fetch__robots_redirect_to_private__treated_as_absent():
    site = Site(
        {
            'a.example/robots.txt': httpx.Response(302, headers={'location': 'http://10.0.0.1/robots.txt'}),
            'a.example/p': ok(),
        }
    )
    fetcher, http, _ = make(site)
    async with http:
        assert isinstance(await fetcher.fetch('https://a.example/p'), FetchResult)
    assert '10.0.0.1/robots.txt' not in site.paths()


async def test__fetch__robots_redirect_loop__treated_as_absent():
    loop = httpx.Response(302, headers={'location': 'https://a.example/robots.txt'})
    site = Site({'a.example/robots.txt': loop, 'a.example/p': ok()})
    fetcher, http, _ = make(site, min_delay_s=1.0)
    async with http:
        assert isinstance(await fetcher.fetch('https://a.example/p'), FetchResult)


async def test__fetch__respect_robots_off__robots_not_requested():
    site = Site({'a.example/p': ok()})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        await fetcher.fetch('https://a.example/p')
    assert site.paths() == ['a.example/p']


async def test__fetch__429_with_retry_after__waits_then_succeeds():
    replies = iter([httpx.Response(429, headers={'retry-after': '3'}), ok('<p>fine</p>')])
    site = Site({'a.example/p': lambda r: next(replies)})
    fetcher, http, time = make(site, respect_robots=False, min_delay_s=1.0)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchResult)
    assert 3.0 in time.sleeps


async def test__fetch__429_retry_after_too_long__deferred_to_next_run():
    site = Site({'a.example/p': httpx.Response(429, headers={'retry-after': '3600'})})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert (result.reason, result.status) == ('rate_limited', 429)
    assert len(site.requests) == 1


async def test__fetch__retry_after_http_date__converted_to_seconds():
    when = 'Tue, 20 Oct 2026 09:00:07 GMT'
    replies = iter([httpx.Response(503, headers={'retry-after': when}), ok()])
    site = Site({'a.example/p': lambda r: next(replies)})
    fetcher, http, time = make(site, respect_robots=False, min_delay_s=1.0)
    async with http:
        assert isinstance(await fetcher.fetch('https://a.example/p'), FetchResult)
    assert 7.0 in time.sleeps


async def test__fetch__retry_after_date_without_zone__treated_as_utc():
    replies = iter([httpx.Response(503, headers={'retry-after': 'Tue, 20 Oct 2026 09:00:04 -0000'}), ok()])
    site = Site({'a.example/p': lambda r: next(replies)})
    fetcher, http, time = make(site, respect_robots=False, min_delay_s=1.0)
    async with http:
        assert isinstance(await fetcher.fetch('https://a.example/p'), FetchResult)
    assert 4.0 in time.sleeps


async def test__fetch__retry_after_garbage__exponential_backoff():
    replies = iter(
        [httpx.Response(503, headers={'retry-after': 'soon'}), httpx.Response(502), ok()],
    )
    site = Site({'a.example/p': lambda r: next(replies)})
    fetcher, http, time = make(site, respect_robots=False, min_delay_s=1.0)
    async with http:
        assert isinstance(await fetcher.fetch('https://a.example/p'), FetchResult)
    assert time.sleeps[:1] == [2.0]
    assert 4.0 in time.sleeps


async def test__fetch__server_error_persists__server_error_after_retries():
    site = Site({'a.example/p': httpx.Response(500)})
    fetcher, http, _ = make(site, respect_robots=False, max_retries=2)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'server_error'
    assert len(site.requests) == 3


async def test__fetch__timeout__retried_then_timeout_without_url_in_detail():
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout('timed out https://a.example/p?token=SECRET', request=request)

    site = Site({'a.example/p': slow})
    fetcher, http, _ = make(site, respect_robots=False, max_retries=1)
    async with http:
        result = await fetcher.fetch('https://a.example/p?token=SECRET')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'timeout'
    assert 'SECRET' not in result.detail
    assert len(site.requests) == 2


async def test__fetch__connection_error__network_type_only():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('cannot connect to https://a.example/?token=SECRET', request=request)

    site = Site({'a.example/p': down})
    fetcher, http, _ = make(site, respect_robots=False, max_retries=0)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert (result.reason, result.detail) == ('network', 'ConnectError')


async def test__fetch__404__http_error_with_status():
    site = Site({})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        result = await fetcher.fetch('https://a.example/gone')
    assert isinstance(result, FetchFailure)
    assert (result.reason, result.status) == ('http_error', 404)


async def test__fetch__declared_oversized_page__too_large():
    site = Site({'a.example/p': ok('x' * 20_000)})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'too_large'


async def test__fetch__streamed_oversized_page__aborted_mid_stream():
    chunks = [b'x' * 4000] * 10
    site = Site({'a.example/p': httpx.Response(200, headers=HTML, stream=Chunks(chunks))})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'too_large'
    assert 'больше 10000' in result.detail


async def test__fetch__json_content_type__not_html():
    site = Site({'a.example/p': httpx.Response(200, json={'price': 1})})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'not_html'


async def test__fetch__redirect_to_public__followed_with_final_url():
    site = Site(
        {
            'a.example/old': httpx.Response(301, headers={'location': '/new'}),
            'a.example/new': ok('<p>moved</p>'),
        }
    )
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        result = await fetcher.fetch('https://a.example/old')
    assert isinstance(result, FetchResult)
    assert result.final_url == 'https://a.example/new'


@pytest.mark.parametrize(
    'location',
    ['http://127.0.0.1/admin', 'http://169.254.169.254/latest/meta-data/', 'http://[::1]/', 'file:///etc/passwd'],
)
async def test__fetch__redirect_to_private_ip__refused(location):
    site = Site({'a.example/p': httpx.Response(302, headers={'location': location})})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'blocked_url'
    assert len(site.requests) == 1


async def test__fetch__redirect_to_name_resolving_inside__refused():
    site = Site({'a.example/p': httpx.Response(302, headers={'location': 'https://evil.example/x'})})
    fetcher, http, _ = make(site, respect_robots=False, dns={'evil.example': [PUBLIC, '10.1.2.3']})
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'blocked_url'
    assert '10.1.2.3' in result.detail


async def test__fetch__redirect_loop__too_many_redirects():
    site = Site({'a.example/p': httpx.Response(302, headers={'location': '/p'})})
    fetcher, http, _ = make(site, respect_robots=False, min_delay_s=1.0)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'too_many_redirects'


async def test__fetch__private_url_given_directly__refused_without_request():
    site = Site({})
    fetcher, http, _ = make(site)
    async with http:
        result = await fetcher.fetch('http://192.168.1.1/')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'blocked_url'
    assert site.requests == []


async def test__fetch__public_ip_literal__no_dns_needed():
    site = Site({f'{PUBLIC}/p': ok()})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        assert isinstance(await fetcher.fetch(f'http://{PUBLIC}/p'), FetchResult)


@pytest.mark.parametrize(
    ('dns', 'reason'), [({}, 'dns'), ({'a.example': []}, 'dns'), ({'a.example': ['junk']}, 'blocked_url')]
)
async def test__fetch__dns_problems__reported(dns, reason):
    site = Site({})
    fetcher, http, _ = make(site, respect_robots=False, dns=dns)
    async with http:
        url = 'https://unknown.test/p' if not dns else 'https://a.example/p'
        result = await fetcher.fetch(url)
    assert isinstance(result, FetchFailure)
    assert result.reason == reason


async def test__fetch__same_site_twice__min_delay_respected_other_site_not_delayed():
    site = Site({'a.example/1': ok(), 'a.example/2': ok(), 'b.example/1': ok()})
    fetcher, http, time = make(site, respect_robots=False, min_delay_s=5.0)
    async with http:
        await fetcher.fetch('https://a.example/1')
        await fetcher.fetch('https://b.example/1')
        assert time.sleeps == []
        await fetcher.fetch('https://a.example/2')
    assert time.sleeps == [5.0]


async def test__fetch__robots_crawl_delay__honoured_and_capped():
    rules = 'User-agent: *\nCrawl-delay: 12\n\nUser-agent: autostudio-prices\nCrawl-delay: 9\n'
    site = Site({'a.example/robots.txt': httpx.Response(200, text=rules), 'a.example/1': ok(), 'a.example/2': ok()})
    fetcher, http, time = make(site, min_delay_s=5.0)
    async with http:
        await fetcher.fetch('https://a.example/1')
        await fetcher.fetch('https://a.example/2')
    assert time.sleeps == [9.0, 9.0]  # правило для нашего агента важнее общего, и паузы — между всеми запросами

    slow = Site(
        {'b.example/robots.txt': httpx.Response(200, text='User-agent: *\nCrawl-delay: 3600\n'), 'b.example/1': ok()}
    )
    fetcher, http, time = make(slow, min_delay_s=5.0)
    async with http:
        await fetcher.fetch('https://b.example/1')
        await fetcher.fetch('https://b.example/1')
    assert max(time.sleeps) == 60.0


def test__decode_html__charsets():
    assert decode_html('Цена'.encode('cp1251'), 'windows-1251') == 'Цена'
    meta = '<meta charset="windows-1251"><p>Цена</p>'.encode('cp1251')
    assert decode_html(meta, None).endswith('Цена</p>')
    assert decode_html(b'\xef\xbb\xbf<p>x</p>', 'latin-1') == '<p>x</p>'
    assert decode_html('₾'.encode(), 'no-such-codec') == '₾'


def test__fetch_settings__from_section__user_agent_rendered():
    section = PricesSection(products=[Product(id='p', name='P')], contact_email='ops@studio.example', min_delay_s=7)
    settings = FetchSettings.from_section(section, version='9.9')
    assert settings.user_agent == 'Mozilla/5.0 (compatible; autostudio-prices/9.9; +mailto:ops@studio.example)'
    assert settings.min_delay_s == 7


async def test__system_resolver__localhost__loopback_addresses():
    addresses = await system_resolver('localhost', 80)
    assert addresses
    assert all(a.startswith(('127.', '::1')) for a in addresses)


# --- ловушки ревью ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('rules', 'path', 'allowed'),
    [
        # urllib.robotparser в Python 3.12 брал первое совпавшее правило и не знал «*» и «$».
        ('User-agent: *\nAllow: /\nDisallow: /private/\n', '/private/x', False),
        ('User-agent: *\nDisallow: /*/private/\n', '/ru/private/x', False),
        ('User-agent: *\nDisallow: /*.html$\n', '/old/airpods.html', False),
        ('User-agent: *\nDisallow: /*?*variant=\n', '/products/x?color=1&variant=2', False),
        ('User-agent: *\nDisallow: /\nAllow: /products/\n', '/products/x', True),
        ('User-agent: *\nDisallow: /*.html$\n', '/old/airpods.html?x=1', True),
    ],
)
async def test__fetch__robots_rfc9309_wildcards_and_longest_match__same_on_every_python(rules, path, allowed):
    site = Site({'a.example/robots.txt': httpx.Response(200, text=rules), f'a.example{path.split("?")[0]}': ok()})
    fetcher, http, _ = make(site, min_delay_s=1.0)
    async with http:
        result = await fetcher.fetch(f'https://a.example{path}')
    assert isinstance(result, FetchResult) is allowed, result


async def test__fetch__crawl_delay_garbage__ignored_no_crash():
    rules = 'User-agent: *\nCrawl-delay: 1' + '0' * 400 + '\nDisallow: /x\n'
    site = Site({'a.example/robots.txt': httpx.Response(200, text=rules), 'a.example/p': ok()})
    fetcher, http, time = make(site, min_delay_s=1.0)
    async with http:
        assert isinstance(await fetcher.fetch('https://a.example/p'), FetchResult)
        assert isinstance(await fetcher.fetch('https://a.example/p'), FetchResult)
    assert time.sleeps == [1.0, 1.0]  # robots.txt, страница, страница — паузы только min_delay_s


async def test__fetch__idn_host__resolver_gets_punycode_httpx_connects_to():
    # Python-резолвер кодирует «faß» по IDNA 2003 («fass»), httpx подключается к IDNA 2008 («xn--fa-hia»).
    asked: list[str] = []

    async def resolve(host: str, port: int) -> list[str]:
        asked.append(host)
        return [PUBLIC]

    site = Site({'faß.example/p': ok()})  # httpx.URL.host — снова Unicode-имя
    settings = FetchSettings(user_agent='bot (+mailto:ops@studio.example)', respect_robots=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
        fetcher = PoliteFetcher(http, settings, sleep=FakeTime().sleep, resolver=resolve)
        assert isinstance(await fetcher.fetch('https://faß.example/p'), FetchResult)
    assert asked == ['xn--fa-hia.example']


class Drip(httpx.AsyncByteStream):
    """Сервер-«тарпит»: отдаёт страницу по байту, каждый — быстрее таймаута чтения."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(1000):
            await asyncio.sleep(0.01)
            yield b'x'


async def test__fetch__tarpit_drip__whole_attempt_deadline_timeout():
    site = Site({'a.example/p': httpx.Response(200, headers=HTML, stream=Drip())})
    fetcher, http, _ = make(site, respect_robots=False, max_retries=0, attempt_timeout_s=0.1)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'timeout'
    assert '0.1' in result.detail


def test__fetch_settings__attempt_deadline__defaults_to_three_read_timeouts():
    assert FetchSettings(user_agent='x', timeout_s=20).attempt_deadline_s == 60
    assert FetchSettings(user_agent='x', attempt_timeout_s=5).attempt_deadline_s == 5


@pytest.mark.parametrize('value', [b'\xb2', b'9' * 400])
async def test__fetch__odd_retry_after__no_crash(value):
    # h11 пропускает байт 0xB2 в заголовке; httpx читает его как «²», а «²».isdigit() — True.
    replies = iter([httpx.Response(429, headers=[(b'retry-after', value)]), ok()])
    site = Site({'a.example/p': lambda r: next(replies)})
    fetcher, http, time = make(site, respect_robots=False, min_delay_s=1.0)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    if value == b'\xb2':
        assert isinstance(result, FetchResult)  # мусор вместо числа — обычная пауза с backoff
        assert time.sleeps[0] == 2.0
    else:
        assert isinstance(result, FetchFailure)  # «очень долго» — до следующего запуска
        assert result.reason == 'rate_limited'


@pytest.mark.parametrize('charset', ['hex', 'rot13', 'base64', 'idna'])
async def test__fetch__non_text_charset__decoded_as_utf8(charset):
    page = httpx.Response(200, headers={'content-type': f'text/html; charset={charset}'}, content='₾'.encode())
    site = Site({'a.example/p': page})
    fetcher, http, _ = make(site, respect_robots=False)
    async with http:
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchResult)
    assert result.html == '₾'
    assert decode_html('₾'.encode(), charset) == '₾'


async def test__fetch__ipv6_literal__robots_read_with_brackets():
    v6 = '2606:4700:4700::1111'
    site = Site({f'{v6}/robots.txt': httpx.Response(200, text='User-agent: *\nDisallow: /\n'), f'{v6}/p': ok()})
    fetcher, http, _ = make(site)
    async with http:
        result = await fetcher.fetch(f'https://[{v6}]/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'robots_disallowed'
    assert site.paths() == [f'{v6}/robots.txt']


async def test__fetch__robots_address_fails_its_own_check__site_skipped_not_allowed():
    calls = 0

    async def flaky(host: str, port: int) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 2:  # имя страницы только что резолвилось, а robots.txt того же сайта — нет
            raise OSError('temporary failure')
        return [PUBLIC]

    site = Site({'a.example/p': ok()})
    settings = FetchSettings(user_agent='bot (+mailto:ops@studio.example)')
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
        fetcher = PoliteFetcher(http, settings, sleep=FakeTime().sleep, resolver=flaky)
        result = await fetcher.fetch('https://a.example/p')
    assert isinstance(result, FetchFailure)
    assert result.reason == 'robots_disallowed'
    assert site.requests == []

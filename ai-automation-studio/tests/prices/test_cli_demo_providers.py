import csv
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from dishka import make_async_container

from autostudio.clientconf import ClientInfo, NotifyConfig, SinkConfig, load_client_config
from autostudio.clientconf import ClientInfo as Client
from autostudio.errors import AutoStudioError, ConfigError
from autostudio.events import EventLog
from autostudio.llm import FakeLLM, ResilientLLM
from autostudio.prices import cli, demo
from autostudio.prices.config import Offer, PricesConfig, PricesSection, Product
from autostudio.prices.extract import PriceGuess
from autostudio.prices.fetch import PoliteFetcher
from autostudio.prices.heuristics import guess_price
from autostudio.prices.monitor import PricesDeps, RunResult
from autostudio.prices.providers import PricesProvider, price_model
from autostudio.prices.store import PriceStore
from autostudio.providers import CoreProvider
from autostudio.settings import LLMProvider, Settings

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'prices' / 'client.toml'
IP = '93.184.215.14'
PAGE = (
    '<html><head><script type="application/ld+json">'
    '{"@type":"Product","offers":{"price":"95.00","priceCurrency":"GEL","availability":"InStock"}}'
    '</script></head><body><span class="price-now">95,00 ₾</span></body></html>'
)

ENV_VARS = (
    'AUTOSTUDIO_LLM_PROVIDER',
    'AUTOSTUDIO_LLM_MODEL',
    'AUTOSTUDIO_HEALTHCHECK_URL',
    'AUTOSTUDIO_OPERATOR_CHAT_ID',
    'OPENAI_API_KEY',
    'ANTHROPIC_API_KEY',
    'GOOGLE_APPLICATION_CREDENTIALS',
    'AUTOSTUDIO_NOTIFY_BOT_TOKEN',
    'AUTOSTUDIO_PRICES_CONTACT_EMAIL',
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AUTOSTUDIO_DATA_DIR', str(tmp_path / 'data'))
    return monkeypatch


def invoke(*tokens: str) -> object:
    return cli.app(list(tokens), result_action='return_value', exit_on_error=False)


CLIENT = """
[client]
id = "acme-shop"
name = "Acme"

[notify]
console = true

[sink]
csv_dir = "{out}"

[prices]
contact_email = "ops@studio.example"
respect_robots = false
{extra}

[[prices.products]]
id = "p1"
name = "Phone"
own_price = "100.00"

  [[prices.products.offers]]
  competitor = "Shop A"
  url = "http://{ip}/p/1"
"""


def client_toml(tmp_path: Path, extra: str = '', out: Path | None = None) -> Path:
    path = tmp_path / 'client.toml'
    path.write_text(CLIENT.format(out=out or tmp_path / 'out', extra=extra, ip=IP), encoding='utf-8')
    return path


# --- check ---------------------------------------------------------------------------------


def test__check__example_config__table_readiness_notes_not_ok(clean_env, capsys):
    invoke('check', str(EXAMPLE))
    out = capsys.readouterr().out
    assert 'iphone-15-128' in out
    assert 'selector .price-now' in out
    assert '3 товаров, 9 ссылок, клиент demo-prices' in out
    assert 'llm_fallback' not in out  # в образце ступень модели выключена (Premium)
    assert '! sink.backend = "csv"' in out
    assert '! prices.contact_email = monitor@autostudio.example: адрес из образца' in out
    assert '! ссылок на демо-доменах (*.example и т.п.): 9' in out
    # Образец читается, но к боевому запуску не готов — и check говорит это прямо.
    assert '✓ конфиг и окружение читаются; к боевому запуску замечаний: 3' in out
    assert 'конфиг и окружение в порядке' not in out


def test__check__example_strict__not_ready_error(clean_env, capsys):
    with pytest.raises(ConfigError, match='не готово к боевому запуску: 3'):
        invoke('check', str(EXAMPLE), '--strict')
    assert '✗ sink.backend = "csv"' in capsys.readouterr().out


def test__check__llm_fallback_with_fake_provider__problem_not_note(clean_env, tmp_path, capsys):
    with pytest.raises(ConfigError, match='не готово'):
        invoke('check', str(client_toml(tmp_path, extra='llm_fallback = true')))
    assert '✗ llm_fallback включён, но AUTOSTUDIO_LLM_PROVIDER=fake' in capsys.readouterr().out


def test__check__ready_client__all_ok(clean_env, tmp_path, capsys):
    creds = tmp_path / 'sa.json'
    creds.write_text('{}')
    clean_env.setenv('GOOGLE_APPLICATION_CREDENTIALS', str(creds))
    path = client_toml(tmp_path)
    text = path.read_text().replace(f'csv_dir = "{tmp_path / "out"}"', 'backend = "google"\nspreadsheet_id = "X"')
    path.write_text(text.replace('ops@studio.example', 'ops@studio.ge'))  # настоящий домен, а не из образца
    invoke('check', str(path), '--strict')
    assert '✓ конфиг и окружение в порядке' in capsys.readouterr().out


def test__readiness_report__empty_product_no_own_price_unknown_language():
    config = PricesConfig(
        client=Client(id='acme-shop', name='Acme', language='ka'),
        sink=SinkConfig(backend='google', spreadsheet_id='X'),
        prices=PricesSection(
            products=[
                Product(id='p1', name='P'),
                Product(id='p2', name='Q', offers=[Offer(competitor='A', url=f'http://{IP}/p')]),
            ],
            contact_email='ops@studio.ge',
        ),
    )
    report = cli.readiness_report(config)
    assert report == [
        'product p1: нет ни одной ссылки конкурента — товар не проверяется',
        'product p1: нет own_price — сравнения «дешевле вас» не будет',
        'product p2: нет own_price — сравнения «дешевле вас» не будет',
        'client.language = "ka": перевода нет, таблица и сообщения будут на английском',
    ]


def test__check__google_without_credentials__config_error(clean_env, tmp_path, capsys):
    path = client_toml(tmp_path)
    text = path.read_text().replace(f'csv_dir = "{tmp_path / "out"}"', 'backend = "google"\nspreadsheet_id = "X"')
    path.write_text(text)
    with pytest.raises(ConfigError, match='не готово к боевому запуску'):
        invoke('check', str(path))
    assert '✗ GOOGLE_APPLICATION_CREDENTIALS не задан' in capsys.readouterr().out


def _config(**notify) -> PricesConfig:
    return PricesConfig(
        client=ClientInfo(id='acme-shop', name='Acme'),
        notify=NotifyConfig(**notify),
        sink=SinkConfig(backend='google', spreadsheet_id='X'),
        prices=PricesSection(
            products=[Product(id='p', name='P')], contact_email='ops@studio.example', respect_robots=False
        ),
    )


def test__environment_report__all_problems_and_notes(tmp_path):
    config = _config(telegram_chat_id='1', webhook_url_env='HOOK')
    problems, notes = cli.environment_report(
        config,
        {'AUTOSTUDIO_LLM_PROVIDER': 'openai', 'GOOGLE_APPLICATION_CREDENTIALS': str(tmp_path / 'missing.json')},
    )
    assert any('OPENAI_API_KEY' in p for p in problems)
    assert any('файла нет' in p for p in problems)
    assert any('AUTOSTUDIO_NOTIFY_BOT_TOKEN' in p for p in problems)
    assert any('HOOK' in p for p in problems)
    assert any('respect_robots = false' in n for n in notes)


def test__environment_report__ready__no_problems(tmp_path):
    creds = tmp_path / 'sa.json'
    creds.write_text('{}')
    environ = {'GOOGLE_APPLICATION_CREDENTIALS': str(creds), 'AUTOSTUDIO_NOTIFY_BOT_TOKEN': 't'}
    problems, notes = cli.environment_report(_config(telegram_chat_id='1'), environ)
    assert problems == []
    assert all('консоль' not in n for n in notes)


# --- run -----------------------------------------------------------------------------------


@respx.mock
def test__run__one_pass__writes_sheet_events_and_prints_summary(clean_env, tmp_path, capsys):
    respx.get(f'http://{IP}/p/1').mock(
        return_value=httpx.Response(200, headers={'content-type': 'text/html'}, text=PAGE)
    )
    invoke('run', str(client_toml(tmp_path)))
    out = capsys.readouterr().out
    assert 'prices: прочитано 1, не прочитано 0, событий 1, страниц модели 0' in out
    assert '📈 Competitor prices — Acme' in out
    with (tmp_path / 'out' / 'Prices_now.csv').open(encoding='utf-8') as fh:
        rows = list(csv.reader(fh))
    assert rows[1][3] == '95.00'
    assert (tmp_path / 'data' / 'acme-shop' / 'events.sqlite').exists()
    assert (tmp_path / 'data' / 'acme-shop' / 'prices.sqlite').exists()


def test__run__sink_broken__run_failed_error(clean_env, tmp_path, capsys):
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    path = client_toml(tmp_path, out=blocker / 'sub')
    with respx.mock:
        respx.get(f'http://{IP}/p/1').mock(
            return_value=httpx.Response(200, headers={'content-type': 'text/html'}, text=PAGE)
        )
        with pytest.raises(AutoStudioError, match='проход монитора завершился ошибкой'):
            invoke('run', str(path))


@pytest.mark.parametrize('hours', ['0.5', '200'])
def test__run__every_hours_out_of_range__config_error(clean_env, tmp_path, hours):
    with pytest.raises(ConfigError, match='every-hours'):
        invoke('run', str(client_toml(tmp_path)), '--every-hours', hours)


@respx.mock
async def test__serve__loop__runs_cycles_with_pause(clean_env, tmp_path):
    respx.get(f'http://{IP}/p/1').mock(
        return_value=httpx.Response(200, headers={'content-type': 'text/html'}, text=PAGE)
    )
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    ok = await cli.serve(client_toml(tmp_path), every_hours=6, sleep=sleep, max_cycles=2)
    assert ok is True
    assert pauses == [6 * 3600.0]


def test__summary__failures_listed():
    result = RunResult(
        checked=1, failed=2, changes=(), digest=None, operator_alert=None, llm_pages=1, failures={'timeout': 2}
    )
    assert cli.summary(result) == 'prices: прочитано 1, не прочитано 2 (timeout 2), событий 0, страниц модели 1'


def test__summary__model_tokens_and_paused_offers():
    used = RunResult(
        checked=1,
        failed=0,
        changes=(),
        digest=None,
        operator_alert=None,
        llm_pages=2,
        llm_input_tokens=3000,
        llm_output_tokens=40,
        paused=3,
    )
    assert cli.summary(used) == (
        'prices: прочитано 1, не прочитано 0, на паузе 3, событий 0, страниц модели 2, '
        'токенов модели 3000 вх. / 40 вых.'
    )


# --- probe ---------------------------------------------------------------------------------


async def resolve_public(host: str, port: int) -> list[str]:
    return [IP]


def site(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(request.url.path, httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


SECTION = PricesSection(
    products=[Product(id='p', name='P')], contact_email='ops@studio.example', respect_robots=False, min_delay_s=1
)


async def test__probe_report__every_rung_and_toml_snippet():
    offer = Offer(competitor='probe', url='https://shop.example/p', selector='.price-now', currency='GEL')
    async with site({'/p': httpx.Response(200, headers={'content-type': 'text/html'}, text=PAGE)}) as http:
        report = await cli.probe_report(
            'https://shop.example/p', section=SECTION, offer=offer, http=http, resolver=resolve_public
        )
    assert '✓ загрузка: HTTP 200' in report
    assert "selector   95.00 GEL ← '95,00 ₾' [in_stock]" in report
    assert "json_ld    95.00 GEL ← 'price=95.00 priceCurrency=GEL' [in_stock]" in report
    assert 'microdata  — (nothing)' in report
    assert 'llm        — (не спрашивали: добавьте --llm)' in report
    assert 'Итог бесплатного каскада: 95.00 GEL' in report
    assert 'span.price-now' in report
    assert 'selector = ".price-now"' in report
    assert 'currency = "GEL"' in report


async def test__probe_report__plain_page_with_llm__suggestion_and_verified_quote():
    page = '<html><body><div class="cost-box">Цена: 729 ₾</div></body></html>'
    offer = Offer(competitor='probe', url='https://shop.example/p', selector='.price', attr='data-price')
    async with site({'/p': httpx.Response(200, headers={'content-type': 'text/html'}, text=page)}) as http:
        report = await cli.probe_report(
            'https://shop.example/p',
            section=SECTION,
            offer=offer,
            http=http,
            resolver=resolve_public,
            use_llm=True,
            settings=Settings(),
        )
    assert 'selector   — (selector_not_found)' in report
    assert "llm        729 GEL ← '729 ₾'" in report
    assert 'эвристика fake — не ИИ, в боевой запуск не попадает' in report
    assert 'Итог бесплатного каскада: ✗ selector_not_found' in report
    assert 'div.cost-box' in report
    assert 'attr = "data-price"' in report


async def test__probe_report__from_price_and_broken_selector__flags_in_verdict():
    page = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Product","offers":{"@type":"AggregateOffer","lowPrice":"10","priceCurrency":"EUR"}}'
        '</script></head><body><p>no structure here</p></body></html>'
    )
    offer = Offer(competitor='probe', url='https://shop.example/p', selector='.gone')
    async with site({'/p': httpx.Response(200, headers={'content-type': 'text/html'}, text=page)}) as http:
        report = await cli.probe_report(
            'https://shop.example/p', section=SECTION, offer=offer, http=http, resolver=resolve_public
        )
    assert 'Итог бесплатного каскада: 10 EUR' in report
    assert '[from; warnings: selector_not_found]' in report
    assert 'Кандидаты в селекторы' not in report


async def test__probe_report__two_current_looking_prices__ambiguity_warning():
    page = '<html><body><b class="price">1 299 ₾</b><i class="cost">999 ₾</i></body></html>'
    offer = Offer(competitor='probe', url='https://shop.example/p')
    async with site({'/p': httpx.Response(200, headers={'content-type': 'text/html'}, text=page)}) as http:
        report = await cli.probe_report(
            'https://shop.example/p', section=SECTION, offer=offer, http=http, resolver=resolve_public
        )
    assert 'selector = "i.cost"' in report  # обе «цены», короче — выше; оператор сверяет глазами
    assert 'несколько разных цен' in report


async def test__probe_report__fetch_failure__reason_shown():
    offer = Offer(competitor='probe', url='https://shop.example/gone')
    async with site({}) as http:
        report = await cli.probe_report(
            'https://shop.example/gone', section=SECTION, offer=offer, http=http, resolver=resolve_public
        )
    assert '✗ загрузка: http_error HTTP 404' in report


async def test__probe_report__unparseable_selector_suggestion_offered():
    page = '<html><body><b class="price-tag">1 299 ₾</b></body></html>'
    offer = Offer(competitor='probe', url='https://shop.example/p')
    async with site({'/p': httpx.Response(200, headers={'content-type': 'text/html'}, text=page)}) as http:
        report = await cli.probe_report(
            'https://shop.example/p', section=SECTION, offer=offer, http=http, resolver=resolve_public
        )
    assert 'selector = "b.price-tag"' in report
    assert '(валюта не найдена)' not in report


async def test__probe_report__real_provider__labelled_model_without_fallback(monkeypatch):
    built: list[object] = []

    class Model(FakeLLM):
        @property
        def model(self) -> str:
            return 'gpt-test'

    def fake_make_llm(settings: Settings, fake: FakeLLM | None = None, on_fallback: object = None) -> FakeLLM:
        built.append(fake)
        return Model({PriceGuess: guess_price})

    monkeypatch.setattr(cli, 'make_llm', fake_make_llm)
    page = '<html><body><div class="cost-box">Цена: 729 ₾</div></body></html>'
    offer = Offer(competitor='probe', url='https://shop.example/p')
    async with site({'/p': httpx.Response(200, headers={'content-type': 'text/html'}, text=page)}) as http:
        report = await cli.probe_report(
            'https://shop.example/p',
            section=SECTION,
            offer=offer,
            http=http,
            resolver=resolve_public,
            use_llm=True,
            settings=Settings(llm_provider=LLMProvider.OPENAI, llm_model='gpt-test'),
        )
    assert '(модель gpt-test' in report
    assert built == [None]  # без запасной эвристики: сбой ключа не выдаёт себя за ответ модели


def test__probe__no_contact_email__config_error(clean_env):
    with pytest.raises(ConfigError, match='contact-email'):
        invoke('probe', f'http://{IP}/p')


@respx.mock
def test__probe__env_email_and_robots_disallow__reported(clean_env, capsys):
    clean_env.setenv('AUTOSTUDIO_PRICES_CONTACT_EMAIL', 'ops@studio.example')
    robots = respx.get(f'http://{IP}/robots.txt').mock(
        return_value=httpx.Response(200, text='User-agent: *\nDisallow: /')
    )
    invoke('probe', f'http://{IP}/p')
    assert robots.calls[0].request.headers['user-agent'].endswith('+mailto:ops@studio.example)')
    assert '✗ загрузка: robots_disallowed robots.txt' in capsys.readouterr().out


@respx.mock
def test__probe__with_config__uses_client_politeness(clean_env, tmp_path, capsys):
    respx.get(f'http://{IP}/p/1').mock(
        return_value=httpx.Response(200, headers={'content-type': 'text/html'}, text=PAGE)
    )
    invoke('probe', f'http://{IP}/p/1', '--config', str(client_toml(tmp_path)), '--selector', '.price-now')
    assert "selector   95.00 GEL ← '95,00 ₾'" in capsys.readouterr().out


def test__probe__unsafe_url__config_error(clean_env):
    with pytest.raises(ConfigError, match='не публичный'):
        invoke('probe', 'http://127.0.0.1/admin', '--contact-email', 'ops@studio.example')


# --- demo ----------------------------------------------------------------------------------


async def test__run_demo__two_days__cascade_alerts_and_tabs(tmp_path, capsys):
    summary = await demo.run_demo(tmp_path)
    printed = capsys.readouterr().out
    assert 'Day 1 (2026-10-20 10:00): 7 of 9 prices read [json_ld 2, llm 1, meta 1, microdata 1, selector 2]' in summary
    assert 'not read: http_error 1, robots_disallowed 1' in summary
    assert 'alerts: price 3, stock 1, undercut 2; operator: alerted' in summary
    assert 'Model calls: 2' in summary
    assert 'Report highlight: Price changes caught: 3' in summary
    assert 'skipped: the site asks robots not to visit this page' in summary  # колонка статуса не обрезана
    assert '[operator channel, internal — the client does not see this]' in printed
    assert 'Successful runs: 2 of 2 (100.0%)' in summary
    assert (tmp_path / 'report.md').read_text(encoding='utf-8').startswith('# Automation report: Tech Corner Tbilisi')
    assert '• iPhone 15 128GB — Shop A: 2,749 → 2,599 GEL (−5.5%)' in printed
    assert '• iPhone 15 128GB — Shop B: out of stock' in printed
    assert "селектор '.price-now' не находит цену" in printed
    for name in ('Prices_now.csv', 'History.csv', 'Changes.csv'):
        assert (tmp_path / name).exists()


async def test__run_demo__twice__deterministic_no_duplicates(tmp_path, capsys):
    first = await demo.run_demo(tmp_path)
    history = (tmp_path / 'History.csv').read_text(encoding='utf-8')
    second = await demo.run_demo(tmp_path)
    assert first == second
    assert (tmp_path / 'History.csv').read_text(encoding='utf-8') == history


async def test__run_demo__no_example__config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, 'EXAMPLE', tmp_path / 'missing.toml')
    with pytest.raises(ConfigError, match='нет образца'):
        await demo.run_demo(tmp_path)


async def test__demo_resolver__only_example_domains():
    assert await demo.demo_resolver('shop-a.example', 443) == ['93.184.215.14']
    with pytest.raises(OSError):
        await demo.demo_resolver('google.com', 443)


def test__demo_command__prints_summary(tmp_path, capsys):
    invoke('demo', '--out', str(tmp_path))
    assert 'Competitor price monitor — offline demo' in capsys.readouterr().out


def test__demo_web__unknown_page__404():
    web = demo.DemoWeb()
    response = web(httpx.Request('GET', 'https://shop-a.example/nope'))
    assert response.status_code == 404


# --- provider ------------------------------------------------------------------------------


async def test__prices_provider__builds_deps_under_client_data_dir(tmp_path):
    config = load_client_config(EXAMPLE, PricesConfig)
    settings = Settings(data_dir=tmp_path)
    container = make_async_container(
        CoreProvider(), PricesProvider(resolver=resolve_public), context={Settings: settings, PricesConfig: config}
    )
    deps = await container.get(PricesDeps)
    assert deps.llm is None  # в образце ступень модели выключена
    assert isinstance(deps.new_fetcher(), PoliteFetcher)
    assert deps.new_fetcher() is not deps.new_fetcher()  # кэш robots.txt — на один запуск
    assert (tmp_path / 'demo-prices' / 'prices.sqlite').exists()
    assert (tmp_path / 'demo-prices' / 'events.sqlite').exists()
    await container.close()


async def test__prices_provider__fallback_off__no_model(tmp_path):
    config = load_client_config(EXAMPLE, PricesConfig)
    config = PricesConfig(
        client=config.client, prices=PricesSection(products=config.prices.products, contact_email='a@b.example')
    )
    container = make_async_container(
        CoreProvider(), PricesProvider(), context={Settings: Settings(data_dir=tmp_path), PricesConfig: config}
    )
    assert (await container.get(PricesDeps)).llm is None
    await container.close()


def test__registry__points_to_this_package():
    from autostudio.registry import PACKAGES, demo_for, highlights_for, metrics_for

    info = PACKAGES['prices']
    assert metrics_for(info)[0].kind == 'prices.checked'
    assert highlights_for(info) is not None
    assert demo_for(info) is demo.run_demo
    assert (
        load_client_config(Path(__file__).resolve().parents[2] / info.example, PricesConfig).client.id == 'demo-prices'
    )


def test__decimal_in_example__no_float_drift():
    config = load_client_config(EXAMPLE, PricesConfig)
    assert config.prices.products[0].own_price == Decimal('2699.00')
    assert str(config.prices.products[0].own_price) == '2699.00'  # строка, а не двоичная дробь
    assert config.prices.alert_change_pct == Decimal(3)
    assert all(isinstance(p.own_price, Decimal) for p in config.prices.products)


def _with_fallback(config: PricesConfig) -> PricesConfig:
    import msgspec

    return msgspec.structs.replace(config, prices=msgspec.structs.replace(config.prices, llm_fallback=True))


def test__price_model__fallback_with_fake_provider__no_heuristic_in_production():
    # Эвристика «первое число с валютой» берёт порог доставки — в таблицу клиента она не попадает.
    config = _with_fallback(load_client_config(EXAMPLE, PricesConfig))
    assert price_model(Settings(), config) is None
    assert price_model(Settings(), load_client_config(EXAMPLE, PricesConfig)) is None


async def test__price_model__real_provider__no_silent_heuristic_fallback(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    config = _with_fallback(load_client_config(EXAMPLE, PricesConfig))
    llm = price_model(Settings(llm_provider=LLMProvider.OPENAI, llm_model='gpt-5.4-mini'), config)
    assert llm is not None
    assert not isinstance(llm, ResilientLLM | FakeLLM)
    assert llm.model == 'gpt-5.4-mini'
    await llm.aclose()


async def test__prices_provider__real_model__closed_with_container(tmp_path, monkeypatch):
    closed: list[bool] = []

    class Model(FakeLLM):
        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr('autostudio.prices.providers.make_llm', lambda settings: Model())
    config = _with_fallback(load_client_config(EXAMPLE, PricesConfig))
    settings = Settings(data_dir=tmp_path, llm_provider=LLMProvider.OPENAI)
    container = make_async_container(
        CoreProvider(), PricesProvider(resolver=resolve_public), context={Settings: settings, PricesConfig: config}
    )
    assert isinstance((await container.get(PricesDeps)).llm, Model)
    await container.close()
    assert closed == [True]


# --- ёмкость, пауза, .env клиента, пропуск прохода -----------------------------------------------


def big_catalog_toml(tmp_path: Path, offers: int, extra: str = '', host: str = 'big.example') -> Path:
    products = '\n'.join(
        f'[[prices.products]]\nid = "p{i}"\nname = "P{i}"\n\n  [[prices.products.offers]]\n'
        f'  competitor = "Big"\n  url = "https://{host}/p/{i}"\n'
        for i in range(offers)
    )
    text = (
        f'[client]\nid = "acme-shop"\nname = "Acme"\n\n[notify]\nconsole = true\n\n[sink]\n'
        f'csv_dir = "{tmp_path / "out"}"\n\n[prices]\ncontact_email = "ops@studio.example"\n{extra}\n\n{products}'
    )
    path = tmp_path / 'client.toml'
    path.write_text(text, encoding='utf-8')
    return path


def test__check__example__site_load_and_schedule_hint(clean_env, capsys):
    invoke('check', str(EXAMPLE))
    out = capsys.readouterr().out
    assert 'Нагрузка на сайты (пауза 5 с между запросами, плюс robots.txt; без Crawl-delay):' in out
    assert 'shop-a.example                   ссылок 3, запросов за проход 4, ≈ 20 с, до 4 запросов/ч' in out
    assert (
        'Весь проход ≈ 20 с (сайтов одновременно до 4); без наложений — не чаще раза в 1 ч; '
        'расписание не задано (check --every-hours N сверит его)'
    ) in out


def test__check__whole_catalog_hourly__capacity_note_strict_problem(clean_env, tmp_path, capsys):
    path = big_catalog_toml(tmp_path, 800)
    invoke('check', str(path), '--every-hours', '1')
    out = capsys.readouterr().out
    assert 'расписание — раз в 1 ч' in out
    assert '! big.example: проход по сайту ≈ 67 мин — не уложится в max_run_minutes = 30' in out
    assert '! big.example: проход по сайту ≈ 67 мин — дольше интервала 1 ч' in out
    with pytest.raises(ConfigError, match='не готово к боевому запуску'):
        invoke('check', str(path), '--every-hours', '1', '--strict')
    assert '✗ big.example: проход по сайту' in capsys.readouterr().out


def test__check__many_sites__top_ten_shown(clean_env, tmp_path, capsys):
    path = client_toml(tmp_path)
    offers = ''.join(
        f'\n  [[prices.products.offers]]\n  competitor = "S{i}"\n  url = "https://s{i}.example/p"\n' for i in range(11)
    )
    path.write_text(path.read_text() + offers)
    invoke('check', str(path))
    assert '… и ещё сайтов: 2' in capsys.readouterr().out


def test__check__every_hours_out_of_range__config_error(clean_env):
    with pytest.raises(ConfigError, match='every-hours'):
        invoke('check', str(EXAMPLE), '--every-hours', '0.5')


def test__check__paused_site__marked_in_table_and_noted_not_a_problem(clean_env, tmp_path, capsys):
    extra = '[prices.sites."big.example"]\npaused = true\npaused_reason = "письмо владельца 2026-10-21"'
    path = big_catalog_toml(tmp_path, 2, extra=extra)
    invoke('check', str(path))
    out = capsys.readouterr().out
    assert 'PAUSED (auto)' in out
    assert '! сайт big.example на паузе (письмо владельца 2026-10-21): ссылок не проверяется: 2' in out
    no_reason = big_catalog_toml(tmp_path, 1, extra='[prices.sites."big.example"]\npaused = true')
    invoke('check', str(no_reason))
    assert '! сайт big.example на паузе (причина не указана): ссылок не проверяется: 1' in capsys.readouterr().out


def test__check__client_env_next_to_config__picked_up(clean_env, tmp_path, capsys):
    creds = tmp_path / 'sa.json'
    creds.write_text('{}')
    # Переменная, которую загрузит .env, вернётся в исходное состояние после теста.
    clean_env.setenv('GOOGLE_APPLICATION_CREDENTIALS', 'placeholder')
    clean_env.delenv('GOOGLE_APPLICATION_CREDENTIALS')
    (tmp_path / '.env').write_text(f'GOOGLE_APPLICATION_CREDENTIALS={creds}\n')
    path = client_toml(tmp_path)
    text = path.read_text().replace(f'csv_dir = "{tmp_path / "out"}"', 'backend = "google"\nspreadsheet_id = "X"')
    path.write_text(text.replace('ops@studio.example', 'ops@studio.ge'))
    invoke('check', str(path), '--strict')
    assert '✓ конфиг и окружение в порядке' in capsys.readouterr().out


@respx.mock
def test__run__client_env_next_to_config__loaded_before_settings(clean_env, tmp_path, capsys):
    clean_env.setenv('AUTOSTUDIO_HEALTHCHECK_URL', 'placeholder')
    clean_env.delenv('AUTOSTUDIO_HEALTHCHECK_URL')
    (tmp_path / '.env').write_text('AUTOSTUDIO_HEALTHCHECK_URL=https://hc.example/ping/abc\n')
    start = respx.post('https://hc.example/ping/abc/start').mock(return_value=httpx.Response(200))
    done = respx.post('https://hc.example/ping/abc').mock(return_value=httpx.Response(200))
    respx.get(f'http://{IP}/p/1').mock(
        return_value=httpx.Response(200, headers={'content-type': 'text/html'}, text=PAGE)
    )
    invoke('run', str(client_toml(tmp_path)))
    assert start.called and done.called


async def test__serve__another_pass_holds_lock__skipped_not_failed(clean_env, tmp_path, capsys):
    path = client_toml(tmp_path)
    store = PriceStore(tmp_path / 'data' / 'acme-shop' / 'prices.sqlite')
    with store.exclusive() as locked:
        assert locked
        ok = await cli.serve(path, every_hours=None)
    assert ok is True  # сознательный пропуск — не ошибка для cron/systemd
    assert cli.SKIPPED in capsys.readouterr().out
    events = EventLog(tmp_path / 'data' / 'acme-shop' / 'events.sqlite')
    assert events.last('prices', ('system.run_skipped',)) is not None
    assert events.last('prices', ('system.run_ok', 'system.run_failed')) is None


async def test__serve__overloaded_site__capacity_warning_logged_pass_still_runs(clean_env, tmp_path, caplog):
    async def no_dns(host: str, port: int) -> list[str]:
        raise OSError('нет сети в тесте')

    path = big_catalog_toml(tmp_path, 13, extra='respect_robots = false\nmax_run_minutes = 1')
    with caplog.at_level('WARNING', logger='autostudio.prices.cli'):
        ok = await cli.serve(path, every_hours=None, provider=lambda: PricesProvider(resolver=no_dns))
    assert ok is True
    assert 'ёмкость: big.example: проход по сайту ≈ 65 с — не уложится в max_run_minutes = 1' in caplog.text


def test__probe__paused_site_from_config__no_request(clean_env, tmp_path, capsys):
    path = big_catalog_toml(tmp_path, 1, extra='[prices.sites."big.example"]\npaused = true')
    invoke('probe', 'https://big.example/p/0', '--config', str(path))
    assert '✗ загрузка: paused big.example' in capsys.readouterr().out

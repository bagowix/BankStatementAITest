"""Офлайн-демо монитора цен: три магазина на httpx.MockTransport, два дня, без сети и ключей.

Сценарий показывает весь каскад и все ловушки, ради которых клиент платит поддержку:

- shop-a.example — JSON-LD (`@graph`, массив, цена строкой); одна страница отдаёт 404;
- shop-b.example — микроразметка и meta-теги; одна старая страница без разметки — её
  читает модель (FakeLLM), а код проверяет цитату по тексту страницы;
- shop-c.example — голый HTML, где рядом стоят старая и новая цена: читает только
  селектор оператора; robots.txt закрывает раздел `/private/` — ссылка пропускается;
- день 2: цены падают, конкурент становится дешевле клиента, товар кончается, а Shop C
  меняет вёрстку — селектор ломается, цена берётся из разметки, оператор узнаёт сразу.

Паузы между запросами настоящие по смыслу, но мгновенные по времени (подменённый
sleep), DNS — словарь: домены `.example` резолвятся в публичный адрес и до сети не доходят.
"""

import csv
import datetime as dt
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

import httpx
import msgspec

from autostudio.clientconf import NotifyConfig, SinkConfig, load_client_config
from autostudio.clock import FrozenClock
from autostudio.errors import ConfigError
from autostudio.events import EventLog
from autostudio.notify import ConsoleNotifier
from autostudio.prices import texts
from autostudio.prices.config import PricesConfig
from autostudio.prices.fetch import FetchSettings, PoliteFetcher
from autostudio.prices.heuristics import make_fake_llm
from autostudio.prices.metrics import highlights
from autostudio.prices.monitor import Fetcher, PricesDeps, RunResult, run_once
from autostudio.prices.store import PriceStore
from autostudio.sinks import CsvSink

EXAMPLE: Final = Path(__file__).resolve().parents[3] / 'examples' / 'prices' / 'client.toml'
DAY_ONE: Final = dt.datetime(2026, 10, 20, 6, 0, tzinfo=dt.UTC)
# Публичный адрес (а не 127.0.0.1): проверка SSRF видит обычный сайт, а соединения нет —
# запрос перехватывает MockTransport.
_PUBLIC_IP: Final = '93.184.215.14'
_HTML: Final = {'content-type': 'text/html; charset=utf-8'}

_ROBOTS: Final[dict[str, str]] = {
    'shop-b.example': 'User-agent: *\nAllow: /\n',
    'shop-c.example': 'User-agent: *\nDisallow: /private/\n',
}


def _layout(title: str, body: str, head: str = '') -> str:
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>{head}</head>'
        f'<body><nav>Home · Phones · Audio · Delivery · Contacts</nav>{body}<footer>© shop</footer></body></html>'
    )


def _json_ld(text: str) -> str:
    return f'<script type="application/ld+json">{text}</script>'


def _shop_a_graph(name: str, price: str, stock: str) -> str:
    # Цена числом в @graph — так отдают WordPress/WooCommerce с SEO-плагинами.
    data = (
        '{"@context":"https://schema.org","@graph":[{"@type":"WebPage","name":"' + name + '"},'
        '{"@type":"Product","name":"' + name + '","offers":{"@type":"Offer","price":' + price + ','
        '"priceCurrency":"GEL","availability":"https://schema.org/' + stock + '"}}]}'
    )
    return _layout(name, f'<h1>{name}</h1><p class="p">{price} GEL</p>', _json_ld(data))


def _shop_a_array(name: str, price: str) -> str:
    # Массив верхнего уровня и цена строкой — так отдают Shopify-подобные шаблоны.
    data = (
        '[{"@type":"BreadcrumbList","itemListElement":[]},{"@type":"Product","name":"' + name + '",'
        '"offers":[{"@type":"Offer","price":"' + price + '","priceCurrency":"GEL",'
        '"availability":"http://schema.org/InStock"}]}]'
    )
    return _layout(name, f'<h1>{name}</h1>', _json_ld(data))


def _shop_b_microdata(name: str, price: str, shown: str, stock: str) -> str:
    body = (
        f'<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">{name}</h1>'
        '<div itemprop="offers" itemscope itemtype="https://schema.org/Offer">'
        f'<span itemprop="price" content="{price}">{shown} ₾</span>'
        '<meta itemprop="priceCurrency" content="GEL">'
        f'<link itemprop="availability" href="https://schema.org/{stock}"></div></div>'
    )
    return _layout(name, body)


def _shop_b_meta(name: str, price: str, shown: str) -> str:
    head = f'<meta property="og:price:amount" content="{price}"><meta property="og:price:currency" content="GEL">'
    return _layout(name, f'<h1>{name}</h1><div class="cost">{shown} ₾</div>', head)


def _shop_b_legacy(name: str, shown: str) -> str:
    # Страница из 2012 года: ни разметки, ни классов — только таблица с текстом.
    body = f'<table><tr><td>{name}</td><td>Price: {shown} ₾</td></tr><tr><td>Warranty</td><td>1 year</td></tr></table>'
    return _layout(name, body)


def _shop_c(name: str, old: str, now: str) -> str:
    # Старая цена стоит раньше новой: без селектора оператора любая эвристика возьмёт старую.
    body = (
        f'<div class="card"><h1>{name}</h1><span class="price-old">{old} ₾</span>'
        f'<span class="price-now">{now} ₾</span></div>'
    )
    return _layout(name, body)


def _shop_c_redesign(name: str, price: str, shown: str) -> str:
    data = '{"@type":"Product","name":"' + name + '","offers":{"price":"' + price + '","priceCurrency":"GEL"}}'
    body = f'<div class="pdp"><h1>{name}</h1><div class="product-price">{shown} ₾</div></div>'
    return _layout(name, body, _json_ld(data))


def _pages(day: int) -> dict[str, str]:
    first = day == 1
    return {
        'shop-a.example/p/iphone-15-128': _shop_a_graph(
            'iPhone 15 128GB', '2749.00' if first else '2599.00', 'InStock'
        ),
        'shop-a.example/p/galaxy-s24-256': _shop_a_array('Samsung Galaxy S24 256GB', '2449.00'),
        # shop-a.example/p/airpods-pro-2 — товар снят с продажи: 404 оба дня.
        'shop-b.example/catalog/apple-iphone-15-128gb': _shop_b_microdata(
            'Apple iPhone 15 128GB', '2799.00', '2 799,00', 'InStock' if first else 'OutOfStock'
        ),
        'shop-b.example/catalog/samsung-galaxy-s24-256gb': _shop_b_meta(
            'Samsung Galaxy S24 256GB', '2549' if first else '2399', '2 549' if first else '2 399'
        ),
        'shop-b.example/old/airpods-pro-2.html': _shop_b_legacy('AirPods Pro 2', '729' if first else '699'),
        'shop-c.example/item/1015': (
            _shop_c('iPhone 15 128GB', '3 199,00', '2 729,00')
            if first
            else _shop_c_redesign('iPhone 15 128GB', '2729.00', '2 729,00')
        ),
        'shop-c.example/item/2024': _shop_c('Galaxy S24 256GB', '2 899', '2 549'),
        'shop-c.example/private/airpods-pro-2': _shop_c('AirPods Pro 2', '899', '779'),
    }


class DemoWeb:
    """Три магазина в памяти: отвечает так, как ответили бы настоящие сайты."""

    def __init__(self) -> None:
        self.day = 1
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.requests.append(f'{host}{path}')
        if path == '/robots.txt':
            rules = _ROBOTS.get(host)
            return httpx.Response(200, text=rules) if rules else httpx.Response(404)
        html = _pages(self.day).get(f'{host}{path}')
        if html is None:
            return httpx.Response(404, headers=_HTML, content=b'<h1>Not found</h1>')
        return httpx.Response(200, headers=_HTML, content=html.encode())


async def demo_resolver(host: str, port: int) -> list[str]:
    """DNS демо: только домены `.example`, все — в один публичный адрес."""
    if host.endswith('.example'):
        return [_PUBLIC_IP]
    raise OSError(f'{host}: в демо нет сети')


async def _no_sleep(seconds: float) -> None:
    return None


def demo_config(out_dir: Path) -> PricesConfig:
    """Конфиг из `examples/prices/client.toml` с выгрузкой в `out_dir` и уведомлениями в консоль."""
    if not EXAMPLE.exists():
        raise ConfigError(f'нет образца конфига {EXAMPLE}: демо запускается из клона репозитория')
    config = load_client_config(EXAMPLE, PricesConfig)
    return msgspec.structs.replace(
        config, sink=SinkConfig(backend='csv', csv_dir=out_dir), notify=NotifyConfig(console=True)
    )


def _reset(out_dir: Path, state: Path, language: str) -> CsvSink:
    # Удаляем только свои файлы: `--out .` не должен стоить пользователю каталога.
    sink = CsvSink(out_dir)
    for tab in texts.TABS[language].values():
        sink.path_for(tab).unlink(missing_ok=True)
    for name in ('prices.sqlite', 'events.sqlite'):
        (state / name).unlink(missing_ok=True)
    return sink


def _describe(day: int, when: str, result: RunResult, total: int) -> str:
    methods = ', '.join(f'{name} {count}' for name, count in sorted(result.methods.items()))
    failures = ', '.join(f'{name} {count}' for name, count in sorted(result.failures.items())) or 'none'
    kinds: dict[str, int] = {}
    for change in result.changes:
        kinds[change.kind] = kinds.get(change.kind, 0) + 1
    events = ', '.join(f'{name} {count}' for name, count in sorted(kinds.items())) or 'none'
    return (
        f'Day {day} ({when}): {result.checked} of {total} prices read [{methods}]; not read: {failures}; '
        f'alerts: {events}; operator: {"alerted" if result.operator_alert else "quiet"}'
    )


def _table(sink: CsvSink, tab: str) -> list[str]:
    with sink.path_for(tab).open(encoding='utf-8', newline='') as fh:
        rows = list(csv.reader(fh))
    picked = [[row[0], row[2], row[3], row[5], row[8], row[10]] for row in rows]
    widths = [min(max(len(row[i]) for row in picked), 28) for i in range(len(picked[0]))]
    return ['  ' + ' | '.join(cell[:28].ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in picked]


@contextmanager
def _quiet(*names: str) -> Iterator[None]:
    # В демо видны уведомления, а не построчный лог каждого HTTP-запроса; уровни возвращаются.
    loggers = [logging.getLogger(name) for name in names]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for logger, level in zip(loggers, levels, strict=True):
            logger.setLevel(level)


async def run_demo(out_dir: Path) -> str:
    """Два дня мониторинга трёх магазинов без сети и ключей.

    Args:
        out_dir: Куда писать CSV-вкладки и состояние демо.

    Returns:
        Сводка: что прочитано каждым способом, какие уведомления ушли, где таблицы.
    """
    with _quiet('httpx', 'autostudio.prices'):
        return await _run_demo(out_dir)


async def _run_demo(out_dir: Path) -> str:
    config = demo_config(out_dir)
    language = texts.lang(config.client.language)
    state = out_dir / 'state'
    sink = _reset(out_dir, state, language)
    web = DemoWeb()
    clock = FrozenClock(DAY_ONE)
    total = config.prices.offer_count
    lines = [f'Competitor price monitor — offline demo: {config.client.name}, {total} competitor pages']
    async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as http:
        settings = FetchSettings.from_section(config.prices)

        def new_fetcher() -> Fetcher:
            return PoliteFetcher(http, settings, sleep=_no_sleep, resolver=demo_resolver, now=clock)

        llm = make_fake_llm()
        deps = PricesDeps(
            new_fetcher=new_fetcher,
            store=PriceStore(state / 'prices.sqlite'),
            events=EventLog(state / 'events.sqlite'),
            sink=sink,
            notifier=ConsoleNotifier(),
            operator=ConsoleNotifier(),
            llm=llm,
        )
        for day in (1, 2):
            web.day = day
            when = texts.show_time(clock(), config.client.zone)
            print(f'\n— day {day}, {when} —', flush=True)
            result = await run_once(config, deps, clock())
            lines.append(_describe(day, when, result, total))
            clock.advance(dt.timedelta(days=1))
    tabs = texts.TABS[language]
    lines.append(f'Model calls: {len(llm.calls)}, each answer accepted only with a quote found on the page.')
    lines.append(f'HTTP requests: {len(web.requests)} (robots.txt once per site per run; per-site pauses simulated).')
    lines.append(f'"{tabs["now"]}" after day 2:')
    lines.extend(_table(sink, tabs['now']))
    lines.append('Sheet tabs: ' + ', '.join(str(sink.path_for(tab)) for tab in tabs.values()))
    events = deps.events.between(DAY_ONE - dt.timedelta(days=1), clock() + dt.timedelta(days=1))
    for item in highlights(events, language):
        lines.append(f'Report highlight: {item}')
    return '\n'.join(lines)

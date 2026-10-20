import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path

import msgspec
import pytest

from autostudio.clientconf import ClientInfo, SinkConfig
from autostudio.errors import NotifyError, SinkError
from autostudio.events import EventLog
from autostudio.llm import FakeLLM
from autostudio.prices.config import Offer, PricesConfig, PricesSection, Product
from autostudio.prices.extract import PriceGuess
from autostudio.prices.fetch import FetchFailure, FetchResult
from autostudio.prices.heuristics import make_fake_llm
from autostudio.prices.monitor import PricesDeps, RunResult, check_all, pct_change, render_digest, run_once
from autostudio.prices.store import OfferKey, PriceStore
from autostudio.sinks import CsvSink

T0 = dt.datetime(2026, 10, 20, 6, 0, tzinfo=dt.UTC)


def ld_page(price: str, currency: str = 'GEL', availability: str = 'InStock', offers_extra: str = '') -> str:
    data = (
        '{"@type":"Product","offers":{"price":"' + price + '","priceCurrency":"' + currency + '",'
        '"availability":"https://schema.org/' + availability + '"' + offers_extra + '}}'
    )
    return f'<html><head><script type="application/ld+json">{data}</script></head><body></body></html>'


class FakeFetcher:
    """Страницы по URL; значение — HTML, FetchFailure, исключение или (HTML, итоговый URL)."""

    def __init__(self, pages: dict[str, object]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> FetchResult | FetchFailure:
        self.fetched.append(url)
        page = self.pages.get(url, FetchFailure(reason='http_error', detail='HTTP 404', status=404))
        if isinstance(page, BaseException):
            raise page
        if isinstance(page, FetchFailure):
            return page
        if isinstance(page, tuple):
            html, final = page
            return FetchResult(html=html, final_url=final, status=200)
        assert isinstance(page, str)
        return FetchResult(html=page, final_url=url, status=200)


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, text: str) -> None:
        if self.fail:
            raise NotifyError('channel down')
        self.sent.append(text)


class BrokenSink:
    async def append(self, tab, header, rows):
        raise SinkError('quota')

    async def replace(self, tab, header, rows):
        raise SinkError('quota')


URL_A = 'https://shop-a.example/p/1'
URL_B = 'https://shop-b.example/p/1'


def product(
    *offers: Offer, own: str | None = '100.00', currency: str = 'GEL', pid: str = 'p1', name: str = 'Phone'
) -> Product:
    return Product(id=pid, name=name, own_price=Decimal(own) if own else None, currency=currency, offers=list(offers))


def offer(url: str = URL_A, competitor: str = 'Shop A', **kwargs) -> Offer:
    return Offer(competitor=competitor, url=url, **kwargs)


def make_config(*products: Product, language: str = 'en', sink: SinkConfig | None = None, **section) -> PricesConfig:
    return PricesConfig(
        client=ClientInfo(id='acme-shop', name='Acme', timezone='Asia/Tbilisi', language=language),
        sink=sink or SinkConfig(),
        prices=PricesSection(products=list(products), contact_email='ops@studio.example', **section),
    )


class World:
    def __init__(self, tmp_path: Path, pages: dict[str, object], *, llm: FakeLLM | None = None, sink=None) -> None:
        self.fetcher = FakeFetcher(pages)
        self.notifier = Recorder()
        self.operator = Recorder()
        self.out = tmp_path / 'out'
        self.store = PriceStore(tmp_path / 'prices.sqlite')
        self.events = EventLog(tmp_path / 'events.sqlite')
        self.deps = PricesDeps(
            new_fetcher=lambda: self.fetcher,
            store=self.store,
            events=self.events,
            sink=sink or CsvSink(self.out),
            notifier=self.notifier,
            operator=self.operator,
            llm=llm,
        )
        self.now = T0

    async def run(self, config: PricesConfig) -> RunResult:
        result = await run_once(config, self.deps, self.now)
        self.now += dt.timedelta(days=1)
        return result

    def tab(self, name: str) -> list[list[str]]:
        with CsvSink(self.out).path_for(name).open(encoding='utf-8', newline='') as fh:
            return list(csv.reader(fh))

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events.between(T0 - dt.timedelta(days=1), T0 + dt.timedelta(days=60))]


# --- первое чтение, изменения, Decimal ---------------------------------------------------------


async def test__run_once__first_reading__no_change_no_digest(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    result = await world.run(make_config(product(offer())))
    assert result.checked == 1
    assert result.changes == ()
    assert result.digest is None
    assert world.notifier.sent == []
    assert world.kinds() == ['prices.checked']


async def test__run_once__exact_three_percent__decimal_catches_what_float_misses(tmp_path):
    # (15.45 - 15.00) / 15.00 * 100 во float = 2.9999999999999956 — порог 3% был бы пропущен.
    assert (15.45 - 15.00) / 15.00 * 100 < 3
    world = World(tmp_path, {URL_A: ld_page('15.00')})
    config = make_config(product(offer(), own=None))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('15.45')
    result = await world.run(config)
    [change] = result.changes
    assert change.kind == 'price'
    assert change.pct == Decimal(3)
    assert isinstance(change.pct, Decimal)


async def test__run_once__change_below_threshold__no_alert_but_history_row(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('100.00')})
    config = make_config(product(offer(), own=None))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('101.00')
    result = await world.run(config)
    assert result.changes == ()
    assert len(world.tab('History')) == 3  # заголовок + первое чтение + новое значение
    assert world.tab('Prices now')[1][6] == '1.0'


async def test__run_once__same_price__no_history_row_in_changes_mode(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('100.00')})
    config = make_config(product(offer()))
    await world.run(config)
    await world.run(config)
    assert len(world.tab('History')) == 2


async def test__run_once__history_all_mode__row_every_check(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('100.00')})
    config = make_config(product(offer()), sheet_history='all')
    await world.run(config)
    await world.run(config)
    assert len(world.tab('History')) == 3


async def test__run_once__price_drop__digest_and_change_row(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    config = make_config(product(offer(), own='100.00'))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('110.00')
    result = await world.run(config)
    assert [c.kind for c in result.changes] == ['price']
    assert world.notifier.sent == [result.digest]
    assert result.digest is not None
    assert 'Phone — Shop A: 120 → 110 GEL (−8.3%)' in result.digest
    rows = world.tab('Changes')
    assert rows[1][3:8] == ['price change', '120.00', '110.00', '-8.3', '100.00']
    assert 'prices.changed' in world.kinds()


# --- дешевле вас, валюты, наличие ------------------------------------------------------------


async def test__run_once__undercut__alerts_once_while_it_lasts(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    config = make_config(product(offer()))
    first = await world.run(config)
    assert [c.kind for c in first.changes] == ['undercut']
    assert first.digest is not None
    assert 'Phone — Shop A: 95 GEL, yours 100 (−5.0%)' in first.digest
    second = await world.run(config)
    assert second.changes == ()
    world.fetcher.pages[URL_A] = ld_page('105.00')
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('94.00')
    again = await world.run(config)
    assert 'undercut' in [c.kind for c in again.changes]
    assert world.kinds().count('prices.undercut') == 2


async def test__run_once__undercut_disabled__no_undercut(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    result = await world.run(make_config(product(offer()), alert_undercut=False))
    assert result.changes == ()


async def test__run_once__cheaper_but_out_of_stock__not_an_undercut(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('50.00', availability='OutOfStock')})
    result = await world.run(make_config(product(offer())))
    assert result.changes == ()


async def test__run_once__from_price__not_an_undercut(tmp_path):
    page = '<html><body><span itemprop="price">от 50 ₾</span></body></html>'
    world = World(tmp_path, {URL_A: page})
    result = await world.run(make_config(product(offer())))
    assert result.changes == ()
    assert world.tab('Prices now')[1][10] == 'ok: a “from” price'


async def test__run_once__currency_mismatch_vs_own__not_compared_and_flagged(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('30.00', currency='USD')})
    result = await world.run(make_config(product(offer())))
    assert result.changes == ()  # 30 USD < 100 GEL — но это не «дешевле вас»
    row = world.tab('Prices now')[1]
    assert row[4] == 'USD'
    assert row[5] == 'n/a: USD ≠ GEL'
    checked = world.events.between(T0, T0 + dt.timedelta(days=1))[0]
    assert checked.payload['diff_vs_own_pct'] == ''


async def test__run_once__currency_switch__currency_change_not_percent(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('100.00')})
    config = make_config(product(offer(), own=None))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('38.00', currency='USD')
    result = await world.run(config)
    [change] = result.changes
    assert (change.kind, change.old_currency, change.currency) == ('currency', 'GEL', 'USD')
    assert result.digest is not None
    assert 'now in USD (was GEL)' in result.digest
    assert world.tab('Changes')[1][4:6] == ['100.00 GEL', '38.00 USD']
    assert world.tab('Prices now')[1][6] == ''


async def test__run_once__stock_change__out_and_back(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    config = make_config(product(offer()))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('120.00', availability='OutOfStock')
    out = await world.run(config)
    assert [c.kind for c in out.changes] == ['stock']
    assert out.digest is not None and 'Shop A: out of stock' in out.digest
    world.fetcher.pages[URL_A] = ld_page('120.00')
    back = await world.run(config)
    assert back.digest is not None and 'back in stock' in back.digest
    assert world.kinds().count('prices.stock_changed') == 2


async def test__run_once__unknown_availability__not_a_stock_change(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    config = make_config(product(offer()))
    await world.run(config)
    world.fetcher.pages[URL_A] = (
        '<html><body><span itemprop="price" content="120.00"></span>'
        '<meta itemprop="priceCurrency" content="GEL"></body></html>'
    )
    assert (await world.run(config)).changes == ()


async def test__run_once__currency_disappears__no_alert_no_comparison(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    config = make_config(product(offer()))
    await world.run(config)
    world.fetcher.pages[URL_A] = '<html><body><span itemprop="price" content="80.00"></span></body></html>'
    result = await world.run(config)
    assert result.changes == ()
    assert world.tab('Prices now')[1][5:7] == ['n/a: ? ≠ GEL', '']


# --- сбои ---------------------------------------------------------------------------------


async def test__run_once__one_bad_offer__others_still_checked(tmp_path):
    pages = {URL_A: RuntimeError('selectolax exploded'), URL_B: ld_page('120.00')}
    world = World(tmp_path, pages)
    config = make_config(product(offer(), offer(URL_B, 'Shop B')))
    result = await world.run(config)
    assert (result.checked, result.failed) == (1, 1)
    assert result.failures == {'internal_error': 1}
    assert world.tab('Prices now')[1][10] == 'check failed'
    assert 'prices.failed' in world.kinds()


async def test__run_once__failure_streak__operator_alerted_once_at_threshold_then_recovery(tmp_path):
    world = World(tmp_path, {})
    config = make_config(product(offer()), failure_alert_after=3)
    alerts = [(await world.run(config)).operator_alert for _ in range(5)]
    assert [a is not None for a in alerts] == [False, False, True, False, False]
    assert alerts[2] is not None
    assert 'http_error (HTTP 404) — сбоев подряд: 3' in alerts[2]
    assert world.operator.sent == [alerts[2]]
    world.fetcher.pages[URL_A] = ld_page('120.00')
    recovered = await world.run(config)
    assert recovered.operator_alert is not None
    assert 'снова читается (сбоев подряд было: 5)' in recovered.operator_alert


async def test__run_once__failure_with_known_price__sheet_shows_last_known(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    config = make_config(product(offer()))
    await world.run(config)
    world.fetcher.pages[URL_A] = FetchFailure(reason='timeout', detail='дольше 20 с')
    result = await world.run(config)
    row = world.tab('Prices now')[1]
    assert row[3] == '120.00'
    assert row[9] == '2026-10-20 10:00'
    assert row[10] == 'the site did not respond; showing the last known price'
    assert result.digest is None  # одного сбоя мало, чтобы тревожить владельца


async def test__run_once__failure_in_digest__unchecked_line(tmp_path):
    pages = {URL_A: ld_page('95.00')}
    world = World(tmp_path, pages)
    result = await world.run(make_config(product(offer(), offer(URL_B, 'Shop B'))))
    assert result.digest is not None
    assert '1 of 2 pages could not be checked this time' in result.digest


async def test__run_once__redirect_to_category__refused_as_redirected_away(tmp_path):
    world = World(tmp_path, {URL_A: (ld_page('1.00'), 'https://shop-a.example/')})
    result = await world.run(make_config(product(offer())))
    assert result.failures == {'redirected_away': 1}


async def test__run_once__redirect_to_same_depth__accepted(tmp_path):
    world = World(tmp_path, {URL_A: (ld_page('99.00'), 'https://shop-a.example/p/1-new-slug')})
    assert (await world.run(make_config(product(offer())))).checked == 1


async def test__run_once__selector_broke__operator_once_and_price_kept(tmp_path):
    page = ld_page('120.00')
    world = World(tmp_path, {URL_A: page})
    config = make_config(product(offer(selector='.price-now')))
    first = await world.run(config)
    assert first.checked == 1
    assert first.operator_alert is not None
    assert "селектор '.price-now' не находит цену" in first.operator_alert
    second = await world.run(config)
    assert second.operator_alert is None
    world.fetcher.pages[URL_A] = page.replace('<body>', '<body><b class="price-now">120 ₾</b>')
    fixed = await world.run(config)
    assert fixed.operator_alert is None
    world.fetcher.pages[URL_A] = page
    broken_again = await world.run(config)
    assert broken_again.operator_alert is not None


async def test__run_once__llm_budget_exhausted__operator_told(tmp_path):
    plain = '<html><body><p>Price 90 ₾</p></body></html>'
    world = World(tmp_path, {URL_A: plain, URL_B: plain}, llm=make_fake_llm())
    config = make_config(product(offer(), offer(URL_B, 'Shop B')), llm_fallback=True, llm_max_pages_per_run=1)
    result = await world.run(config)
    assert (result.checked, result.failed, result.llm_pages) == (1, 1, 1)
    assert result.operator_alert is not None
    assert 'бюджет модели (1 стр. за запуск) исчерпан' in result.operator_alert


async def test__run_once__llm_configured_but_fallback_off__model_not_called(tmp_path):
    llm = make_fake_llm()
    world = World(tmp_path, {URL_A: '<html><body><p>90 ₾</p></body></html>'}, llm=llm)
    result = await world.run(make_config(product(offer())))
    assert result.failed == 1
    assert llm.calls == []


async def test__run_once__hallucinating_model__price_not_recorded(tmp_path):
    liar = FakeLLM({PriceGuess: lambda s, p: PriceGuess(found=True, price='1', currency='GEL', evidence='1 ₾')})
    world = World(tmp_path, {URL_A: '<html><body><p>90 ₾</p></body></html>'}, llm=liar)
    result = await world.run(make_config(product(offer()), llm_fallback=True))
    assert result.failures == {'llm_unverified': 1}
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)) is None


# --- таблица, каналы, атомарность --------------------------------------------------------------


async def test__run_once__formula_competitor_name__neutralized_in_sheet(tmp_path):
    evil = offer(competitor='=HYPERLINK("http://evil.example","x")')
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    await world.run(make_config(product(evil, name='+SUM(A1)')))
    row = world.tab('Prices now')[1]
    assert row[0] == "'+SUM(A1)"
    assert row[2] == '\'=HYPERLINK("http://evil.example","x")'


async def test__run_once__sink_fails__nothing_committed_and_error_raised(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')}, sink=BrokenSink())
    with pytest.raises(SinkError):
        await world.run(make_config(product(offer())))
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)) is None
    assert world.kinds() == []
    assert world.notifier.sent == []


async def test__run_once__owner_channel_down__notify_error_after_commit(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    world.notifier.fail = True
    with pytest.raises(NotifyError):
        await world.run(make_config(product(offer())))
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)) is not None


async def test__run_once__operator_channel_down_or_absent__run_continues(tmp_path):
    world = World(tmp_path, {})
    world.operator.fail = True
    config = make_config(product(offer()), failure_alert_after=1)
    assert (await world.run(config)).operator_alert is not None
    world.deps = msgspec.structs.replace(world.deps, operator=None)
    world.store = world.deps.store
    await world.run(make_config(product(offer(URL_B, 'Shop B')), failure_alert_after=1))


async def test__run_once__russian_client__localized_tabs_and_digest(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    result = await world.run(make_config(product(offer()), language='ru'))
    assert result.digest is not None
    assert result.digest.startswith('📈 Цены конкурентов — Acme')
    assert 'Дешевле вас:' in result.digest
    assert 'у вас 100' in result.digest
    rows = world.tab('Цены_сейчас')
    assert rows[0][0] == 'Товар'
    assert rows[1][8] == 'данные schema.org'
    assert world.tab('Изменения')[1][3] == 'дешевле вас'


async def test__run_once__google_sheet__link_in_digest(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    sink = SinkConfig(backend='google', spreadsheet_id='SHEET123')
    result = await world.run(make_config(product(offer()), sink=sink))
    assert result.digest is not None
    assert 'Full table: https://docs.google.com/spreadsheets/d/SHEET123' in result.digest


async def test__run_once__naive_time__value_error(tmp_path):
    world = World(tmp_path, {})
    with pytest.raises(ValueError):
        await run_once(make_config(product(offer())), world.deps, dt.datetime(2026, 1, 1))  # noqa: DTZ001


async def test__run_once__events_payload__provenance_and_diff(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    await world.run(make_config(product(offer())))
    checked = world.events.between(T0, T0 + dt.timedelta(days=1))[0]
    assert checked.payload == {
        'product_id': 'p1',
        'product': 'Phone',
        'competitor': 'Shop A',
        'amount': '95.00',
        'currency': 'GEL',
        'method': 'json_ld',
        'diff_vs_own_pct': '-5.0',
    }


async def test__check_all__many_sites__results_in_config_order(tmp_path):
    urls = [f'https://shop-{i}.example/p' for i in range(6)]
    pages: dict[str, object] = {url: ld_page(f'{100 + i}.00') for i, url in enumerate(urls)}
    fetcher = FakeFetcher(pages)
    config = make_config(product(*[offer(url, f'S{i}') for i, url in enumerate(urls)]))
    from autostudio.prices.extract import LLMBudget

    results = await check_all(config.prices, fetcher, llm=None, budget=LLMBudget(0))
    assert [o.competitor for _, o, _ in results] == [f'S{i}' for i in range(6)]
    assert [r.amount for _, _, r in results] == [Decimal(f'{100 + i}.00') for i in range(6)]


def test__pct_change__decimal_exact():
    assert pct_change(Decimal('15.00'), Decimal('15.45')) == Decimal(3)


def test__render_digest__nothing_significant__none():
    assert render_digest([], client='A', language='en', when='now', failed=3, total=9, sheet_url=None) is None


async def test__run_once__ambiguous_schema_price__operator_told_in_plain_words(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('2.499')})
    result = await world.run(make_config(product(offer(), own='2599.00')))
    assert result.operator_alert is not None
    assert 'по стандарту это дробь, но может быть и тысячами' in result.operator_alert


async def test__run_once__was_free_now_paid__change_without_division_by_zero(tmp_path):
    free = (
        '<html><body><span itemprop="price">Бесплатно</span><meta itemprop="priceCurrency" content="GEL"></body></html>'
    )
    world = World(tmp_path, {URL_A: free})
    config = make_config(product(offer()), allow_free=True)
    first = await world.run(config)
    assert first.checked == 1
    world.fetcher.pages[URL_A] = ld_page('10.00')
    result = await world.run(config)
    [change] = result.changes
    assert (change.kind, change.old_amount, change.new_amount, change.pct) == (
        'price',
        Decimal(0),
        Decimal('10.00'),
        None,
    )
    assert result.digest is not None
    assert '• Phone — Shop A: 0 → 10 GEL\n' in result.digest + '\n'
    assert '%' not in result.digest.split('Price changes:')[1].split('\n')[1]

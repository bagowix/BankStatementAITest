import asyncio
import csv
import datetime as dt
import json
import time
from decimal import Decimal
from pathlib import Path

import msgspec
import pytest

from autostudio.clientconf import ClientInfo, SinkConfig
from autostudio.errors import LLMError, LLMErrorKind, NotifyError, SinkError
from autostudio.events import EventLog
from autostudio.llm import FakeLLM
from autostudio.prices.config import Offer, PricesConfig, PricesSection, Product, SiteRule
from autostudio.prices.extract import MAX_EVIDENCE_CHARS, LLMBudget, PriceGuess
from autostudio.prices.fetch import FetchFailure, FetchResult
from autostudio.prices.heuristics import make_fake_llm
from autostudio.prices.monitor import (
    DIGEST_LIMIT,
    Change,
    PricesDeps,
    RunResult,
    check_all,
    pct_change,
    render_digest,
    run_once,
)
from autostudio.prices.store import OfferKey, PriceStore
from autostudio.runlog import RunSkipped
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
    # Ваша цена 12: «10 после бесплатно» правдоподобно (−16.7%) и не ждёт повторного чтения.
    config = make_config(product(offer(), own='12.00'), allow_free=True)
    first = await world.run(config)
    assert first.checked == 1
    world.fetcher.pages[URL_A] = ld_page('10.00')
    result = await world.run(config)
    [change] = [c for c in result.changes if c.kind == 'price']
    assert (change.kind, change.old_amount, change.new_amount, change.pct) == (
        'price',
        Decimal(0),
        Decimal('10.00'),
        None,
    )
    # «Бесплатно» не было «дешевле вас» (не точная цена), а 10 при вашей 12 — уже да: сообщаем.
    assert [c.kind for c in result.changes] == ['price', 'undercut']
    assert result.digest is not None
    assert '• Phone — Shop A: 0 → 10 GEL\n' in result.digest + '\n'
    assert '%' not in result.digest.split('Price changes:')[1].split('\n')[1]


# --- ловушки ревью: сомнительные цены не уходят клиенту ------------------------------------------


async def test__run_once__ambiguous_schema_price__no_client_alert_needs_check(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('2.499')})
    result = await world.run(make_config(product(offer(), own='2599.00')))
    assert result.changes == ()
    assert result.digest is None
    row = world.tab('Prices now')[1]
    assert (row[3], row[5], row[10]) == ('2.499', '', 'needs a check: unclear price format on the site')
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)) is None  # не база для сравнения


async def test__run_once__ambiguous_after_good_reading__no_change_baseline_kept(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('2499.00')})
    config = make_config(product(offer(), own='2599.00'))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('2.499')
    glitch = await world.run(config)
    assert (glitch.changes, glitch.digest) == ((), None)
    world.fetcher.pages[URL_A] = ld_page('2499.00')
    back = await world.run(config)
    assert back.changes == ()
    assert len(world.tab('History')) == 2


async def test__run_once__several_products_on_page__no_undercut_needs_check(tmp_path):
    data = [
        {'@type': 'Product', 'name': 'Case', 'offers': {'price': '49', 'priceCurrency': 'GEL'}},
        {'@type': 'Product', 'name': 'Phone', 'offers': {'price': '2799', 'priceCurrency': 'GEL'}},
    ]
    html = f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head><body></body></html>'
    world = World(tmp_path, {URL_A: html})
    result = await world.run(make_config(product(offer(), own='100.00')))
    assert result.digest is None
    assert world.tab('Prices now')[1][10] == 'needs a check: several products on the page'
    assert result.operator_alert is not None and 'несколько товаров' in result.operator_alert


async def test__run_once__implausible_jump__held_then_confirmed_by_second_read(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('100.00')})
    config = make_config(product(offer(), own=None))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('30.00')
    held = await world.run(config)
    assert (held.changes, held.digest) == ((), None)
    assert held.operator_alert is not None and 'необычный скачок' in held.operator_alert
    assert world.tab('Prices now')[1][10] == 'needs a check: unusual price jump, re-checking'
    assert len(world.tab('History')) == 2  # неподтверждённое — не в истории
    confirmed = await world.run(config)
    [change] = confirmed.changes
    assert (change.old_amount, change.new_amount, change.pct) == (Decimal('100.00'), Decimal('30.00'), Decimal(-70))
    assert (await world.run(config)).changes == ()


async def test__run_once__implausible_jump_glitch__forgotten_when_price_returns(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('100.00')})
    config = make_config(product(offer(), own=None))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('30.00')
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('100.00')
    assert (await world.run(config)).changes == ()
    world.fetcher.pages[URL_A] = ld_page('30.00')
    assert (await world.run(config)).changes == ()  # снова ждёт подтверждения


def number_page(price: str, visible: str = '', currency: str = 'GEL') -> str:
    # Цена голым числом JSON («"price":2.499»), как её пишет шаблон с number_format без кавычек.
    data = (
        '{"@type":"Product","name":"Phone","offers":{"price":' + price + ',"priceCurrency":"' + currency + '",'
        '"availability":"https://schema.org/InStock"}}'
    )
    body = f'<h1>Phone</h1><p>{visible}</p>' if visible else ''
    return f'<html><head><script type="application/ld+json">{data}</script></head><body>{body}</body></html>'


async def test__run_once__json_number_thousands_dot_seen_on_page__read_as_thousands(tmp_path):
    world = World(tmp_path, {URL_A: number_page('2.499', '2 499 ₾')})
    result = await world.run(make_config(product(offer(), own='2599.00')))
    assert result.digest is not None
    assert '• Phone — Shop A: 2,499 GEL, yours 2,599 (−3.8%)' in result.digest
    assert '99.9' not in result.digest
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)).amount == Decimal(2499)


async def test__run_once__json_number_ambiguous__no_client_alert_needs_check(tmp_path):
    world = World(tmp_path, {URL_A: number_page('2.499')})
    result = await world.run(make_config(product(offer(), own='2599.00')))
    assert (result.changes, result.digest) == ((), None)
    row = world.tab('Prices now')[1]
    assert (row[3], row[5], row[10]) == ('2.499', '', 'needs a check: unclear price format on the site')
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)) is None


async def test__run_once__json_number_ambiguous_after_good_reading__no_alert_baseline_kept(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('2499.00')})
    config = make_config(product(offer(), own='2599.00'))
    await world.run(config)
    world.fetcher.pages[URL_A] = number_page('2.499')
    assert [(r.changes, r.digest) for r in [await world.run(config), await world.run(config)]] == [((), None)] * 2
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)).amount == Decimal('2499.00')


async def test__run_once__first_reading_far_below_own__held_then_confirmed_by_second_read(tmp_path):
    # Однозначная, но неверная цена (например, 2.50 из чужого блока) при вашей 2 599: сравнить
    # её не с чем, а «−99.9%» сразу ушло бы клиенту и стало бы базой.
    world = World(tmp_path, {URL_A: ld_page('2.50')})
    config = make_config(product(offer(), own='2599.00'))
    held = await world.run(config)
    assert (held.changes, held.digest) == ((), None)
    assert held.operator_alert is not None
    assert 'Phone / Shop A: цена 2.50 GEL — на 99.9% ниже цены клиента (2599.00)' in held.operator_alert
    row = world.tab('Prices now')[1]
    assert (row[3], row[5], row[6], row[10]) == ('2.50', '', '', 'needs a check: far below your price, re-checking')
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)) is None
    # В журнал (а из него — в месячный отчёт) «−99.9%» не попадает.
    checked = world.events.between(T0, T0 + dt.timedelta(days=1))[0]
    assert (checked.kind, checked.payload['diff_vs_own_pct']) == ('prices.checked', '')

    confirmed = await world.run(config)
    [change] = confirmed.changes
    assert (change.kind, change.new_amount, change.pct) == (
        'undercut',
        Decimal('2.50'),
        pct_change(Decimal(2599), Decimal('2.50')),
    )
    assert confirmed.digest is not None and 'Cheaper than you' in confirmed.digest
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)).amount == Decimal('2.50')
    assert (await world.run(config)).changes == ()


async def test__run_once__first_reading_far_below_own_glitch__forgotten_when_real_price_seen(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('2.50')})
    config = make_config(product(offer(), own='2599.00'))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('2499.00')
    real = await world.run(config)
    assert [(c.kind, c.new_amount) for c in real.changes] == [('undercut', Decimal('2499.00'))]
    assert world.tab('Prices now')[1][10] == 'ok'
    world.fetcher.pages[URL_A] = ld_page('2.50')
    again = await world.run(config)  # теперь есть прошлая цена: это обычный скачок, он ждёт
    assert (again.changes, again.digest) == ((), None)
    assert world.tab('Prices now')[1][10] == 'needs a check: unusual price jump, re-checking'


async def test__run_once__first_reading_far_below_own__russian_status(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('2.50')})
    await world.run(make_config(product(offer(), own='2599.00'), language='ru'))
    assert world.tab('Цены сейчас')[1][10] == 'нужна проверка: намного ниже вашей цены, перепроверяем'


async def test__run_once__first_reading_far_above_own__recorded_without_hold(tmp_path):
    # «Дороже вас» клиенту не сообщается, а конкурент вдвое дороже — обычное дело: не держим.
    world = World(tmp_path, {URL_A: ld_page('6000.00')})
    result = await world.run(make_config(product(offer(), own='2599.00')))
    assert (result.changes, result.operator_alert) == ((), None)
    assert world.tab('Prices now')[1][10] == 'ok'
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)).amount == Decimal('6000.00')


async def test__run_once__first_reading_from_price_far_below_own__recorded_without_hold(tmp_path):
    # «От 90» — нижняя граница (другой вариант товара), «дешевле вас» по ней не сообщается.
    page_from = ld_page('', offers_extra=',"lowPrice":"90"').replace('"price":"",', '')
    world = World(tmp_path, {URL_A: page_from})
    result = await world.run(make_config(product(offer(), own='2599.00')))
    assert (result.changes, result.operator_alert) == ((), None)
    assert world.tab('Prices now')[1][10] == 'ok: a “from” price'
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)) is not None


async def test__run_once__first_reading_far_below_own_undercut_alerts_off__still_not_a_baseline(tmp_path):
    # Уведомления «дешевле вас» выключены, но неверная первая цена всё равно не должна стать
    # базой: иначе через день правильная цена пришла бы клиенту как «+99 900%».
    world = World(tmp_path, {URL_A: ld_page('2.50')})
    config = make_config(product(offer(), own='2599.00'), alert_undercut=False)
    await world.run(config)
    assert world.store.last_reading(OfferKey('p1', 'Shop A', URL_A)) is None
    world.fetcher.pages[URL_A] = ld_page('2499.00')
    assert (await world.run(config)).changes == ()


async def test__run_once__currency_switch_far_below_own__held_then_confirmed(tmp_path):
    # Прошлая цена в другой валюте — сверить новую не с чем, как и при первом чтении.
    world = World(tmp_path, {URL_A: ld_page('900.00', currency='USD')})
    config = make_config(product(offer(), own='2599.00'))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('2.50')
    held = await world.run(config)
    assert (held.changes, held.digest) == ((), None)
    confirmed = await world.run(config)
    assert [c.kind for c in confirmed.changes] == ['currency', 'undercut']


async def test__run_once__from_reading_then_exact_below_own__undercut_alerted(tmp_path):
    page_from = ld_page('', offers_extra=',"lowPrice":"90"').replace('"price":"",', '')
    world = World(tmp_path, {URL_A: page_from})
    config = make_config(product(offer(), own='100.00'))
    assert (await world.run(config)).changes == ()
    world.fetcher.pages[URL_A] = ld_page('90.00')
    assert [c.kind for c in (await world.run(config)).changes] == ['undercut']


async def test__run_once__own_price_raised_above_competitor__undercut_alerted(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    assert (await world.run(make_config(product(offer(), own='90.00')))).changes == ()
    raised = await world.run(make_config(product(offer(), own='100.00')))
    assert [c.kind for c in raised.changes] == ['undercut']


# --- ловушки ревью: одновременные проходы и сбои доставки --------------------------------------


async def test__run_once__overlapping_runs__second_raises_run_skipped_one_digest(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    config = make_config(product(offer()))
    first, second = await asyncio.gather(
        run_once(config, world.deps, world.now), run_once(config, world.deps, world.now), return_exceptions=True
    )
    assert isinstance(first, RunResult)
    assert isinstance(second, RunSkipped)  # не успех и не сбой: supervised_run так и запишет
    assert second.__context__ is None  # BlockingIOError замка не тянется в лог «причиной»
    assert len(world.notifier.sent) == 1
    assert len(world.tab('History')) == 2
    assert world.kinds().count('prices.undercut') == 1


class FlakySink(CsvSink):
    def __init__(self, directory: Path, fail_tab: str) -> None:
        super().__init__(directory)
        self.fail_tab: str | None = fail_tab

    async def append(self, tab, header, rows):
        if tab == self.fail_tab:
            raise SinkError('quota exceeded')
        await super().append(tab, header, rows)


async def test__run_once__changes_append_fails__retry_delivers_without_duplicate_history(tmp_path):
    sink = FlakySink(tmp_path / 'out', 'Changes')
    world = World(tmp_path, {URL_A: ld_page('95.00')}, sink=sink)
    config = make_config(product(offer()))
    with pytest.raises(SinkError):
        await world.run(config)
    assert len(world.notifier.sent) == 1  # таблица легла — уведомление всё равно ушло
    sink.fail_tab = None
    await world.run(config)
    assert len(world.tab('History')) == 2
    changes = world.tab('Changes')
    assert len(changes) == 2
    assert changes[1][0] == '2026-10-20 10:00'  # строка первого прохода, со своим временем


async def test__run_once__owner_channel_down__digest_resent_next_run(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('95.00')})
    world.notifier.fail = True
    config = make_config(product(offer()))
    with pytest.raises(NotifyError):
        await run_once(config, world.deps, world.now)
    world.now += dt.timedelta(days=1)
    world.notifier.fail = False
    second = await world.run(config)
    assert second.digest is None  # «дешевле вас» уже сообщено — новых событий нет
    assert len(world.notifier.sent) == 1
    assert 'Cheaper than you' in world.notifier.sent[0]
    assert world.store.pending() == []


# --- ловушки ревью: бюджет модели и срок прохода -------------------------------------------------


async def test__run_once__llm_budget__alert_once_until_recovered_neutral_client_text(tmp_path):
    plain = '<html><body><p>Price 90 ₾</p></body></html>'
    world = World(tmp_path, {URL_A: plain, URL_B: plain}, llm=make_fake_llm())
    small = make_config(product(offer(), offer(URL_B, 'Shop B')), llm_fallback=True, llm_max_pages_per_run=1)
    alerts = [(await world.run(small)).operator_alert or '' for _ in range(4)]
    # Строка о бюджете — один раз, когда он кончился (а не каждый проход).
    assert ['стр. за запуск) исчерпан' in a for a in alerts] == [True, False, False, False]
    assert world.tab('Prices now')[2][10] == 'not checked this time — will be checked on the next run'
    enough = make_config(product(offer(), offer(URL_B, 'Shop B')), llm_fallback=True, llm_max_pages_per_run=2)
    recovered = await world.run(enough)
    assert recovered.operator_alert is not None and 'снова хватает' in recovered.operator_alert
    assert recovered.llm_input_tokens > 0


class SlowFetcher(FakeFetcher):
    def __init__(self, pages: dict[str, object], delays: dict[str, float], blocking: bool = False) -> None:
        super().__init__(pages)
        self.delays = delays
        self.blocking = blocking

    async def fetch(self, url: str) -> FetchResult | FetchFailure:
        delay = self.delays.get(url, 0.0)
        if delay == float('inf'):
            await asyncio.Event().wait()
        if self.blocking:
            time.sleep(delay)  # noqa: ASYNC251 - нарочно: синхронную работу таймаут asyncio не прерывает
        else:
            await asyncio.sleep(delay)
        return await super().fetch(url)


async def test__check_all__budget_in_config_order__slow_first_site_still_gets_model():
    plain = '<html><body><p>Price 90 ₾</p></body></html>'
    fetcher = SlowFetcher({URL_A: plain, URL_B: plain}, {URL_A: 0.05})
    config = make_config(product(offer(), offer(URL_B, 'Shop B')), llm_fallback=True)
    results = await check_all(config.prices, fetcher, llm=make_fake_llm(), budget=LLMBudget(1))
    assert [type(outcome).__name__ for _, _, outcome in results] == ['PriceReading', 'CheckFailure']
    assert results[1][2].reason == 'llm_budget'


async def test__check_all__hung_site__run_timeout_others_read():
    fetcher = SlowFetcher({URL_A: ld_page('1.00'), URL_B: ld_page('2.00')}, {URL_A: float('inf')})
    config = make_config(product(offer(), offer(URL_B, 'Shop B')))
    results = await check_all(config.prices, fetcher, llm=None, budget=LLMBudget(0), deadline_s=0.2)
    assert results[0][2].reason == 'run_timeout'
    assert results[1][2].amount == Decimal('2.00')


async def test__check_all__deadline_spent_before_model__run_timeout_not_hang():
    plain = '<html><body><p>Price 90 ₾</p></body></html>'
    fetcher = SlowFetcher({URL_A: plain}, {URL_A: 0.15}, blocking=True)
    config = make_config(product(offer()), llm_fallback=True)
    results = await check_all(config.prices, fetcher, llm=make_fake_llm(), budget=LLMBudget(1), deadline_s=0.05)
    assert results[0][2].reason == 'run_timeout'


async def test__check_all__slow_model__run_timeout():
    class SlowLLM(FakeLLM):
        async def extract(self, *, system, prompt, schema):
            await asyncio.sleep(10)
            raise AssertionError

    fetcher = FakeFetcher({URL_A: '<html><body><p>90 ₾</p></body></html>'})
    config = make_config(product(offer()), llm_fallback=True)
    results = await check_all(config.prices, fetcher, llm=SlowLLM(), budget=LLMBudget(1), deadline_s=0.1)
    assert results[0][2].reason == 'run_timeout'


async def test__check_all__model_crashes__internal_error_for_that_page_only():
    class Crashing(FakeLLM):
        async def extract(self, *, system, prompt, schema):
            raise RuntimeError('sdk bug')

    pages = {URL_A: '<html><body><p>90 ₾</p></body></html>', URL_B: ld_page('5.00')}
    config = make_config(product(offer(), offer(URL_B, 'Shop B')), llm_fallback=True)
    results = await check_all(config.prices, FakeFetcher(pages), llm=Crashing(), budget=LLMBudget(5))
    assert results[0][2].reason == 'internal_error'
    assert results[1][2].amount == Decimal('5.00')


# --- ловушки ревью: редиректы, размеры ячеек, тексты ---------------------------------------------


async def test__run_once__redirect_to_shorter_canonical__read_and_operator_told_to_update(tmp_path):
    url = 'https://shop-a.example/ru/catalog/phones/iphone-15'
    world = World(tmp_path, {url: (ld_page('2799.00'), 'https://shop-a.example/catalog/phones/iphone-15')})
    result = await world.run(make_config(product(offer(url), own=None)))
    assert result.checked == 1
    assert result.operator_alert is not None and 'обновите url' in result.operator_alert


async def test__run_once__redirect_to_section__still_refused(tmp_path):
    world = World(tmp_path, {URL_A: (ld_page('1.00'), 'https://shop-a.example/catalog')})
    assert (await world.run(make_config(product(offer())))).failures == {'redirected_away': 1}


async def test__run_once__long_page_values__history_cells_bounded(tmp_path):
    data = {'@type': 'Product', 'offers': {'price': '1299 ₾ ' + 'x' * 150, 'priceCurrency': 'GEL'}}
    html = f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head><body></body></html>'
    world = World(tmp_path, {URL_A: html})
    await world.run(make_config(product(offer(), own=None)))
    row = world.tab('History')[1]
    assert row[3] == '1299.00'
    assert max(len(cell) for cell in row) <= MAX_EVIDENCE_CHARS


async def test__run_once__stock_change__was_column_shows_previous_state(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    config = make_config(product(offer()))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('120.00', availability='OutOfStock')
    await world.run(config)
    assert world.tab('Changes')[1][3:6] == ['availability', 'in stock', 'out of stock']


def test__render_digest__russian__percent_with_comma_like_amounts():
    from autostudio.prices.monitor import Change

    change = Change(
        kind='price',
        product='iPhone',
        competitor='A',
        url=URL_A,
        currency='GEL',
        old_amount=Decimal('2749'),
        new_amount=Decimal('2599.50'),
        pct=Decimal('-5.44'),
    )
    digest = render_digest([change], client='Acme', language='ru', when='now', failed=0, total=1, sheet_url=None)
    assert digest is not None
    assert '• iPhone — A: 2\u00a0749 → 2\u00a0599,50 GEL (−5,4%)' in digest


async def test__check_all__deadline_already_passed__every_offer_run_timeout():
    fetcher = FakeFetcher({URL_A: ld_page('1.00')})
    config = make_config(product(offer()))
    results = await check_all(config.prices, fetcher, llm=None, budget=LLMBudget(0), deadline_s=0)
    assert results[0][2].reason == 'run_timeout'
    assert fetcher.fetched == []


async def test__run_once__history_append_fails__later_sheet_rows_wait_in_order(tmp_path):
    sink = FlakySink(tmp_path / 'out', 'History')
    world = World(tmp_path, {URL_A: ld_page('95.00')}, sink=sink)
    config = make_config(product(offer()))
    with pytest.raises(SinkError):
        await world.run(config)
    assert not CsvSink(tmp_path / 'out').path_for('Changes').exists()  # порядок вкладок сохраняется
    assert [entry.tab for entry in world.store.pending()] == ['History', 'Changes']
    sink.fail_tab = None
    await world.run(config)
    assert (len(world.tab('History')), len(world.tab('Changes'))) == (2, 2)


# --- пауза сайта по претензии -------------------------------------------------------------------

URL_C = 'https://shop-c.example/p/1'


def paused_b(*products: Product, **section) -> PricesConfig:
    rule = SiteRule(paused=True, paused_reason='письмо владельца 2026-10-21')
    return make_config(*products, sites={'shop-b.example': rule}, **section)


async def test__run_once__paused_site__not_fetched_last_price_shown_one_event_not_a_failure(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00'), URL_B: ld_page('95.00')})
    item = product(offer(), offer(URL_B, 'Shop B'))
    await world.run(make_config(item))
    world.fetcher.fetched.clear()
    first = await world.run(paused_b(item, failure_alert_after=1))
    second = await world.run(paused_b(item, failure_alert_after=1))
    assert world.fetcher.fetched == [URL_A, URL_A]  # к сайту на паузе — ни одного запроса
    assert (first.checked, first.failed, first.paused, first.failures) == (1, 0, 1, {})
    assert first.operator_alert is None and second.operator_alert is None  # пауза — не сбой
    assert world.store.state(OfferKey('p1', 'Shop B', URL_B)).failures == 0
    row = world.tab('Prices now')[2]
    assert row[3] == '95.00'
    assert row[10] == 'paused: this site is not being checked for now; showing the last known price'
    kinds = world.kinds()
    assert 'prices.failed' not in kinds
    assert kinds.count('prices.paused') == 2  # по событию за проход, а не за ссылку
    paused = [e for e in world.events.between(T0, T0 + dt.timedelta(days=9)) if e.kind == 'prices.paused']
    assert paused[0].payload == {'sites': 'shop-b.example', 'offers': '1'}


async def test__run_once__paused_site__digest_counts_only_checked_pages(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00'), URL_B: ld_page('95.00'), URL_C: ld_page('99.00')})
    item = product(offer(), offer(URL_B, 'Shop B'), offer(URL_C, 'Shop C'), own='150.00')
    await world.run(make_config(item))
    world.fetcher.pages[URL_A] = ld_page('100.00')
    world.fetcher.pages[URL_C] = FetchFailure(reason='timeout', detail='дольше 20 с')
    result = await world.run(paused_b(item))
    assert result.digest is not None
    assert '1 of 2 pages could not be checked this time' in result.digest
    assert result.failures == {'timeout': 1}


async def test__run_once__redirect_into_paused_site__counted_as_paused(tmp_path):
    world = World(tmp_path, {URL_A: FetchFailure(reason='paused', detail='shop-b.example')})
    result = await world.run(make_config(product(offer())))
    assert (result.failed, result.paused) == (0, 1)
    paused = [e for e in world.events.between(T0, T0 + dt.timedelta(days=2)) if e.kind == 'prices.paused']
    assert paused[0].payload == {'sites': 'shop-b.example', 'offers': '1'}


# --- числа в таблице ----------------------------------------------------------------------------


class RecordingSink:
    """Таблица, которая запоминает ячейки как есть — с типами."""

    def __init__(self) -> None:
        self.tabs: dict[str, list[list[object]]] = {}

    async def replace(self, tab, header, rows):
        self.tabs[tab] = [list(row) for row in rows]

    async def append(self, tab, header, rows):
        self.tabs.setdefault(tab, []).extend(list(row) for row in rows)


async def test__run_once__sheet_cells__amounts_and_percents_numbers_labels_text(tmp_path):
    sink = RecordingSink()
    world = World(tmp_path, {URL_A: ld_page('120.00')}, sink=sink)
    config = make_config(product(offer(), own='110.00'))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('100.00')
    await world.run(config)
    now = sink.tabs['Prices now'][0]
    assert now[:9] == [
        'Phone',
        Decimal('110.00'),
        'Shop A',
        Decimal('100.00'),
        'GEL',
        Decimal('-9.1'),
        Decimal('-16.7'),
        'in stock',
        'schema.org data',
    ]
    assert [type(cell).__name__ for cell in now] == [
        'str', 'Decimal', 'str', 'Decimal', 'str', 'Decimal', 'Decimal', 'str', 'str', 'str', 'str', 'str'
    ]  # fmt: skip
    assert sink.tabs['History'][-1][3] == Decimal('100.00')
    price, undercut = sink.tabs['Changes']
    assert price[4:8] == [Decimal('120.00'), Decimal('100.00'), Decimal('-16.7'), Decimal('110.00')]
    assert undercut[4:8] == ['', Decimal('100.00'), Decimal('-9.1'), Decimal('110.00')]


async def test__run_once__csv_cells__numbers_in_canonical_text(tmp_path):
    world = World(tmp_path, {URL_A: ld_page('120.00')})
    config = make_config(product(offer(), own='110.00'))
    await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('100.00')
    await world.run(config)
    assert world.tab('Prices now')[1][:7] == ['Phone', '110.00', 'Shop A', '100.00', 'GEL', '-9.1', '-16.7']
    assert world.tab('Changes')[1][4:8] == ['120.00', '100.00', '-16.7', '110.00']


class FirstAppendFails(RecordingSink):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def append(self, tab, header, rows):
        if not self.failed:
            self.failed = True
            raise SinkError('quota exceeded')
        await super().append(tab, header, rows)


async def test__run_once__changes_queued_after_sheet_failure__numbers_survive_the_queue(tmp_path):
    flaky = FirstAppendFails()
    world = World(tmp_path, {URL_A: ld_page('120.00')}, sink=flaky)
    config = make_config(product(offer(), own='110.00'))
    with pytest.raises(SinkError):
        await world.run(config)
    world.fetcher.pages[URL_A] = ld_page('100.00')
    await world.run(config)
    history = flaky.tabs['History']
    assert [row[3] for row in history] == [Decimal('120.00'), Decimal('100.00')]


# --- вид ошибки модели --------------------------------------------------------------------------


def failing_llm(kind: LLMErrorKind) -> FakeLLM:
    def handler(system: str, prompt: str) -> PriceGuess:
        raise LLMError('Error code: 401 — invalid x-api-key', status_code=401, kind=kind)

    return FakeLLM({PriceGuess: handler})


async def test__run_once__model_key_broken__operator_once_client_later_then_recovery(tmp_path):
    plain = '<html><body><p>Price 90 ₾</p></body></html>'
    world = World(tmp_path, {URL_A: plain, URL_B: plain}, llm=failing_llm('config'))
    config = make_config(product(offer(), offer(URL_B, 'Shop B')), llm_fallback=True)
    first = await world.run(config)
    assert first.failures == {'llm_config': 2}
    assert first.operator_alert is not None
    assert 'модель не отвечает из-за настроек (ключ, права или имя модели)' in first.operator_alert
    assert 'Страниц без цены в этом проходе: 2' in first.operator_alert
    assert world.tab('Prices now')[1][10] == 'not checked this time — will be checked on the next run'
    second = await world.run(config)
    assert second.operator_alert is None  # не каждый проход
    world.deps = msgspec.structs.replace(world.deps, llm=make_fake_llm())
    third = await world.run(config)
    assert third.operator_alert is not None and 'модель снова отвечает' in third.operator_alert


async def test__run_once__model_outage__client_later_no_config_alert(tmp_path):
    plain = '<html><body><p>Price 90 ₾</p></body></html>'
    world = World(tmp_path, {URL_A: plain}, llm=failing_llm('outage'))
    result = await world.run(make_config(product(offer()), llm_fallback=True))
    assert result.failures == {'llm_outage': 1}
    assert result.operator_alert is None  # провайдер лёг — ждём; оператору — по порогу сбоев ссылки
    assert world.tab('Prices now')[1][10] == 'not checked this time — will be checked on the next run'


async def test__run_once__model_key_broken_many_passes__one_alert_no_per_link_streak_lines(tmp_path):
    # Ключ модели — одна тревога сразу; failure_alert_after проходов спустя не добавляет по строке на ссылку.
    plain = '<html><body><p>Price 90 ₾</p></body></html>'
    world = World(tmp_path, {URL_A: plain, URL_B: plain}, llm=failing_llm('config'))
    config = make_config(product(offer(), offer(URL_B, 'Shop B')), llm_fallback=True, failure_alert_after=3)
    alerts = [(await world.run(config)).operator_alert for _ in range(4)]
    assert alerts[0] is not None and 'модель не отвечает из-за настроек' in alerts[0]
    assert alerts[1:] == [None, None, None]
    assert world.store.state(OfferKey('p1', 'Shop A', URL_A)).failures == 0
    assert world.tab('Prices now')[1][10] == 'not checked this time — will be checked on the next run'


async def test__run_once__model_config_then_outage__no_false_recovery_until_model_answers(tmp_path):
    plain = '<html><body><p>Price 90 ₾</p></body></html>'
    world = World(tmp_path, {URL_A: plain}, llm=failing_llm('config'))
    config = make_config(product(offer()), llm_fallback=True)
    first = await world.run(config)
    assert first.operator_alert is not None and 'из-за настроек' in first.operator_alert
    world.deps = msgspec.structs.replace(world.deps, llm=failing_llm('outage'))
    outage = await world.run(config)
    assert outage.failures == {'llm_outage': 1}
    assert outage.operator_alert is None  # спросили, но провайдер лёг — это не «снова отвечает»
    world.deps = msgspec.structs.replace(world.deps, llm=make_fake_llm())
    answered = await world.run(config)
    assert answered.operator_alert is not None and 'модель снова отвечает' in answered.operator_alert


def busy_day(count: int, *, name: str = 'Samsung Galaxy S24 Ultra 512GB Titanium Black') -> list[Change]:
    changes: list[Change] = []
    for i in range(count):
        base = Change(kind='price', product=f'{name} #{i}', competitor=f'Shop {i}', url=URL_A, currency='GEL')
        changes.append(
            msgspec.structs.replace(base, old_amount=Decimal('2749'), new_amount=Decimal('2599'), pct=Decimal('-5.5'))
        )
    for i in range(count // 4):
        base = Change(kind='undercut', product=f'{name} U{i}', competitor='Shop U', url=URL_A, currency='GEL')
        changes.append(
            msgspec.structs.replace(base, new_amount=Decimal('2599'), own_price=Decimal('2699'), pct=Decimal('-3.7'))
        )
    return changes


@pytest.mark.parametrize('language', ['en', 'ru'])
def test__render_digest__busy_day__fits_telegram_footer_and_every_section_kept(language):
    changes = busy_day(60)
    sheet = 'https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
    digest = render_digest(
        changes, client='Acme', language=language, when='2026-10-20 10:00', failed=7, total=90, sheet_url=sheet
    )
    assert digest is not None
    assert len(digest) <= DIGEST_LIMIT
    lines = digest.split('\n')
    # Хвост — всегда: «не проверено N из M» и ссылка на таблицу.
    assert lines[-1].endswith(sheet)
    assert ('7 of 90' if language == 'en' else '7 из 90') in lines[-2]
    # «Дешевле вас» — первым и целиком (их 15), «изменения цен» сокращены с честным «и ещё N».
    titles = [line for line in lines if line.endswith(':') and not line.startswith('•')]
    assert titles == (
        ['Cheaper than you:', 'Price changes:'] if language == 'en' else ['Дешевле вас:', 'Изменения цен:']
    )
    undercuts = [line for line in lines if '— Shop U:' in line and line.startswith('•')]
    prices = [line for line in lines if '#' in line and line.startswith('•')]
    more = [line for line in lines if line.startswith('…')]
    assert len(undercuts) == 15
    assert 0 < len(prices) < 60
    (rest,) = more
    assert str(60 - len(prices)) in rest
    # Ни одна строка не обрезана посередине.
    assert all(line.endswith('%)') for line in undercuts + prices)


def test__render_digest__quiet_day__unchanged_no_more_line():
    changes = busy_day(4)
    digest = render_digest(changes, client='Acme', language='en', when='now', failed=0, total=4, sheet_url=None)
    assert digest is not None
    assert '…' not in digest
    assert digest.split('\n')[2] == 'Cheaper than you:'


def test__render_digest__tiny_limit__sections_shrink_evenly():
    changes = busy_day(8)
    digest = render_digest(
        changes, client='Acme', language='en', when='now', failed=0, total=8, sheet_url=None, limit=500
    )
    assert digest is not None
    assert len(digest) <= 500
    shown = [line for line in digest.split('\n') if line.startswith('•')]
    undercuts = [line for line in shown if '— Shop U:' in line]
    assert len(undercuts) >= 1 and len(shown) - len(undercuts) >= 1  # каждому разделу — хотя бы строка

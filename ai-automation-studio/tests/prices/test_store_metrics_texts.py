import datetime as dt
import ipaddress
import sqlite3
from contextlib import closing
from decimal import Decimal

import pytest

from autostudio.events import Event
from autostudio.prices import texts
from autostudio.prices.extract import Availability, Method, PriceReading
from autostudio.prices.metrics import METRICS, highlights
from autostudio.prices.money import Qualifier
from autostudio.prices.store import OfferKey, OfferState, OutboxEntry, PriceStore
from autostudio.prices.urls import ip_is_public, origin, redact, url_problem

T0 = dt.datetime(2026, 10, 20, 6, 0, tzinfo=dt.UTC)
KEY = OfferKey('p1', 'Shop A', 'https://shop-a.example/p/1')


def reading(amount: str, **kwargs) -> PriceReading:
    return PriceReading(amount=Decimal(amount), currency='GEL', method=Method.JSON_LD, raw=f'price={amount}', **kwargs)


# --- store ---------------------------------------------------------------------------------


def test__store__new_offer__no_reading_zero_state(tmp_path):
    store = PriceStore(tmp_path / 'deep' / 'p.sqlite')
    assert store.last_reading(KEY) is None
    assert store.state(KEY) == OfferState()


def test__store__amount__exact_decimal_round_trip_as_text(tmp_path):
    store = PriceStore(tmp_path / 'p.sqlite')
    store.commit(T0, [(KEY, reading('2749.10', availability=Availability.OUT_OF_STOCK))], [])
    stored = store.last_reading(KEY)
    assert stored is not None
    assert stored.amount == Decimal('2749.10')
    assert str(stored.amount) == '2749.10'
    assert stored.availability is Availability.OUT_OF_STOCK
    assert stored.qualifier is Qualifier.EXACT
    assert stored.at == T0
    with closing(sqlite3.connect(tmp_path / 'p.sqlite')) as conn:
        assert conn.execute('SELECT typeof(amount) FROM readings').fetchone() == ('text',)


def test__store__history__newest_first(tmp_path):
    store = PriceStore(tmp_path / 'p.sqlite')
    store.commit(T0, [(KEY, reading('10.00'))], [])
    store.commit(T0 + dt.timedelta(days=1), [(KEY, reading('11.00'))], [])
    assert [r.amount for r in store.readings(KEY)] == [Decimal('11.00'), Decimal('10.00')]
    other = OfferKey('p1', 'Shop A', 'https://shop-a.example/p/other')
    assert store.last_reading(other) is None


def test__store__state__upserted(tmp_path):
    store = PriceStore(tmp_path / 'p.sqlite')
    store.commit(T0, [], [(KEY, OfferState(failures=2, last_reason='timeout'))])
    store.commit(T0, [], [(KEY, OfferState(failures=0, warning='selector_not_found'))])
    assert store.state(KEY) == OfferState(failures=0, last_reason=None, warning='selector_not_found')


def test__store__naive_time__rejected(tmp_path):
    with pytest.raises(ValueError):
        PriceStore(tmp_path / 'p.sqlite').commit(dt.datetime(2026, 1, 1), [], [])  # noqa: DTZ001


def test__store__own_price_and_pending__round_trip(tmp_path):
    store = PriceStore(tmp_path / 'p.sqlite')
    state = OfferState(failures=0, warning='selector_not_found,multiple_products', pending='30.00 GEL')
    store.commit(T0, [(KEY, reading('95.00'))], [(KEY, state)], own_prices={KEY: Decimal('100.00')})
    stored = store.last_reading(KEY)
    assert stored is not None
    assert stored.own_price == Decimal('100.00')
    assert store.state(KEY) == state
    assert store.state(KEY).warnings == {'selector_not_found', 'multiple_products'}


def test__store__database_from_first_release__columns_added_rows_kept(tmp_path):
    path = tmp_path / 'p.sqlite'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(
            'CREATE TABLE readings (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, product_id TEXT NOT NULL, '
            'competitor TEXT NOT NULL, url TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT, method TEXT NOT NULL, '
            'availability TEXT NOT NULL, qualifier TEXT NOT NULL, raw TEXT NOT NULL);'
            'CREATE TABLE offer_state (product_id TEXT NOT NULL, competitor TEXT NOT NULL, url TEXT NOT NULL, '
            'failures INTEGER NOT NULL DEFAULT 0, last_reason TEXT, warning TEXT, '
            'PRIMARY KEY (product_id, competitor, url));'
        )
        conn.execute(
            'INSERT INTO readings (at, product_id, competitor, url, amount, currency, method, availability, '
            "qualifier, raw) VALUES (?, 'p1', 'Shop A', ?, '10.00', 'GEL', 'json_ld', 'unknown', 'exact', 'x')",
            (T0.isoformat(), KEY.url),
        )
    store = PriceStore(path)
    old = store.last_reading(KEY)
    assert old is not None
    assert (old.amount, old.own_price) == (Decimal('10.00'), None)
    store.commit(T0, [], [(KEY, OfferState(pending='1 GEL'))])
    assert store.state(KEY).pending == '1 GEL'


def test__store__outbox__fifo_until_delivered(tmp_path):
    store = PriceStore(tmp_path / 'p.sqlite')
    rows = [
        OutboxEntry(channel='sheet', tab='History', header=['a'], rows=[['1']]),
        OutboxEntry(channel='owner', text='hi'),
    ]
    store.commit(T0, [], [], outbox=rows)
    pending = store.pending()
    assert [(e.channel, e.tab, e.rows, e.text) for e in pending] == [
        ('sheet', 'History', [['1']], ''),
        ('owner', '', [], 'hi'),
    ]
    store.delivered(pending[0].id)
    assert [e.text for e in store.pending()] == ['hi']


def test__store__flags__set_and_cleared(tmp_path):
    store = PriceStore(tmp_path / 'p.sqlite')
    assert store.flag('x') is None
    store.set_flag('x', '1')
    store.set_flag('x', '2')
    assert store.flag('x') == '2'
    store.set_flag('x', None)
    assert store.flag('x') is None


def test__store__exclusive__second_holder_refused_then_released(tmp_path):
    first, second = PriceStore(tmp_path / 'p.sqlite'), PriceStore(tmp_path / 'p.sqlite')
    with first.exclusive() as taken:
        assert taken
        with second.exclusive() as again:
            assert not again
    with second.exclusive() as after:
        assert after


# --- metrics -------------------------------------------------------------------------------


def test__metrics__specs__prefixed_bilingual_honest_minutes():
    assert all(spec.kind.startswith('prices.') for spec in METRICS)
    assert all({'en', 'ru'} <= spec.label.keys() for spec in METRICS)
    minutes = {spec.kind: spec.minutes_saved for spec in METRICS}
    assert minutes['prices.checked'] == 2.0
    assert sum(minutes.values()) == 2.0  # время экономят проверки, а не тревоги


def event(kind: str, **payload: str) -> Event:
    return Event(at=T0, package='prices', kind=kind, payload=payload)


def test__highlights__no_events__empty():
    assert highlights([], 'en') == []


def test__highlights__month__changes_competitor_expensive():
    events = [
        event('prices.checked', product='iPhone', competitor='A', diff_vs_own_pct='-3.0'),
        event('prices.checked', product='iPhone', competitor='B', diff_vs_own_pct='-1.0'),
        event('prices.checked', product='Galaxy', competitor='A', diff_vs_own_pct='2.0'),
        event('prices.checked', product='Galaxy', competitor='B', diff_vs_own_pct='-5.0'),
        event('prices.checked', product='AirPods', competitor='A', diff_vs_own_pct=''),
        event('prices.changed', competitor='B', pct='-4.0'),
        event('prices.changed', competitor='B', pct='bad'),
        event('prices.changed', competitor='A', pct='5.0'),
        event('prices.undercut', competitor='A'),
        event('prices.undercut', competitor='B'),
        # Последняя проверка месяца важнее первой: iPhone у A стал дороже клиента.
        event('prices.checked', product='iPhone', competitor='B', diff_vs_own_pct='-2.0'),
    ]
    assert highlights(events, 'en') == [
        'Price changes caught: 3',
        'Most active competitor: B — price cuts: 1, cheaper than you: 1 times',
        'Where you were the most expensive: iPhone (the cheapest competitor is 3.0% below you)',
    ]


def test__highlights__one_comparable_competitor__not_most_expensive():
    # Один конкурент дешевле — это «дороже конкурента», а не «дороже всех».
    events = [event('prices.checked', product='AirPods', competitor='B', diff_vs_own_pct='-6.7')]
    assert highlights(events, 'en') == []


def test__highlights__russian__comma_decimal_and_wording():
    events = [
        event('prices.checked', product='AirPods', competitor='A', diff_vs_own_pct='-6.7'),
        event('prices.checked', product='AirPods', competitor='B', diff_vs_own_pct='-2.0'),
    ]
    assert highlights(events, 'ru') == ['Где вы были дороже всех: AirPods (самый дешёвый конкурент на 6,7% ниже вас)']


def test__highlights__russian_and_tie__alphabetical():
    events = [event('prices.undercut', competitor='Б'), event('prices.undercut', competitor='А')]
    assert highlights(events, 'ru') == ['Самый активный конкурент: А — снижений цены: 0, дешевле вас: 1 раз(а)']


# --- texts ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('amount', 'language', 'expected'),
    [
        ('2599.00', 'en', '2,599'),
        ('2599.50', 'en', '2,599.50'),
        ('2599.00', 'ru', '2 599'),
        ('2599.50', 'ru', '2 599,50'),
        ('0.5', 'ka', '0.50'),
    ],
)
def test__show_amount__locale_formats(amount, language, expected):
    assert texts.show_amount(Decimal(amount), language) == expected


@pytest.mark.parametrize(('value', 'expected'), [('-5.55', '−5.6'), ('3.04', '+3.0'), ('0', '0.0')])
def test__show_pct__signed(value, expected):
    assert texts.show_pct(Decimal(value)) == expected


def test__show_pct__russian__comma_like_amounts():
    assert texts.show_pct(Decimal('-5.44'), 'ru') == '−5,4'
    assert texts.show_pct(Decimal('3.04'), 'ru') == '+3,0'
    assert texts.plain_pct(Decimal('6.66'), 'ru') == '6,7'


def test__cells__machine_friendly():
    assert texts.cell_amount(Decimal('2749')) == '2749.00'
    assert texts.cell_amount(Decimal('1.299')) == '1.299'
    assert texts.cell_pct(Decimal('-3.75')) == '-3.8'
    assert texts.cell_pct(Decimal('3.75')) == '3.8'


@pytest.mark.parametrize(
    ('reason', 'expected'),
    [
        ('robots_disallowed', 'skipped: the site asks robots not to visit this page'),
        ('timeout', 'the site did not respond'),
        ('too_large', 'page could not be read'),
        ('selector_not_found', 'price not found on the page'),
        ('llm_unverified', 'price not found on the page'),
        ('llm_budget', 'not checked this time — will be checked on the next run'),
        ('run_timeout', 'not checked this time — will be checked on the next run'),
    ],
)
def test__failure_text__client_words(reason, expected):
    assert texts.failure_text(reason, 'en') == expected


def test__lang__unknown__english():
    assert texts.lang('ka') == 'en'
    assert texts.lang('ru') == 'ru'


def test__texts__every_language_has_every_key():
    for table in (texts.TABS, texts.HEADERS, texts.METHODS, texts.AVAILABILITY, texts.FAILURES, texts.STATUS):
        assert table['en'].keys() == table['ru'].keys()
    for tab in texts.HEADERS['en']:
        assert len(texts.HEADERS['en'][tab]) == len(texts.HEADERS['ru'][tab])
    assert texts.DIGEST['en'].keys() == texts.DIGEST['ru'].keys()
    assert texts.CHANGE_KINDS['en'].keys() == texts.CHANGE_KINDS['ru'].keys()


# --- urls ----------------------------------------------------------------------------------


def test__ip_is_public__mapped_and_multicast():
    assert ip_is_public(ipaddress.ip_address('93.184.215.14'))
    assert not ip_is_public(ipaddress.ip_address('::ffff:10.0.0.1'))
    assert not ip_is_public(ipaddress.ip_address('224.0.0.1'))
    assert not ip_is_public(ipaddress.ip_address('100.64.0.1'))  # CGNAT


def test__redact__credentials_and_query_removed():
    assert redact('https://u:p@shop.example/p?token=SECRET#x') == 'https://shop.example/p'
    assert redact('http://[::1') == '(битый URL)'


def test__origin__port_kept():
    assert origin('https://shop.example:8443/p?q=1') == 'https://shop.example:8443'
    assert origin('https://shop.example/p') == 'https://shop.example'


def test__origin__ipv6_literal__brackets_kept_robots_url_valid():
    site = origin('https://[2606:4700:4700::1111]/p/1')
    assert site == 'https://[2606:4700:4700::1111]'
    assert url_problem(f'{site}/robots.txt') is None


def test__url_problem__public_ok_invalid_rejected():
    assert url_problem('https://shop.example/p') is None
    assert url_problem('https://магазин.example/товар') is None
    assert url_problem('http://[::1') == 'не URL'
    assert url_problem('http://xn--/') == 'не URL'
    assert url_problem('http://exa\nmple.com') == 'не URL'

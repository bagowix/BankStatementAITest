import datetime as dt
from decimal import Decimal

import httpx
import pytest
import respx

from autostudio.errors import ConfigError
from autostudio.office.rates import (
    NBG_URL,
    Rate,
    RateUnavailableError,
    fetch_nbg_rate,
    parse_nbg_response,
    resolve_rate,
)

DAY = dt.date(2026, 10, 14)
TODAY = dt.date(2026, 10, 20)


def _body(*currencies: str, date: str = '2026-10-14T00:00:00.000Z') -> bytes:
    return f'[{{"date": "{date}", "currencies": [{", ".join(currencies)}]}}]'.encode()


USD = '{"code": "USD", "quantity": 1, "rate": 2.7123, "name": "US Dollar", "diff": -0.0012, "validFromDate": "x"}'
JPY = '{"code": "JPY", "quantity": 100, "rate": 1.8234, "name": "Japanese Yen"}'


class MemoryCache:
    def __init__(self) -> None:
        self.rates: dict[tuple[str, dt.date], Rate] = {}

    def get_rate(self, currency: str, on_date: dt.date) -> Rate | None:
        return self.rates.get((currency, on_date))

    def put_rate(self, rate: Rate) -> None:
        self.rates[(rate.currency, rate.on_date)] = rate


def test__parse__usd_with_extra_fields__exact_decimal():
    rate = parse_nbg_response(_body(USD, JPY), 'USD', DAY)
    assert rate.gel_per_unit == Decimal('2.7123')
    assert rate.quantity == Decimal(1)
    assert rate.source_date == DAY
    assert isinstance(rate.gel_per_unit, Decimal)


def test__parse__quantity_100__rate_divided_per_unit():
    rate = parse_nbg_response(_body(USD, JPY), 'JPY', DAY)
    assert rate.published_rate == Decimal('1.8234')
    assert rate.gel_per_unit == Decimal('0.018234')
    # 10 000 иен по 1.8234 за 100 — это 182.34 лари, а не 18 234.
    assert Decimal(10_000) * rate.gel_per_unit == Decimal('182.340000')


def test__parse__numbers_as_strings_and_lowercase_code__tolerated():
    body = _body('{"code": "usd", "quantity": "1", "rate": "2.7123"}', date='2026-10-14')
    assert parse_nbg_response(body, 'USD', DAY).gel_per_unit == Decimal('2.7123')


def test__parse__weekend__last_business_day_rate_accepted():
    rate = parse_nbg_response(_body(USD, date='2026-10-09T00:00:00Z'), 'USD', dt.date(2026, 10, 11))
    assert rate.source_date == dt.date(2026, 10, 9)
    assert rate.on_date == dt.date(2026, 10, 11)


@pytest.mark.parametrize(
    ('body', 'why'),
    [
        (b'<html>maintenance</html>', 'неожиданный ответ'),
        (b'{"date": "2026-10-14", "currencies": []}', 'неожиданный ответ'),
        (b'[]', 'пустой ответ'),
        (b'[{"date": "2026-10-14"}]', 'неожиданный ответ'),
        (b'[{"date": "yesterday", "currencies": []}]', 'непонятная дата'),
        (_body(JPY), 'валюты нет'),
        (_body('{"code": "USD", "quantity": 1, "rate": 0}'), 'неположительный'),
        (_body('{"code": "USD", "quantity": 0, "rate": 2.7}'), 'неположительный'),
        (_body('{"code": "USD", "quantity": 1, "rate": "abc"}'), 'неожиданный ответ'),
        (_body(USD, date='2026-10-15T00:00:00Z'), 'курс на 2026-10-15'),
        (_body(USD, date='2026-09-01T00:00:00Z'), 'курс на 2026-09-01'),
    ],
)
def test__parse__malformed_or_wrong__asks_for_manual_rate(body, why):
    with pytest.raises(RateUnavailableError) as caught:
        parse_nbg_response(body, 'USD', DAY)
    assert why in str(caught.value)
    assert '--rate' in str(caught.value)


@respx.mock
def test__fetch__query_params_and_parse():
    route = respx.get(NBG_URL, params={'currencies': 'USD', 'date': '2026-10-14'}).mock(
        return_value=httpx.Response(200, content=_body(USD))
    )
    with httpx.Client() as client:
        rate = fetch_nbg_rate(client, 'USD', DAY)
    assert route.call_count == 1
    assert rate.gel_per_unit == Decimal('2.7123')


@respx.mock
@pytest.mark.parametrize(
    'outcome',
    [httpx.Response(503, text='busy'), httpx.ConnectError('dns'), httpx.ReadTimeout('slow')],
)
def test__fetch__http_or_network_failure__manual_rate_requested(outcome):
    route = respx.get(NBG_URL)
    if isinstance(outcome, httpx.Response):
        route.mock(return_value=outcome)
    else:
        route.mock(side_effect=outcome)
    with httpx.Client() as client, pytest.raises(RateUnavailableError, match='--rate'):
        fetch_nbg_rate(client, 'USD', DAY)


def test__resolve__gel__always_one_and_no_network():
    cache = MemoryCache()
    lookup = resolve_rate('GEL', DAY, today=TODAY, manual=None, cache=cache, client_factory=None)
    assert (lookup.gel_per_unit, lookup.source) == (Decimal(1), 'gel')
    assert resolve_rate('GEL', DAY, today=TODAY, manual=Decimal(1), cache=cache, client_factory=None).source == 'gel'
    with pytest.raises(ConfigError, match='всегда 1'):
        resolve_rate('GEL', DAY, today=TODAY, manual=Decimal('2.7'), cache=cache, client_factory=None)


def test__resolve__manual__wins_and_not_cached():
    cache = MemoryCache()
    lookup = resolve_rate('USD', DAY, today=TODAY, manual=Decimal('2.70'), cache=cache, client_factory=None)
    assert (lookup.gel_per_unit, lookup.source) == (Decimal('2.70'), 'manual')
    assert cache.rates == {}
    with pytest.raises(ConfigError, match='больше нуля'):
        resolve_rate('USD', DAY, today=TODAY, manual=Decimal(0), cache=cache, client_factory=None)


def test__resolve__future_date__refused_even_with_manual_rate():
    with pytest.raises(ConfigError, match='в будущем'):
        resolve_rate(
            'USD', dt.date(2026, 10, 21), today=TODAY, manual=Decimal('2.7'), cache=MemoryCache(), client_factory=None
        )


@respx.mock
def test__resolve__fetch_then_cache__second_call_offline():
    route = respx.get(NBG_URL).mock(return_value=httpx.Response(200, content=_body(USD)))
    cache = MemoryCache()
    first = resolve_rate('USD', DAY, today=TODAY, manual=None, cache=cache, client_factory=httpx.Client)
    second = resolve_rate('USD', DAY, today=TODAY, manual=None, cache=cache, client_factory=None)
    assert route.call_count == 1
    assert first == second
    assert (second.source, second.source_date) == ('nbg', DAY)


def test__resolve__no_cache_and_fetch_disabled__clear_error():
    with pytest.raises(RateUnavailableError, match='--no-fetch'):
        resolve_rate('USD', DAY, today=TODAY, manual=None, cache=MemoryCache(), client_factory=None)


@respx.mock
def test__resolve__nbg_down__nothing_cached_and_manual_rate_requested():
    respx.get(NBG_URL).mock(side_effect=httpx.ConnectError('down'))
    cache = MemoryCache()
    with pytest.raises(RateUnavailableError, match='--rate'):
        resolve_rate('USD', DAY, today=TODAY, manual=None, cache=cache, client_factory=httpx.Client)
    assert cache.rates == {}

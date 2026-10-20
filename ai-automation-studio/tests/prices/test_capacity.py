from pathlib import Path

import pytest

from autostudio.clientconf import load_client_config
from autostudio.prices.capacity import MAX_REQUESTS_PER_HOUR, capacity, duration_text
from autostudio.prices.config import Offer, PricesConfig, PricesSection, Product, SiteRule

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'prices' / 'client.toml'


def catalog(*sites: tuple[str, int], **section) -> PricesSection:
    """Секция, где у сайта `host` — `count` карточек товаров (по товару на карточку)."""
    products: list[Product] = []
    for host, count in sites:
        for _ in range(count):
            index = len(products)
            url = f'https://{host}/p/{index}'
            products.append(Product(id=f'p{index}', name=f'P{index}', offers=[Offer(competitor='A', url=url)]))
    return PricesSection(products=products, contact_email='ops@studio.example', **section)


def test__capacity__example_config__light_load_no_warnings():
    load = capacity(load_client_config(EXAMPLE, PricesConfig).prices)
    assert [(s.site, s.offers, s.requests, s.pass_s, s.per_hour) for s in load.sites] == [
        ('https://shop-a.example', 3, 4, 20.0, 4),
        ('https://shop-b.example', 3, 4, 20.0, 4),
        ('https://shop-c.example', 3, 4, 20.0, 4),
    ]
    assert load.total_s == 20.0  # три сайта параллельно
    assert load.min_every_hours == 1
    assert load.interval_s is None
    assert load.warnings == ()


def test__capacity__whole_catalog_of_one_shop_hourly__run_limit_and_interval_warned():
    # Бриф 5.8: ежечасный Premium — для страниц категорий и фидов, не для 1 000 карточек одного домена.
    load = capacity(catalog(('big.example', 1000)), every_hours=1)
    (site,) = load.sites
    assert (site.requests, site.pass_s, site.per_hour) == (1001, 5005.0, 720)
    assert len(load.warnings) == 2
    run_limit, interval = load.warnings
    assert run_limit.startswith('big.example: проход по сайту ≈ 83 мин — не уложится в max_run_minutes = 30')
    assert 'около 641 из 1000 ссылок каждый раз будут «в этот раз не проверено»' in run_limit
    assert 'страницы категорий или фиды' in run_limit
    assert 'дольше интервала 1 ч' in interval
    assert interval.endswith('--every-hours 2')
    assert load.min_every_hours == 2


def test__capacity__short_pause__over_polite_hourly_limit_warned():
    load = capacity(catalog(('big.example', 1000), min_delay_s=1, max_run_minutes=60), every_hours=24)
    assert load.sites[0].per_hour == 1001
    assert load.sites[0].per_hour > MAX_REQUESTS_PER_HOUR
    assert load.warnings == (
        'big.example: до 1001 запросов в час при паузе 1 с — больше бережного предела ≈ 720–900 в час '
        '(пауза 4–5 с): поднимите min_delay_s до 4–5 с',
    )


def test__capacity__many_medium_sites__whole_pass_warned_not_each_site():
    sites = [(f's{i}.example', 150) for i in range(20)]
    load = capacity(catalog(*sites, max_run_minutes=60), every_hours=1)
    assert all(site.pass_s == 755.0 for site in load.sites)  # 12.6 мин на сайт — каждый укладывается
    assert load.total_s == 5 * 755.0  # 20 сайтов, по 4 одновременно
    assert load.warnings == (
        'весь проход ≈ 63 мин (сайтов одновременно: 4) — не уложится в max_run_minutes = 60: '
        'поднимите предел или сократите ссылки',
        'весь проход ≈ 63 мин (сайтов одновременно: 4) — дольше интервала 1 ч: --every-hours 2 или меньше ссылок',
    )


@pytest.mark.parametrize(
    ('sites', 'total'),
    [
        ([('slow.example', 19), ('a.example', 1), ('b.example', 1), ('c.example', 1), ('d.example', 1)], 95.0),
        ([('a.example', 1), ('b.example', 1), ('c.example', 1), ('d.example', 1), ('slow.example', 19)], 100.0),
    ],
    ids=['slow-first', 'slow-waits-for-a-slot'],
)
def test__capacity__sites_take_slots_in_config_order__like_the_monitor(sites, total):
    load = capacity(catalog(*sites, respect_robots=False))
    assert load.total_s == total


def test__capacity__paused_site_and_robots_off__not_counted():
    section = catalog(
        ('big.example', 400), ('small.example', 2), respect_robots=False, sites={'big.example': SiteRule(paused=True)}
    )
    load = capacity(section, every_hours=1)
    assert [(s.site, s.requests) for s in load.sites] == [('https://small.example', 2)]
    assert load.warnings == ()


@pytest.mark.parametrize(('seconds', 'text'), [(40.0, '40 с'), (600.0, '10 мин'), (9000.0, '2.5 ч')])
def test__duration_text__seconds_minutes_hours(seconds, text):
    assert duration_text(seconds) == text

from decimal import Decimal
from pathlib import Path

import pytest

from autostudio.clientconf import load_client_config
from autostudio.errors import ConfigError
from autostudio.prices.config import ROBOTS_AGENT, Offer, PricesConfig, PricesSection, Product

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'prices' / 'client.toml'

BASE = """
[client]
id = "acme-shop"
name = "Acme"

[prices]
contact_email = "ops@studio.example"
{extra}

[[prices.products]]
id = "p1"
name = "Phone"
own_price = {own_price}

  [[prices.products.offers]]
  competitor = "Shop A"
  url = "{url}"
"""


def write(write_toml, *, extra: str = '', own_price: str = '"2699.00"', url: str = 'https://shop-a.example/p/1'):
    return write_toml(BASE.format(extra=extra, own_price=own_price, url=url))


def test__example_config__loads_and_is_demo():
    config = load_client_config(EXAMPLE, PricesConfig)
    assert config.client.id == 'demo-prices'
    assert config.prices.offer_count == 9
    assert all(product.own_price is not None for product in config.prices.products)


def test__config__toml_float_price__exact_decimal(write_toml):
    config = load_client_config(write(write_toml, own_price='1299.99'), PricesConfig)
    assert config.prices.products[0].own_price == Decimal('1299.99')


def test__config__defaults__sane(write_toml):
    section = load_client_config(write(write_toml), PricesConfig).prices
    assert section.alert_change_pct == Decimal(3)
    assert section.respect_robots is True
    assert section.llm_fallback is False
    assert section.failure_alert_after == 3


def test__config__user_agent__identifies_operator(write_toml):
    section = load_client_config(write(write_toml), PricesConfig).prices
    agent = section.render_user_agent('0.1.0')
    assert 'ops@studio.example' in agent
    assert ROBOTS_AGENT in agent


@pytest.mark.parametrize(
    ('url', 'message'),
    [
        ('ftp://shop.example/p', 'схема'),
        ('https://user:secret@shop.example/p', 'логин'),
        ('http://127.0.0.1/admin', 'не публичный'),
        ('http://10.0.0.5/p', 'не публичный'),
        ('http://169.254.169.254/latest/meta-data', 'не публичный'),
        ('http://[::1]/', 'не публичный'),
        ('http://[::ffff:127.0.0.1]/', 'не публичный'),
        ('http://localhost:8080/', 'локальное'),
        ('http://printer.local/', 'локальное'),
        ('http://metadata.google.internal/', 'локальное'),
        ('http://router/', 'без домена'),
        ('http://2130706433/', 'числовая'),
        ('http://127.1/', 'числовая'),
        ('http://0x7f.0.0.1/', 'числовая'),
        ('http://exa mple.com/', 'недопустимое'),
        ('https://shop.example:6379/', 'порт'),
        ('https:///nohost', 'нет хоста'),
    ],
)
def test__config__unsafe_url__rejected(write_toml, url, message):
    with pytest.raises(ConfigError, match=message):
        load_client_config(write(write_toml, url=url), PricesConfig)


@pytest.mark.parametrize(
    ('extra', 'message'),
    [
        ('alert_change_pct = 0', 'alert_change_pct'),
        ('alert_change_pct = "nan"', 'alert_change_pct'),
        ('min_delay_s = 0.1', 'min_delay_s'),
        ('timeout_s = 500', 'timeout_s'),
        ('max_retries = 10', 'max_retries'),
        ('max_page_bytes = 100', 'max_page_bytes'),
        ('llm_max_pages_per_run = -1', 'llm_max_pages_per_run'),
        ('failure_alert_after = 0', 'failure_alert_after'),
        ('user_agent = "secret-bot/1.0"', 'contact_email'),
        ('user_agent = "bot {contact_email} {nope}"', 'подстановка'),
        ('user_agent = "bot {contact_email}\\nX-Evil: 1"', 'перевод строки'),
    ],
)
def test__config__bad_numbers_or_agent__rejected(write_toml, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_client_config(write(write_toml, extra=extra), PricesConfig)


def test__config__bad_contact_email__rejected(write_toml):
    text = BASE.format(extra='', own_price='1', url='https://a.example/p').replace('ops@studio.example', 'nobody')
    with pytest.raises(ConfigError, match='contact_email'):
        load_client_config(write_toml(text), PricesConfig)


def test__config__unknown_field__rejected(write_toml):
    with pytest.raises(ConfigError):
        load_client_config(write(write_toml, extra='surprise = 1'), PricesConfig)


def _offer(**kwargs: str) -> Offer:
    return Offer(**{'competitor': 'S', 'url': 'https://s.example/p', **kwargs})


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'competitor': ' '}, 'competitor'),
        ({'competitor': 'a\nb'}, 'competitor'),
        ({'selector': 'div[['}, 'CSS'),
        ({'selector': '  '}, 'пуст'),
        ({'attr': 'content'}, 'без offer.selector'),
        ({'selector': '.p', 'attr': 'bad attr'}, 'атрибут'),
        ({'currency': 'lari'}, 'ISO'),
    ],
)
def test__offer__invalid__rejected(kwargs, message):
    with pytest.raises(ConfigError, match=message):
        _offer(**kwargs)


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'id': 'bad id'}, 'product.id'),
        ({'name': ' '}, 'name'),
        ({'own_price': Decimal(0)}, 'own_price'),
        ({'own_price': Decimal('NaN')}, 'own_price'),
        ({'currency': 'gel'}, 'currency'),
        ({'offers': [_offer(), _offer(url='https://s.example/other')]}, 'дважды'),
    ],
)
def test__product__invalid__rejected(kwargs, message):
    with pytest.raises(ConfigError, match=message):
        Product(**{'id': 'p1', 'name': 'Phone', **kwargs})


def test__section__no_products_or_duplicate_ids__rejected():
    with pytest.raises(ConfigError, match='пуст'):
        PricesSection(products=[], contact_email='a@b.example')
    product = Product(id='p1', name='Phone')
    with pytest.raises(ConfigError, match='повторяются'):
        PricesSection(products=[product, product], contact_email='a@b.example')

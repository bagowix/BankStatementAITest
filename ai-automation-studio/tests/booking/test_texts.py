import datetime as dt
from decimal import Decimal

import pytest

from autostudio.booking.config import placeholders
from autostudio.booking.texts import BOOKINGS_HEADER, DEFAULT_TEXTS, SCHEDULE_HEADER, Texts, plural_ru
from tests.booking.helpers import START, TUE, WED, make_config


@pytest.fixture
def texts(config) -> Texts:
    return Texts(config.booking, config.client.zone)


def test__default_texts__en_and_ru__same_keys_and_placeholders():
    en, ru = DEFAULT_TEXTS['en'], DEFAULT_TEXTS['ru']
    assert set(en) == set(ru)
    mismatched = [key for key in en if placeholders(en[key]) != placeholders(ru[key])]
    assert mismatched == []
    assert len(BOOKINGS_HEADER['en']) == len(BOOKINGS_HEADER['ru'])
    assert len(SCHEDULE_HEADER['en']) == len(SCHEDULE_HEADER['ru'])


def test__customer_texts__short_enough_for_a_phone_screen():
    too_long = [key for lang in DEFAULT_TEXTS for key, text in DEFAULT_TEXTS[lang].items() if len(text) > 260]
    assert too_long == []


@pytest.mark.parametrize(
    ('amount', 'expected'), [('40', '40 GEL'), ('25.50', '25.50 GEL'), ('0.5', '0.50 GEL'), ('1299.00', '1299 GEL')]
)
def test__money__decimal_formatting(amount, expected):
    assert Texts.money(Decimal(amount), 'GEL') == expected


@pytest.mark.parametrize(
    ('minutes', 'lang', 'expected'),
    [(45, 'en', '45 min'), (60, 'en', '1 h'), (90, 'en', '1 h 30 min'), (120, 'ru', '2 ч'), (75, 'ru', '1 ч 15 мин')],
)
def test__duration__labels(minutes, lang, expected):
    assert Texts.duration(minutes, lang) == expected


@pytest.mark.parametrize(
    ('count', 'expected'),
    [(1, '1 день'), (2, '2 дня'), (5, '5 дней'), (11, '11 дней'), (14, '14 дней'), (21, '21 день')],
)
def test__days__russian_plural(count, expected):
    assert Texts.days(count, 'ru') == expected


def test__days__english_plural():
    assert Texts.days(1, 'en') == '1 day' and Texts.days(14, 'en') == '14 days'
    assert plural_ru(112, ('a', 'b', 'c')) == 'c' and plural_ru(102, ('a', 'b', 'c')) == 'b'


def test__dates__localized_labels(texts):
    assert texts.date(WED, 'en') == 'Wed 21 Oct'
    assert texts.date(WED, 'ru') == 'ср, 21 окт'
    assert texts.date(WED, 'ka') == 'Wed 21 Oct'
    assert texts.day_label(TUE, 'ru', TUE) == 'Сегодня'
    assert texts.day_label(WED, 'en', TUE) == 'Tomorrow'
    assert texts.day_label(WED + dt.timedelta(days=1), 'en', TUE) == 'Thu 22 Oct'
    assert texts.when(START, 'en') == 'Tue 20 Oct, 12:30'
    assert texts.time(START) == '12:30'


def test__resolve_language__telegram_codes(texts):
    assert texts.resolve_language('ru-RU') == 'ru'
    assert texts.resolve_language('EN') == 'en'
    assert texts.resolve_language('de') == 'en'
    assert texts.resolve_language(None) == 'en'
    ru_only = make_config(languages=['ru'])
    assert Texts(ru_only.booking, ru_only.client.zone).resolve_language('en') == 'ru'


def test__get__override_and_unknown_language_falls_back(config):
    custom = make_config(texts={'ru': {'booked': 'Записали! №{id}'}})
    texts = Texts(custom.booking, custom.client.zone)
    assert texts.get('ru', 'booked', id='X1', service='', when='', staff='') == 'Записали! №X1'
    assert texts.get('ka', 'ok_short') == 'OK'


def test__names__unknown_ids_fall_back_to_id(texts):
    assert texts.service_name('haircut', 'ru') == 'Стрижка'
    assert texts.service_name('removed', 'ru') == 'removed'
    assert texts.staff_name('nika') == 'Nika' and texts.staff_name('gone') == 'gone'
    assert texts.pick({'en': 'Hi'}, 'ru') == 'Hi'

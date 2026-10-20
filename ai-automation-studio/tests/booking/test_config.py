from decimal import Decimal
from pathlib import Path

import pytest

from autostudio.booking.config import (
    BookingConfig,
    WeeklyHours,
    load_booking_config,
    parse_intervals,
    pick_text,
)
from autostudio.clientconf import load_client_config
from autostudio.errors import ConfigError
from tests.booking.helpers import make_config

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'booking' / 'client.toml'


def test__example_config__loads__demo_id_and_decimal_prices():
    config = load_booking_config(EXAMPLE)
    assert config.client.id == 'demo-booking'
    assert config.booking.services[0].price == Decimal('40')
    assert all(isinstance(service.price, Decimal) for service in config.booking.services)
    assert config.booking.staff_member('nika').hours is not None
    assert config.owner_language == 'en'


def test__load__toml_float_price__decimal_without_float_rounding(write_toml):
    path = write_toml(EXAMPLE.read_text(encoding='utf-8').replace('price = "40"', 'price = 19.99'))
    config = load_booking_config(path)
    assert config.booking.services[0].price == Decimal('19.99')
    assert str(config.booking.services[0].price) == '19.99'


def test__core_loader__same_config__also_valid(write_toml):
    path = write_toml(EXAMPLE.read_text(encoding='utf-8').replace('price = "40"', 'price = 40.5'))
    config = load_client_config(path, BookingConfig)
    assert config.booking.services[0].price == Decimal('40.5')


def test__load__missing_file__config_error(tmp_path):
    with pytest.raises(ConfigError, match='не найден'):
        load_booking_config(tmp_path / 'nope.toml')


def test__load__broken_toml__config_error(write_toml):
    with pytest.raises(ConfigError, match='битый TOML'):
        load_booking_config(write_toml('[client\nid='))


def test__load__unknown_field__config_error(write_toml):
    text = EXAMPLE.read_text(encoding='utf-8').replace('slot_step_min = 15', 'slot_step_min = 15\nfoo = 1')
    with pytest.raises(ConfigError, match='foo'):
        load_booking_config(write_toml(text))


def test__parse_intervals__touching__merged_and_sorted():
    assert parse_intervals(('14:00-18:00', '10:00-14:00')) == ((600, 1080),)
    assert parse_intervals(('20:00-24:00',)) == ((1200, 1440),)


@pytest.mark.parametrize(
    ('raw', 'message'),
    [
        (('10-19',), 'HH:MM-HH:MM'),
        (('19:00-10:00',), 'раньше конца'),
        (('10:00-10:00',), 'раньше конца'),
        (('25:00-26:00',), 'неверное время'),
        (('10:61-11:00',), 'неверное время'),
        (('10:00-24:30',), 'неверное время'),
        (('10:00-14:00', '13:00-18:00'), 'пересекаются'),
    ],
)
def test__parse_intervals__invalid__config_error(raw, message):
    with pytest.raises(ConfigError, match=message):
        parse_intervals(raw)


def test__weekly_hours__bad_day__error_names_the_day():
    with pytest.raises(ConfigError, match='tue'):
        WeeklyHours(tue=['bad'])


def test__weekly_hours__for_weekday__monday_is_zero():
    hours = WeeklyHours(mon=['10:00-11:00'])
    assert hours.for_weekday(0) == ((600, 660),)
    assert hours.for_weekday(6) == ()
    assert not hours.is_empty and WeeklyHours().is_empty


@pytest.mark.parametrize(
    ('override', 'message'),
    [
        ({'services': []}, 'хотя бы одна услуга'),
        ({'staff': []}, 'хотя бы один мастер'),
        ({'hours': {}}, 'ни одного рабочего интервала'),
        ({'languages': []}, 'хотя бы один язык'),
        ({'languages': ['en', 'ka']}, 'ka не поддерживается'),
        ({'languages': ['en', 'en']}, 'повторяются'),
        ({'slot_step_min': 7}, 'slot_step_min'),
        ({'slot_step_min': 240}, 'slot_step_min'),
        ({'min_lead_min': -1}, 'min_lead_min'),
        ({'horizon_days': 0}, 'horizon_days'),
        ({'horizon_days': 365}, 'horizon_days'),
        ({'buffer_min': 500}, 'buffer_min'),
        ({'cancellation_min_before': -5}, 'cancellation_min_before'),
        ({'state_ttl_min': 1}, 'state_ttl_min'),
        ({'reminders_min_before': [120, 120]}, 'reminders_min_before'),
        ({'reminders_min_before': [0]}, 'reminders_min_before'),
        ({'phone_region': 'XX'}, 'phone_region'),
        ({'contact_phone': 'call me'}, 'contact_phone'),
        ({'contact_phone': '+995 55'}, 'contact_phone'),
        ({'max_active_per_customer': 0}, 'max_active_per_customer'),
        ({'max_active_per_customer': 51}, 'max_active_per_customer'),
        ({'phone_region': 'ge'}, 'phone_region'),
        ({'texts': {'en': {'nonexistent': 'x'}}}, 'нет такого текста'),
        ({'texts': {'ka': {'booked': 'x'}}}, 'не поддерживается'),
        ({'texts': {'en': {'booked': 'Done {nmae}'}}}, 'nmae'),
        ({'texts': {'en': {'booked': 'Done {id'}}}, 'битый шаблон'),
    ],
)
def test__booking_section__invalid__config_error(override, message):
    with pytest.raises(ConfigError, match=message):
        make_config(**override)


def test__service__unknown_staff__config_error():
    services = [{'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': '10', 'staff_ids': ['bob']}]
    with pytest.raises(ConfigError, match='неизвестный мастер bob'):
        make_config(services=services)


@pytest.mark.parametrize(
    ('service', 'message'),
    [
        ({'id': 'Cut!', 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': '10'}, 'id'),
        ({'id': 'a' * 25, 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': '10'}, 'id'),
        ({'id': 'cut', 'name': {}, 'duration_min': 30, 'price': '10'}, 'name'),
        ({'id': 'cut', 'name': {'en': ' '}, 'duration_min': 30, 'price': '10'}, 'name'),
        ({'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 0, 'price': '10'}, 'duration_min'),
        ({'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': '-1'}, 'price'),
        ({'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': '10.123'}, 'price'),
        ({'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': 'NaN'}, 'price'),
        ({'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': '10', 'currency': 'gel'}, 'currency'),
        ({'id': 'cut', 'name': {'ru': 'Стрижка'}, 'duration_min': 30, 'price': '10'}, 'языке по умолчанию'),
    ],
)
def test__service__invalid__config_error(service, message):
    with pytest.raises(ConfigError, match=message):
        make_config(services=[service])


def test__service__duration_not_multiple_of_step__allowed():
    config = make_config(services=[{'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 40, 'price': '10'}])
    assert config.booking.services[0].duration_min == 40


@pytest.mark.parametrize(
    ('staff', 'message'),
    [
        ([{'id': 'Bob Smith', 'name': 'Bob'}], 'id'),
        ([{'id': 'bob', 'name': ' '}], 'пустое имя'),
        ([{'id': 'bob', 'name': 'Bob'}, {'id': 'bob', 'name': 'Bobby'}], 'повторяется'),
    ],
)
def test__staff__invalid__config_error(staff, message):
    with pytest.raises(ConfigError, match=message):
        make_config(staff=staff, services=[{'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': '1'}])


@pytest.mark.parametrize(
    ('faq', 'message'),
    [
        ([{'id': 'Bad Id', 'question': {'en': 'q'}, 'answer': {'en': 'a'}}], 'id'),
        ([{'id': 'x', 'question': {}, 'answer': {'en': 'a'}}], 'question и answer'),
        ([{'id': 'x', 'question': {'ru': 'в'}, 'answer': {'ru': 'о'}}], "языке 'en'"),
        (
            [{'id': 'x', 'question': {'en': 'q'}, 'answer': {'en': 'a'}}] * 2,
            'повторяется',
        ),
    ],
)
def test__faq__invalid__config_error(faq, message):
    with pytest.raises(ConfigError, match=message):
        make_config(faq=faq)


def test__lookups__unknown_ids__none(config):
    section = config.booking
    assert section.service('nope') is None
    assert section.staff_member('nope') is None
    assert section.faq_item('address') is not None and section.faq_item('nope') is None
    assert [m.id for m in section.eligible_staff(section.service('combo'))] == ['giorgi']
    assert [m.id for m in section.eligible_staff(section.service('haircut'))] == ['giorgi', 'nika']
    assert section.default_language == 'en'


def test__owner_language__unsupported_client_language__english():
    assert make_config(client={'language': 'ka'}).owner_language == 'en'
    assert make_config(client={'language': 'ru'}).owner_language == 'ru'


def test__pick_text__missing_language__fallbacks():
    assert pick_text({'en': 'Hi', 'ru': 'Привет'}, 'ru', 'en') == 'Привет'
    assert pick_text({'en': 'Hi'}, 'ru', 'en') == 'Hi'
    assert pick_text({'ka': 'გამარჯობა'}, 'ru', 'en') == 'გამარჯობა'
    assert pick_text({}, 'ru', 'en') == ''


def test__booking_config__owner_channel__admins_or_real_notify_channel():
    import msgspec

    from autostudio.clientconf import NotifyConfig

    assert make_config().has_owner_channel
    lonely = make_config(admin_chat_ids=[])
    assert not lonely.has_owner_channel
    assert not msgspec.structs.replace(lonely, notify=NotifyConfig(console=True)).has_owner_channel
    assert msgspec.structs.replace(lonely, notify=NotifyConfig(webhook_url_env='HOOK')).has_owner_channel
    assert make_config(contact_phone='599 12 34 56').booking.contact_phone == '599 12 34 56'  # местный формат GE


@pytest.mark.parametrize(
    ('section', 'message'),
    [
        ({'calendar': {}}, 'нужен calendar_id'),
        ({'calendar': {'calendar_id': 'a b@x'}}, 'не похоже на id календаря'),
        ({'calendar': {'calendar_id': 's@x', 'cache_ttl_s': 5}}, 'cache_ttl_s'),
        ({'calendar': {'calendar_id': 's@x', 'credentials_env': '/path/key.json'}}, 'имя переменной'),
        ({'calendar': {'staff': {'ghost': 'g@x'}}}, 'неизвестный мастер ghost'),
        ({'whatsapp': {'phone_number_id': '+995 555 12 34 56'}}, 'числовой ID номера'),
        ({'whatsapp': {'phone_number_id': '123456789', 'graph_version': '25'}}, 'graph_version'),
        ({'whatsapp': {'phone_number_id': '123456789', 'token_env': 'EAAB...'}}, 'имя переменной'),
        ({'whatsapp': {'phone_number_id': '123456789', 'reminder_template': 'Reminder!'}}, 'имя шаблона'),
        ({'whatsapp': {'phone_number_id': '123456789', 'template_languages': {'ka': 'ka'}}}, 'template_languages'),
        ({'whatsapp': {'phone_number_id': '123456789', 'template_languages': {'en': 'English'}}}, 'template_languages'),
        ({'telegram': {'mode': 'webhook'}}, 'public_url'),
        ({'telegram': {'webhook_secret_env': 'x y'}}, 'имя переменной'),
        ({'server': {'public_url': 'http://bot.example.test'}}, 'только https'),
        ({'server': {'port': 70000}}, 'port'),
        ({'server': {'port_env': '8080'}}, 'имя переменной'),
    ],
)
def test__channel_sections__invalid__config_error(section, message):
    with pytest.raises(ConfigError, match=message):
        make_config(**section)


def test__channel_sections__valid__defaults_and_lookups():
    config = make_config(
        calendar={'calendar_id': 'salon@x', 'staff': {'giorgi': 'giorgi@x'}},
        whatsapp={
            'phone_number_id': '123456789',
            'reminder_template': 'booking_reminder',
            'template_languages': {'en': 'en_US'},
        },
        telegram={'mode': 'webhook'},
        server={'public_url': 'https://bot.example.test/'},
    )
    section = config.booking
    assert section.calendar is not None and section.whatsapp is not None
    assert section.calendar.calendar_for('giorgi') == 'giorgi@x' and section.calendar.calendar_for('nika') == 'salon@x'
    assert section.calendar.calendars == ['giorgi@x', 'salon@x']
    assert section.whatsapp.template_language('en') == 'en_US' and section.whatsapp.template_language('ru') == 'ru'
    assert section.whatsapp.graph_version == 'v25.0'
    assert section.server.base_url == 'https://bot.example.test' and section.needs_server
    plain = make_config().booking
    assert plain.telegram.mode == 'polling' and not plain.needs_server and plain.server.base_url is None
    staff_only = make_config(calendar={'staff': {'nika': 'nika@x'}}).booking.calendar
    assert staff_only is not None and staff_only.calendar_for('giorgi') is None

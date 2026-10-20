import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import msgspec
import pytest

from autostudio.errors import ConfigError
from autostudio.office.invoice import (
    PRESET_WORDING,
    ClientParty,
    Invoice,
    InvoiceDraft,
    LineItem,
    load_invoice_request,
    md_text,
    parse_item,
    period_suffix,
    preset_item,
    render_html,
    render_markdown,
    validate_draft,
)
from tests.office.helpers import make_draft

HOSTILE = '<script>alert("x")</script> & <b>Co</b>'


def _invoice(items: list[LineItem], **changes: object) -> Invoice:
    base = InvoiceDraft(
        client=ClientParty(name='Example Corp', address='1 Example Way\nSpringfield', country='US'),
        items=items,
        currency='USD',
        issue_date=dt.date(2026, 10, 20),
    ).numbered('AS-2026-001')
    return msgspec.structs.replace(base, **changes) if changes else base


def test__total__decimal_lines__exact_sum_no_float():
    # 0.1 + 0.2 во float — 0.30000000000000004; в счёте должно быть ровно 0.30.
    invoice = _invoice(
        [
            LineItem(description='a', unit_price=Decimal('0.1')),
            LineItem(description='b', unit_price=Decimal('0.2')),
        ]
    )
    assert invoice.total == Decimal('0.30')
    assert isinstance(invoice.total, Decimal)
    assert all(isinstance(item.amount, Decimal) for item in invoice.items)


def test__line_amount__fractional_quantity__rounded_half_up_per_line():
    item = LineItem(description='hours', quantity=Decimal('3'), unit_price=Decimal('0.335'))
    assert item.amount == Decimal('1.01')  # 1.005 → 1.01, а не банковские 1.00
    hours = LineItem(description='hours', quantity=Decimal('1.5'), unit_price=Decimal('40'))
    assert hours.amount == Decimal('60.00')


def test__numbered__due_date__issue_plus_due_days():
    draft = msgspec.structs.replace(make_draft(), due_days=7)
    invoice = draft.numbered('AS-2026-007')
    assert invoice.due_date == dt.date(2026, 10, 27)
    assert invoice.number == 'AS-2026-007'


def test__payload__json_roundtrip__money_as_strings_not_floats():
    invoice = _invoice([LineItem(description='x', quantity=Decimal('1.5'), unit_price=Decimal('40.10'))])
    raw = msgspec.json.encode(invoice)
    assert b'"40.10"' in raw and b'"1.5"' in raw
    assert msgspec.json.decode(raw, type=Invoice) == invoice


@pytest.mark.parametrize(
    'description',
    ['IT consulting', 'Consultancy services', 'Консультационные услуги', 'კონსულტაცია'],
)
def test__validate__consulting_wording__refused(description):
    draft = msgspec.structs.replace(make_draft(), items=[LineItem(description=description, unit_price=Decimal(1))])
    with pytest.raises(ConfigError, match='консалтинг'):
        validate_draft(draft)


@pytest.mark.parametrize(
    ('changes', 'message'),
    [
        ({'items': []}, 'нет позиций'),
        ({'client': ClientParty(name='  ')}, 'наименование'),
        ({'client': ClientParty(name='X', country='usa')}, 'ISO 3166'),
        ({'due_days': 400}, 'срок оплаты'),
        ({'items': [LineItem(description='x', unit_price=Decimal(0))]}, 'ноль'),
        ({'items': [LineItem(description=' ', unit_price=Decimal(1))]}, 'без описания'),
        ({'items': [LineItem(description='x', quantity=Decimal(0), unit_price=Decimal(1))]}, 'количество'),
        ({'items': [LineItem(description='x', unit_price=Decimal(-1))]}, 'отрицательной'),
    ],
)
def test__validate__bad_draft__config_error(changes, message):
    with pytest.raises(ConfigError, match=message):
        validate_draft(msgspec.structs.replace(make_draft(), **changes))


def test__preset_item__default_wording__never_consulting():
    for language in ('en', 'ru'):
        for preset in ('setup', 'support'):
            item = preset_item(preset, '99', language)
            assert item.description == PRESET_WORDING[language][preset]
            assert 'consult' not in item.description.lower()
            assert 'консульт' not in item.description.lower()
    assert 'Software development, configuration and integration services' in PRESET_WORDING['en']['setup']
    assert 'maintenance' in PRESET_WORDING['en']['support']


def test__parse_item__forms__quantity_and_price():
    assert parse_item('Extra hours;1.5;40') == LineItem(
        description='Extra hours', quantity=Decimal('1.5'), unit_price=Decimal('40')
    )
    assert parse_item('Bot texts;120').quantity == Decimal(1)
    # Точка с запятой в описании: справа — число и цена, середина не число — значит описание.
    item = parse_item('Setup; Telegram;250')
    assert item.description == 'Setup; Telegram'
    weird = parse_item('a;b;100')
    assert weird.description == 'a;b' and weird.unit_price == Decimal(100)
    with pytest.raises(ConfigError, match='описание'):
        parse_item('no price')


def test__period_suffix__en_and_ru():
    assert period_suffix(dt.date(2026, 11, 1), 'en') == ' — November 2026'
    assert period_suffix(dt.date(2026, 11, 1), 'ru') == ' — ноябрь 2026'


def test__render_html__client_text__escaped(operator):
    invoice = _invoice(
        [LineItem(description=HOSTILE, unit_price=Decimal('10'))],
        client=ClientParty(name=HOSTILE, address=HOSTILE, tax_id=HOSTILE, email=HOSTILE),
        notes=HOSTILE,
    )
    page = render_html(invoice, operator)
    assert '<script>' not in page
    assert '<b>Co</b>' not in page
    assert '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;b&gt;Co&lt;/b&gt;' in page
    # Внешних ресурсов нет: файл печатается без сети.
    assert not re.search(r'(src|href)=|@import|url\(', page)


def test__render_html__payment_block_and_print_css(operator):
    invoice = _invoice([LineItem(description='x', unit_price=Decimal('1250.5'))])
    page = render_html(invoice, operator)
    assert page.startswith('<!doctype html>')
    assert '@page { size: A4' in page
    assert 'GE07 XX00 0000 0000 0000 00' in page
    assert 'XXXXGE22' in page
    assert 'JSC Example Bank' in page
    assert 'Payment reference' in page and page.count('AS-2026-001') >= 3
    assert 'USD 1,250.50' in page
    assert 'Small Business status holder' in page
    assert '1 Example Way<br>Springfield' in page
    assert 'class="stamp"' not in page


def test__render_html__russian_and_void__labels_and_stamp(operator):
    invoice = _invoice([LineItem(description='x', unit_price=Decimal('1250.5'))], language='ru', currency='GEL')
    page = render_html(invoice, operator, status='void')
    assert 'Счёт на оплату' in page
    assert 'ИП Джейн Пример' in page
    assert '1 250,50 GEL' in page
    assert 'АННУЛИРОВАН' in page
    assert 'НДС не начисляется' in page


def test__render_markdown__summary__escaped_and_complete(operator):
    invoice = _invoice(
        [LineItem(description='Bot | texts', quantity=Decimal(2), unit_price=Decimal('10'))],
        client=ClientParty(name=HOSTILE),
        notes='Thanks',
    )
    text = render_markdown(invoice, operator, status='void')
    assert '<script>' not in text
    assert 'Bot \\| texts' in text
    assert '| 1 | Bot \\| texts | 2 | 10.00 | 20.00 |' in text
    assert '**USD 20.00**' in text
    assert 'VOID' in text
    assert '`AS-2026-001`' in text
    assert 'Notes: Thanks' in text


def test__md_text__newlines_and_html__flattened():
    assert md_text('a\nb <i>') == 'a b &lt;i&gt;'


def test__load_invoice_request__presets_and_decimals(tmp_path):
    path = tmp_path / 'invoice.toml'
    path.write_text(
        'currency = "eur"\nlanguage = "ru"\nissue_date = 2026-11-01\ndue_days = 10\nnotes = "Спасибо"\n'
        '[client]\nname = "Example GmbH"\ncountry = "DE"\n'
        '[[items]]\npreset = "setup"\nunit_price = 600.10\n'
        '[[items]]\npreset = "support"\nquantity = 2\nunit_price = "99"\n'
        '[[items]]\ndescription = "Extra"\nunit_price = 0.1\n',
        encoding='utf-8',
    )
    request = load_invoice_request(path)
    draft = request.draft(default_currency='USD', today=dt.date(2026, 10, 20))
    assert draft.currency == 'EUR'
    assert draft.issue_date == dt.date(2026, 11, 1)
    assert draft.items[0].description == PRESET_WORDING['ru']['setup']
    assert draft.items[0].unit_price == Decimal('600.10')
    assert draft.items[1].quantity == Decimal(2)
    assert draft.items[2].unit_price == Decimal('0.1')
    validate_draft(draft)


def test__load_invoice_request__defaults_and_errors(tmp_path):
    path = tmp_path / 'invoice.toml'
    path.write_text('[client]\nname = "X"\n[[items]]\nunit_price = 5\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='description или preset'):
        load_invoice_request(path).draft(default_currency='GEL', today=dt.date(2026, 10, 20))
    with pytest.raises(ConfigError, match='не найден'):
        load_invoice_request(tmp_path / 'missing.toml')
    path.write_text('[client\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='битый TOML'):
        load_invoice_request(path)
    path.write_text('extra = 1\nitems = []\n[client]\nname = "X"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='extra'):
        load_invoice_request(path)


def test__load_invoice_request__default_currency_and_today(tmp_path: Path):
    path = tmp_path / 'invoice.toml'
    path.write_text('[client]\nname = "X"\n[[items]]\ndescription = "d"\nunit_price = 5\n', encoding='utf-8')
    draft = load_invoice_request(path).draft(default_currency='GEL', today=dt.date(2026, 10, 20))
    assert draft.currency == 'GEL'
    assert draft.issue_date == dt.date(2026, 10, 20)
    assert draft.language == 'en'

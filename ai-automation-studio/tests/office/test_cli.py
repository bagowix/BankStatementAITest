import datetime as dt
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import httpx
import msgspec
import pytest
import respx

from autostudio import cli as root_cli
from autostudio.errors import ConfigError
from autostudio.office import cli
from autostudio.office.contract import ContractIncompleteError
from autostudio.office.rates import NBG_URL, RateUnavailableError
from autostudio.office.store import OfficeStore
from tests.office.helpers import OPERATOR_TOML, TEST_BANK, TEST_NAME

REPO_ROOT = Path(__file__).resolve().parents[2]

NOW = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC)
NBG_USD = b'[{"date": "2026-10-14T00:00:00.000Z", "currencies": [{"code": "USD", "quantity": 1, "rate": 2.7123}]}]'
TEMPLATE = """# Support Terms

> **Template. Not legal advice.** Fill `{{double braces}}` and `[square brackets]`.

Agreement No. [number] dated [date] between {{NAME}} and [Client's legal name].
Report by the [5th] Business Day. [VERIFY: backups.]
"""


@pytest.fixture
def office(tmp_path: Path, operator_file: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / 'office-data'
    monkeypatch.setenv('AUTOSTUDIO_OPERATOR', str(operator_file))
    monkeypatch.setenv('AUTOSTUDIO_OFFICE_DIR', str(data))
    monkeypatch.setattr(cli, '_now', lambda: NOW)
    OfficeStore(data / 'office.sqlite').init()
    return data


def _issue(**overrides: object) -> None:
    values: dict[str, object] = {'client_name': 'Example Corp', 'client_country': 'us', 'setup': '600'}
    values.update(overrides)
    cli.invoice_new(**values)


def test__office_dir__flag_env_default(monkeypatch, tmp_path):
    monkeypatch.delenv('AUTOSTUDIO_OFFICE_DIR', raising=False)
    monkeypatch.chdir(tmp_path)
    # Не от текущего каталога: из любой папки — тот же реестр в корне репозитория.
    assert cli.office_dir(None) == REPO_ROOT / 'var' / 'office'
    monkeypatch.setenv('AUTOSTUDIO_OFFICE_DIR', '/srv/office')
    assert cli.office_dir(None) == Path('/srv/office')
    assert cli.office_dir(Path('x')) == Path('x')


def test__invoice__flags__registry_html_and_markdown(office, capsys):
    cli.invoice_new(
        client_name='Acme <b>Ltd</b>',
        client_address='1 Example Way',
        client_tax_id='DE000',
        client_email='ap@example.org',
        client_country='de',
        setup='600',
        support='99',
        months=2,
        period='2026-11',
        item=['Extra hours;1.5;40'],
        currency='eur',
        notes='Thank you',
    )
    out = capsys.readouterr().out
    assert 'счёт AS-2026-001 выставлен' in out
    assert 'EUR 858.00' in out  # 600 + 2×99 + 1.5×40
    html = (office / 'invoices' / 'AS-2026-001.html').read_text(encoding='utf-8')
    assert 'Acme &lt;b&gt;Ltd&lt;/b&gt;' in html and '<b>Ltd</b>' not in html
    assert 'Technical support and maintenance of software (monthly) — November 2026' in html
    summary = (office / 'invoices' / 'AS-2026-001.md').read_text(encoding='utf-8')
    assert '| 2 | Technical support' in summary and '| 2 | 99.00 | 198.00 |' in summary
    [record] = OfficeStore(office / 'office.sqlite').list_invoices()
    assert record.invoice.total == Decimal('858.00')
    assert record.invoice.client.country == 'DE'
    assert record.invoice.issue_date == dt.date(2026, 10, 20)  # сегодня в Тбилиси


def test__invoice__georgian_client_in_usd_and_future_date__notes(office, capsys):
    _issue(client_country='GE', date=dt.date(2026, 10, 25))
    out = capsys.readouterr().out
    assert 'местные клиенты платят в лари' in out
    assert 'дата счёта в будущем' in out
    assert f'реестр: {office.resolve() / "office.sqlite"}' in out


def test__invoice__typo_dates__refused_before_number_or_allowed_explicitly(office, capsys):
    with pytest.raises(ConfigError, match='--allow-date'):
        _issue(date=dt.date(2062, 10, 10))
    _issue(date=dt.date(2026, 10, 1))
    with pytest.raises(ConfigError, match='раньше последнего счёта серии'):
        _issue(date=dt.date(2026, 3, 1))
    _issue(date=dt.date(2026, 3, 1), allow_date=True)
    numbers = [(r.invoice.number, r.invoice.issue_date) for r in OfficeStore(office / 'office.sqlite').list_invoices()]
    assert numbers == [('AS-2026-001', dt.date(2026, 10, 1)), ('AS-2026-002', dt.date(2026, 3, 1))]


def test__invoice__several_support_months__period_range_in_line(office):
    _issue(setup=None, support='99', months=3, period='2026-11')
    html = (office / 'invoices' / 'AS-2026-001.html').read_text(encoding='utf-8')
    assert '(monthly) — November 2026 – January 2027' in html
    with pytest.raises(ConfigError, match='--months: от 1 до 24'):
        _issue(setup=None, support='99', months=25, period='2026-11')


def test__invoice__absurd_numbers__config_error_not_traceback(office):
    with pytest.raises(ConfigError, match='12 цифр'):
        _issue(setup=None, item=['x;1;99999999999999999999999999999'])
    with pytest.raises(ConfigError, match='2 знаков'):
        _issue(setup=None, item=['Hours;3;33.333'])
    with pytest.raises(ConfigError, match='2000'):
        cli.invoice_list(year=0)
    with pytest.raises(ConfigError, match='2000'):
        cli.ledger_export(year=0)
    with pytest.raises(ConfigError, match='12 цифр'):
        cli.ledger_add(
            amount='999999999999999999999999999999', currency='GEL', channel='bank', counterparty='X', country='GE'
        )
    assert OfficeStore(office / 'office.sqlite').list_invoices() == []


def test__invoice__from_file__lang_and_overrides(office, tmp_path, capsys):
    request = tmp_path / 'invoice.toml'
    request.write_text(
        'currency = "USD"\n[client]\nname = "Example GmbH"\ncountry = "de"\n'
        '[[items]]\npreset = "support"\nunit_price = 99\n',
        encoding='utf-8',
    )
    cli.invoice_new(
        from_file=request, lang='ru', currency='GEL', due_days=14, notes='Спасибо', date=dt.date(2026, 10, 1)
    )
    record = OfficeStore(office / 'office.sqlite').get_invoice('AS-2026-001')
    invoice = record.invoice
    assert invoice.language == 'ru' and invoice.currency == 'GEL'
    assert invoice.items[0].description.startswith('Техническая поддержка')
    assert invoice.due_date == dt.date(2026, 10, 15)
    assert invoice.notes == 'Спасибо'
    assert invoice.client.country == 'DE'
    with pytest.raises(ConfigError, match='не смешивайте'):
        cli.invoice_new(from_file=request, setup='100')


def test__invoice__no_client_or_bad_months__refused_without_number(office):
    with pytest.raises(ConfigError, match='--client-name'):
        cli.invoice_new(setup='100')
    with pytest.raises(ConfigError, match='--months'):
        _issue(support='99', months=0)
    with pytest.raises(ConfigError, match='--support'):
        _issue(period='2026-11')
    with pytest.raises(ConfigError, match='ISO 3166'):
        _issue(client_country='USA')
    _issue()
    assert OfficeStore(office / 'office.sqlite').list_invoices()[0].invoice.number == 'AS-2026-001'


def test__invoice_list__statuses(office, capsys):
    cli.invoice_list()
    assert 'счетов нет' in capsys.readouterr().out
    _issue()
    _issue(client_name='Second')
    _issue(client_name='Third')
    cli.invoice_void('AS-2026-002', reason='duplicate')
    cli.invoice_paid('AS-2026-003', date=dt.date(2026, 10, 14), rate='2.70')
    capsys.readouterr()
    cli.invoice_list(year=2026)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith('AS-2026-001') and 'выставлен, срок 2026-10-27' in lines[0]
    assert 'АННУЛИРОВАН: duplicate' in lines[1]
    assert 'оплачен 2026-10-14' in lines[2]


def test__invoice_void__file_rewritten_with_stamp(office, capsys):
    _issue()
    cli.invoice_void('AS-2026-001', reason='wrong amount')
    assert 'аннулирован' in capsys.readouterr().out
    html = (office / 'invoices' / 'AS-2026-001.html').read_text(encoding='utf-8')
    assert 'class="stamp">VOID<' in html
    _issue()
    assert (office / 'invoices' / 'AS-2026-002.html').exists()


def test__invoice_void_and_show__after_profile_change__issued_details_kept(office, tmp_path, monkeypatch):
    _issue()
    moved = tmp_path / 'operator-new.toml'
    moved.write_text(
        OPERATOR_TOML.replace(TEST_BANK, 'JSC Other Bank').replace('Test Street 1', 'New Street 99'), encoding='utf-8'
    )
    monkeypatch.setenv('AUTOSTUDIO_OPERATOR', str(moved))
    cli.invoice_void('AS-2026-001', reason='wrong amount')
    html = (office / 'invoices' / 'AS-2026-001.html').read_text(encoding='utf-8')
    assert TEST_BANK in html and 'Test Street 1' in html
    assert 'Other Bank' not in html and 'New Street 99' not in html
    cli.invoice_show('AS-2026-001', html=True)
    assert 'Other Bank' not in (office / 'invoices' / 'AS-2026-001.md').read_text(encoding='utf-8')
    _issue()
    assert 'JSC Other Bank' in (office / 'invoices' / 'AS-2026-002.html').read_text(encoding='utf-8')


def test__invoice_show__invoice_without_snapshot__current_profile_with_warning(office, capsys):
    _issue()
    store = OfficeStore(office / 'office.sqlite')
    record = store.get_invoice('AS-2026-001')
    legacy = msgspec.json.encode(msgspec.structs.replace(record.invoice, issuer=None)).decode()
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("UPDATE invoices SET payload = ? WHERE number = 'AS-2026-001'", (legacy,))
    capsys.readouterr()
    cli.invoice_show('AS-2026-001')
    out = capsys.readouterr().out
    assert 'нет снимка реквизитов' in out and TEST_BANK in out


def test__invoice_show__markdown_and_html(office, capsys):
    _issue()
    (office / 'invoices' / 'AS-2026-001.html').unlink()
    cli.invoice_show('AS-2026-001', html=True)
    out = capsys.readouterr().out
    assert '# Invoice AS-2026-001' in out
    assert (office / 'invoices' / 'AS-2026-001.html').exists()


def test__invoice_paid__defaults_from_invoice__receipt_in_ledger(office, capsys):
    _issue()
    cli.invoice_paid('AS-2026-001', date=dt.date(2026, 10, 14), rate='2.7123', fee='12.50', channel='wise')
    out = capsys.readouterr().out
    assert 'счёт AS-2026-001 оплачен' in out
    assert '600.00 USD × 2.7123 = 1627.38 GEL (курс вручную), wise' in out
    [receipt] = OfficeStore(office / 'office.sqlite').receipts()
    assert receipt.invoice_number == 'AS-2026-001'
    assert receipt.client_country == 'US'
    assert receipt.platform_fee == Decimal('12.50')
    with pytest.raises(ConfigError, match='статус paid'):
        cli.invoice_paid('AS-2026-001', rate='2.7')


def test__invoice_paid__other_currency_without_amount__refused(office, capsys):
    # Сумма USD-счёта не может молча стать той же цифрой в лари: оборот занизился бы в 2.7 раза.
    _issue(setup='450')
    with pytest.raises(ConfigError, match='укажите --amount'):
        cli.invoice_paid('AS-2026-001', date=dt.date(2026, 10, 14), currency='GEL')
    assert OfficeStore(office / 'office.sqlite').receipts() == []
    cli.invoice_paid('AS-2026-001', date=dt.date(2026, 10, 14), currency='GEL', amount='1215.00')
    assert 'другой валюте' in capsys.readouterr().out
    [receipt] = OfficeStore(office / 'office.sqlite').receipts()
    assert (receipt.amount, receipt.currency, receipt.gel_amount) == (Decimal('1215.00'), 'GEL', Decimal('1215.00'))


def test__ledger_add_then_invoice_paid__one_receipt_not_two(office, capsys):
    _issue(setup='1000')
    cli.ledger_add(
        amount='1000',
        currency='USD',
        channel='bank',
        counterparty='Example Corp',
        country='US',
        date=dt.date(2026, 10, 10),
        rate='2.70',
        invoice='AS-2026-001',
    )
    out = capsys.readouterr().out
    assert 'invoice paid AS-2026-001 (без --amount: только статус' in out
    cli.invoice_paid('AS-2026-001')
    out = capsys.readouterr().out
    assert 'отмечен оплаченным 2026-10-10' in out and 'новой записи нет' in out
    store = OfficeStore(office / 'office.sqlite')
    assert len(store.receipts()) == 1
    assert store.get_invoice('AS-2026-001').status == 'paid'
    cli.ledger_month('2026-10')
    month = capsys.readouterr().out
    assert 'Оборот месяца: **2 700.00 GEL**' in month
    assert 'Налог к уплате за месяц (оценка): **27.00 GEL**' in month


def test__ledger_add_partial_then_invoice_paid__flags_and_top_up(office, capsys):
    _issue(setup='1000')
    common = {'currency': 'USD', 'channel': 'bank', 'counterparty': 'Example Corp', 'country': 'US', 'rate': '2.70'}
    cli.ledger_add(amount='400', date=dt.date(2026, 10, 10), invoice='AS-2026-001', **common)
    assert 'получено 400.00, а в счёте 1000.00 USD' in capsys.readouterr().out
    with pytest.raises(ConfigError, match='добавьте --amount'):
        cli.invoice_paid('AS-2026-001', rate='2.70')
    with pytest.raises(ConfigError, match='в будущем'):
        cli.invoice_paid('AS-2026-001', date=dt.date(2026, 10, 21))
    # Доплата остатка — явной суммой, отдельной записью.
    cli.invoice_paid('AS-2026-001', amount='600', date=dt.date(2026, 10, 14), rate='2.70')
    assert 'внимание' not in capsys.readouterr().out
    amounts = [r.amount for r in OfficeStore(office / 'office.sqlite').receipts()]
    assert amounts == [Decimal('400.00'), Decimal('600.00')]


def test__ledger_add__invoice_already_paid__summary_without_paid_hint(office, capsys):
    # Путь из docs/office.md, раздел 9: первая часть — invoice paid --amount, остаток — ledger add --invoice.
    _issue(setup='1000')
    cli.invoice_paid('AS-2026-001', amount='400', date=dt.date(2026, 10, 10), rate='2.70')
    assert 'внимание: получено 400.00, а в счёте 1000.00 USD: не хватает 600.00' in capsys.readouterr().out
    cli.ledger_add(
        amount='600',
        currency='USD',
        channel='bank',
        counterparty='Example Corp',
        country='US',
        date=dt.date(2026, 10, 14),
        rate='2.70',
        invoice='AS-2026-001',
    )
    out = capsys.readouterr().out
    assert 'записано #2' in out and 'invoice paid' not in out
    assert 'по счёту AS-2026-001: получено 1000.00 USD — ровно сумма счёта (2 зап.)' in out


def test__invoice_paid_then_ledger_add_same_amount__refused_or_warned(office, capsys):
    # Обратный порядок к ledger add → invoice paid: invoice paid уже записал поступление,
    # и та же банковская проводка через ledger add --invoice удвоила бы оборот и налог.
    _issue(client_name='Beta LLC', setup='1000', date=dt.date(2026, 10, 10))
    cli.invoice_paid('AS-2026-001', date=dt.date(2026, 10, 14), currency='usd', rate='2.7', fetch=False)
    assert 'по счёту AS-2026-001: получено 1000.00 USD — ровно сумма счёта' in capsys.readouterr().out
    again = {
        'amount': '1000',
        'currency': 'USD',
        'channel': 'bank',
        'counterparty': 'Beta LLC',
        'country': 'US',
        'invoice': 'AS-2026-001',
        'rate': '2.70',
    }
    with pytest.raises(ConfigError, match=r'та же сумма и дата.*--extra'):
        cli.ledger_add(date=dt.date(2026, 10, 14), **again)
    with pytest.raises(ConfigError, match=r'больше суммы счёта.*--extra'):
        cli.ledger_add(date=dt.date(2026, 10, 15), **again)
    with pytest.raises(ConfigError, match='--extra относится к поступлению по счёту'):
        cli.ledger_add(**{**again, 'invoice': None}, extra=True)
    store = OfficeStore(office / 'office.sqlite')
    assert len(store.receipts()) == 1
    cli.ledger_month('2026-10')
    month = capsys.readouterr().out
    assert 'Оборот месяца: **2 700.00 GEL**' in month
    assert 'Налог к уплате за месяц (оценка): **27.00 GEL**' in month
    # Клиент правда заплатил дважды: --extra записывает, а сводка громко показывает переплату.
    cli.ledger_add(date=dt.date(2026, 10, 15), extra=True, **again)
    out = capsys.readouterr().out
    assert 'записано #2' in out
    assert 'внимание: получено 2000.00, а в счёте 1000.00 USD: больше на 1000.00' in out
    assert 'не записана ли оплата дважды' in out


def test__ledger_add_full_then_invoice_paid_amount__top_up_beyond_total_refused(office, capsys):
    _issue(setup='1000')
    common = {'currency': 'USD', 'channel': 'bank', 'counterparty': 'Example Corp', 'country': 'US', 'rate': '2.70'}
    cli.ledger_add(amount='1000', date=dt.date(2026, 10, 10), invoice='AS-2026-001', **common)
    assert 'по счёту AS-2026-001: получено 1000.00 USD — ровно сумма счёта (1 зап.)' in capsys.readouterr().out
    # «Доплата остатка» на всю сумму — та же оплата второй раз.
    with pytest.raises(ConfigError, match='больше суммы счёта'):
        cli.invoice_paid('AS-2026-001', amount='1000', date=dt.date(2026, 10, 14), rate='2.70')
    with pytest.raises(ConfigError, match='--extra относятся к новой записи'):
        cli.invoice_paid('AS-2026-001', extra=True)
    store = OfficeStore(office / 'office.sqlite')
    assert len(store.receipts()) == 1 and store.get_invoice('AS-2026-001').status == 'issued'
    cli.invoice_paid('AS-2026-001', amount='1000', date=dt.date(2026, 10, 14), rate='2.70', extra=True)
    assert 'внимание: получено 2000.00, а в счёте 1000.00 USD' in capsys.readouterr().out
    assert store.get_invoice('AS-2026-001').status == 'paid'


def test__invoice_paid__flip_only_partial__warns_received_less(office, capsys):
    _issue(setup='1000')
    cli.ledger_add(
        amount='400',
        currency='USD',
        channel='bank',
        counterparty='Example Corp',
        country='US',
        date=dt.date(2026, 10, 10),
        rate='2.70',
        invoice='AS-2026-001',
    )
    capsys.readouterr()
    cli.invoice_paid('AS-2026-001', date=dt.date(2026, 10, 12))
    out = capsys.readouterr().out
    assert 'отмечен оплаченным 2026-10-12' in out and 'внимание: получено 400.00' in out


def test__invoice_paid__partial_amount__warning_and_country_needed(office, capsys, tmp_path):
    _issue()
    cli.invoice_paid('AS-2026-001', date=dt.date(2026, 10, 14), amount='300', rate='2.7')
    assert 'внимание: получено 300.00' in capsys.readouterr().out
    request = tmp_path / 'invoice.toml'
    request.write_text('[client]\nname = "No Country"\n[[items]]\npreset = "setup"\nunit_price = 5\n', encoding='utf-8')
    cli.invoice_new(from_file=request)
    with pytest.raises(ConfigError, match='--country'):
        cli.invoice_paid('AS-2026-002', rate='2.7')
    cli.invoice_paid('AS-2026-002', rate='2.7', country='ge')
    assert OfficeStore(office / 'office.sqlite').receipts()[-1].client_country == 'GE'


@respx.mock
def test__invoice_paid__nbg_rate_fetched_and_cached(office, capsys):
    route = respx.get(NBG_URL).mock(return_value=httpx.Response(200, content=NBG_USD))
    _issue()
    cli.invoice_paid('AS-2026-001', date=dt.date(2026, 10, 14))
    assert 'курс НБГ от 2026-10-14' in capsys.readouterr().out
    cli.ledger_rate(dt.date(2026, 10, 14))
    assert '2.7123 GEL за 1 USD' in capsys.readouterr().out
    assert route.call_count == 1  # второй раз — из кэша


@respx.mock
def test__ledger_add__nbg_down__clear_request_for_rate(office):
    respx.get(NBG_URL).mock(side_effect=httpx.ConnectError('down'))
    with pytest.raises(RateUnavailableError, match='--rate'):
        cli.ledger_add(amount='100', currency='USD', channel='upwork', counterparty='Upwork', country='US')
    assert OfficeStore(office / 'office.sqlite').receipts() == []


def test__ledger_add__manual_rate_gross_and_invoice_hint(office, capsys):
    _issue()
    capsys.readouterr()
    cli.ledger_add(
        amount='1000',
        currency='usd',
        channel='upwork',
        counterparty=' Upwork client ',
        country='us',
        date=dt.date(2026, 10, 5),
        rate='2.70',
        fee='100',
        invoice='AS-2026-001',
        note='milestone 1',
        fetch=False,
    )
    out = capsys.readouterr().out
    assert '#1: 2026-10-05 1000.00 USD × 2.70 = 2700.00 GEL' in out
    assert 'invoice paid AS-2026-001' in out
    [receipt] = OfficeStore(office / 'office.sqlite').receipts()
    assert receipt.amount == Decimal('1000.00') and receipt.platform_fee == Decimal('100.00')
    assert receipt.counterparty == 'Upwork client'


def test__ledger_add__bad_input__config_error(office):
    common = {'amount': '100', 'currency': 'GEL', 'channel': 'bank'}
    with pytest.raises(ConfigError, match='--counterparty'):
        cli.ledger_add(**common, counterparty=' ', country='GE')
    with pytest.raises(ConfigError, match='ISO 3166'):
        cli.ledger_add(**common, counterparty='Shop', country='Georgia')
    with pytest.raises(ConfigError, match='2 знаков'):
        cli.ledger_add(amount='1.005', currency='GEL', channel='bank', counterparty='Shop', country='GE')


def test__ledger_month_and_export__numbers_and_csv(office, capsys):
    cli.ledger_add(
        amount='3000', currency='GEL', channel='bank', counterparty='Shop', country='GE', date=dt.date(2026, 10, 10)
    )
    cli.ledger_add(
        amount='1000',
        currency='USD',
        channel='upwork',
        counterparty='=cmd',
        country='US',
        date=dt.date(2026, 10, 5),
        rate='2.7',
    )
    cli.ledger_add(
        amount='50', currency='GEL', channel='bank', counterparty='Old', country='GE', date=dt.date(2026, 9, 30)
    )
    capsys.readouterr()
    cli.ledger_month('2026-10')
    out = capsys.readouterr().out
    assert 'Оборот месяца: **5 700.00 GEL**' in out
    assert 'Налог к уплате за месяц (оценка): **57.00 GEL**' in out
    assert 'до 2026-11-15' in out
    assert 'ledger export --month 2026-10' in out
    cli.ledger_export(month='2026-10')
    path = office / 'exports' / 'ledger-2026-10.csv'
    assert f'{path}: 2 поступлений, 5700.00 GEL' in capsys.readouterr().out
    raw = path.read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')  # BOM для Excel бухгалтера
    text = raw.decode('utf-8-sig')
    assert text.splitlines()[1].startswith("2026-10-05,1000.00,USD,2.7,manual,2700.00,upwork,'=cmd")
    out_path = office / 'year.csv'
    cli.ledger_export(year=2026, out=out_path)
    assert len(out_path.read_text(encoding='utf-8-sig').splitlines()) == 4
    with pytest.raises(ConfigError, match='ровно одно'):
        cli.ledger_export()
    with pytest.raises(ConfigError, match='ровно одно'):
        cli.ledger_export(month='2026-10', year=2026)


def _template_dir(tmp_path: Path) -> Path:
    templates = tmp_path / 'legal'
    templates.mkdir()
    (templates / 'support-sla.en.md').write_text(TEMPLATE, encoding='utf-8')
    return templates


def test__contract__flags_file_and_set__html_written(office, tmp_path, capsys):
    templates = _template_dir(tmp_path)
    client_file = tmp_path / 'client.toml'
    client_file.write_text('[client]\nname = "Example <LLC>"\n[fields]\n"5th" = "3rd"\n', encoding='utf-8')
    cli.contract_render(
        'support-sla',
        client_file=[client_file],
        number='SA-7',
        date='2026-10-20',
        set_=['VERIFY: backups.=Daily encrypted backups.'],
        templates=templates,
    )
    out = capsys.readouterr().out
    target = office / 'contracts' / 'SA-7-support-sla.en.html'
    assert f'договор собран: {target}' in out
    page = target.read_text(encoding='utf-8')
    assert f'Agreement No. SA-7 dated 2026-10-20 between {TEST_NAME} and Example &lt;LLC&gt;.' in page
    assert 'by the 3rd Business Day. Daily encrypted backups.' in page
    assert 'Template. Not legal advice' not in page


def test__contract__existing_file__refused_unless_force(office, tmp_path, capsys):
    templates = _template_dir(tmp_path)
    request = {
        'client_name': 'ООО Ромашка',
        'date': '2026-10-20',
        'set_': ['5th=5th', 'VERIFY: backups.=', 'number=1'],
        'fmt': 'md',
        'templates': templates,
    }
    cli.contract_render('support-sla', **request)
    [first] = (office / 'contracts').iterdir()
    first.write_text('правки оператора', encoding='utf-8')
    with pytest.raises(ConfigError, match='--force'):
        cli.contract_render('support-sla', **request)
    assert first.read_text(encoding='utf-8') == 'правки оператора'
    cli.contract_render('support-sla', **{**request, 'client_name': 'ИП Иванов'})
    assert len(list((office / 'contracts').iterdir())) == 2
    cli.contract_render('support-sla', **request, force=True)
    assert first.read_text(encoding='utf-8').startswith('# Support Terms')


def test__contract__markdown_and_notices(office, tmp_path, capsys):
    templates = _template_dir(tmp_path)
    out_path = tmp_path / 'out' / 'sla.md'
    cli.contract_render(
        'support-sla',
        client_name='Example LLC',
        number='SA-8',
        date='2026-10-20',
        set_=['5th=5th', 'VERIFY: backups.=', 'not in template=x'],
        fmt='md',
        templates=templates,
        out=out_path,
    )
    assert out_path.read_text(encoding='utf-8').endswith('Report by the 5th Business Day.\n')
    assert 'не найден в шаблоне' in capsys.readouterr().out


def test__contract__incomplete__error_and_no_file(office, tmp_path):
    templates = _template_dir(tmp_path)
    with pytest.raises(ContractIncompleteError) as caught:
        cli.contract_render('support-sla', client_name='Example LLC', templates=templates)
    assert '[5th]' in str(caught.value) and '[VERIFY: backups.]' in str(caught.value)
    assert not (office / 'contracts').exists()
    with pytest.raises(ConfigError, match='--set'):
        cli.contract_render('support-sla', set_=['no equals sign'], templates=templates)


def test__contract_fields__stdout_and_file(office, tmp_path, capsys):
    templates = _template_dir(tmp_path)
    cli.contract_fields('support-sla', templates=templates)
    assert '# "VERIFY: backups." = ""' in capsys.readouterr().out
    target = tmp_path / 'fields.toml'
    cli.contract_fields('support-sla', templates=templates, out=target)
    assert '# name = ""' in target.read_text(encoding='utf-8')
    with pytest.raises(ConfigError, match='уже существует'):
        cli.contract_fields('support-sla', templates=templates, out=target)


def test__cyclopts__default_and_subcommands__parsed(office, tmp_path, capsys):
    cli.app(
        ['invoice', '--client-name', 'Parsed Co', '--setup', '100', '--item', 'A;1;5', '--item', 'B;2', '--lang', 'ru'],
        result_action='return_value',
        exit_on_error=False,
    )
    cli.app(['invoice', 'list'], result_action='return_value', exit_on_error=False)
    assert 'Parsed Co' in capsys.readouterr().out
    [record] = OfficeStore(office / 'office.sqlite').list_invoices()
    assert [item.unit_price for item in record.invoice.items] == [Decimal(100), Decimal(5), Decimal(2)]
    cli.app(['init'], result_action='return_value', exit_on_error=False)
    assert 'реестр уже есть' in capsys.readouterr().out
    templates = _template_dir(tmp_path)
    cli.app(
        ['contract', 'fields', 'support-sla', '--templates', str(templates)],
        result_action='return_value',
        exit_on_error=False,
    )
    assert '[agreement]' in capsys.readouterr().out
    cli.app(
        [
            'ledger',
            'add',
            '--amount',
            '10',
            '--currency',
            'GEL',
            '--channel',
            'bank',
            '--counterparty',
            'X',
            '--country',
            'GE',
            '--no-fetch',
        ],
        result_action='return_value',
        exit_on_error=False,
    )
    assert 'записано #1' in capsys.readouterr().out
    by_invoice = [
        'ledger',
        'add',
        '--amount',
        '5',
        '--currency',
        'GEL',
        '--channel',
        'bank',
        '--counterparty',
        'Parsed Co',
    ]
    by_invoice += ['--country', 'GE', '--no-fetch', '--invoice', 'AS-2026-001']
    cli.app(by_invoice, result_action='return_value', exit_on_error=False)
    with pytest.raises(ConfigError, match='--extra'):
        cli.app(by_invoice, result_action='return_value', exit_on_error=False)
    cli.app([*by_invoice, '--extra'], result_action='return_value', exit_on_error=False)
    assert 'записано #3' in capsys.readouterr().out


def test__office_init__creates_once_and_commands_never_create(tmp_path, capsys):
    data = tmp_path / 'fresh'
    with pytest.raises(ConfigError, match='office init'):
        cli.ledger_month('2026-10', data_dir=data)
    with pytest.raises(ConfigError, match='office init'):
        cli.ledger_add(amount='10', currency='GEL', channel='bank', counterparty='X', country='GE', data_dir=data)
    assert not data.exists()
    cli.office_init(data_dir=data)
    assert f'реестр создан: {data.resolve() / "office.sqlite"}' in capsys.readouterr().out
    cli.office_init(data_dir=data)
    assert 'реестр уже есть' in capsys.readouterr().out


def test__office__run_from_other_directory__same_registry_at_repo_root(tmp_path, operator_file, monkeypatch, capsys):
    # Раньше var/office считался от текущего каталога: из другой папки — пустой реестр и второй AS-2026-001.
    root = tmp_path / 'repo'
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.setattr(cli, 'project_root', lambda: root)
    monkeypatch.setattr(cli, '_now', lambda: NOW)
    monkeypatch.delenv('AUTOSTUDIO_OFFICE_DIR', raising=False)
    monkeypatch.setenv('AUTOSTUDIO_OPERATOR', str(operator_file))
    monkeypatch.chdir(elsewhere)
    with pytest.raises(ConfigError) as caught:
        _issue()
    assert str(root / 'var' / 'office' / 'office.sqlite') in str(caught.value)
    cli.office_init()
    _issue()
    monkeypatch.chdir(tmp_path)
    _issue(client_name='Other Corp')
    assert 'счёт AS-2026-002 выставлен' in capsys.readouterr().out
    assert not (elsewhere / 'var').exists() and not (tmp_path / 'var').exists()
    assert len(OfficeStore(root / 'var' / 'office' / 'office.sqlite').list_invoices()) == 2


def test__root_cli__office_registered_lazily():
    assert root_cli.app['office']['invoice']['list'] is not None

from pathlib import Path

import pytest

from autostudio.clientconf import load_client_config
from autostudio.errors import ConfigError
from autostudio.inbox.config import Category, CompanyProfile, InboxConfig, default_categories
from tests.inbox.helpers import make_config

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'inbox' / 'client.toml'


def test__example_config__loads__tour_agency_with_booking_category():
    config = load_client_config(EXAMPLE, InboxConfig)
    assert config.client.id == 'demo-inbox'
    assert 'booking_request' in config.inbox.category_ids
    assert config.inbox.company.facts
    assert config.inbox.drafts_folder == '[Gmail]/Drafts'


def test__defaults__no_categories__five_sensible_categories_with_other():
    config = make_config()
    assert config.inbox.category_ids == ['lead', 'support', 'invoice', 'marketing', 'other']
    assert config.inbox.category('lead').notify and config.inbox.category('lead').draft_reply
    assert config.inbox.notify_on_urgency == 'high'
    assert config.inbox.imap_port == 993


def test__category__unknown_id__falls_back_to_other():
    assert make_config().inbox.category('spam').id == 'other'


def test__categories__duplicate_ids__rejected():
    cats = [{'id': 'lead', 'description': 'a'}, {'id': 'lead', 'description': 'b'}, {'id': 'other', 'description': 'c'}]
    with pytest.raises(ConfigError, match='повторяются'):
        make_config(categories=cats)


def test__categories__without_other__rejected():
    with pytest.raises(ConfigError, match="'other'"):
        make_config(categories=[{'id': 'lead', 'description': 'x'}], bulk_category='lead', auto_reply_category='lead')


def test__bulk_category__missing_in_categories__rejected():
    cats = [{'id': 'lead', 'description': 'x'}, {'id': 'other', 'description': 'y'}]
    with pytest.raises(ConfigError, match='bulk_category'):
        make_config(categories=cats)


@pytest.mark.parametrize(
    ('overrides', 'match'),
    [
        ({'imap_host': ' '}, 'imap_host'),
        ({'imap_port': 0}, 'imap_port'),
        ({'drafts_folder': 'INBOX'}, 'drafts_folder'),
        ({'folder': 'IN\r\nBOX'}, 'управляющие'),
        ({'first_run_since_days': 61}, 'first_run_since_days'),
        ({'max_per_run': 0}, 'max_per_run'),
        ({'max_body_chars': 10}, 'max_body_chars'),
        ({'timeout_seconds': 0}, 'timeout_seconds'),
        ({'sheet_tab': ''}, 'sheet_tab'),
        ({'from_address': 'not-an-address'}, 'from_address'),
    ],
)
def test__inbox_section__bad_values__config_error(overrides, match):
    with pytest.raises(ConfigError, match=match):
        make_config(**overrides)


def test__category__move_to_inbox__rejected():
    cats = [{'id': 'other', 'description': 'x', 'move_to': 'INBOX'}, {'id': 'marketing', 'description': 'y'}]
    with pytest.raises(ConfigError, match='move_to'):
        make_config(categories=cats)


@pytest.mark.parametrize(
    ('kwargs', 'match'),
    [
        ({'id': 'Lead', 'description': 'x'}, 'a-z'),
        ({'id': 'lead', 'description': ' '}, 'описание'),
        ({'id': 'lead', 'description': 'x', 'move_to': ''}, 'move_to'),
    ],
)
def test__category__invalid__config_error(kwargs, match):
    with pytest.raises(ConfigError, match=match):
        Category(**kwargs)


def test__company__validation_and_sign_off():
    with pytest.raises(ConfigError, match='name'):
        CompanyProfile(name=' ')
    with pytest.raises(ConfigError, match='facts'):
        CompanyProfile(name='A', facts=['ok', ' '])
    with pytest.raises(ConfigError, match='reply_language'):
        CompanyProfile(name='A', reply_language='russian')
    assert CompanyProfile(name='Acme').sign_off == 'Acme'
    assert CompanyProfile(name='Acme', signature=' Team ').sign_off == 'Team'


def test__default_categories__fresh_list_each_call():
    first = default_categories()
    first.pop()
    assert len(default_categories()) == 5


def test__category__label_lead_flag_and_title_fallback():
    labelled = Category(id='booking_request', description='x', label={'ru': 'Заявка'}, notify=True)
    assert labelled.title('ru') == 'Заявка' and labelled.title('en') == 'Booking request'
    assert labelled.is_lead  # без lead — как раньше, по notify
    assert Category(id='lead', description='x', lead=True).is_lead
    assert not Category(id='vip', description='x', notify=True, lead=False).is_lead


@pytest.mark.parametrize(
    'kwargs',
    [
        {'label': {'russian': 'x'}},
        {'label': {'ru': ' '}},
    ],
)
def test__category__bad_label__config_error(kwargs):
    with pytest.raises(ConfigError, match='label'):
        Category(id='lead', description='x', **kwargs)


def test__company__localized_signatures_and_facts():
    company = CompanyProfile(
        name='Acme',
        signature='Best, Acme',
        signatures={'ru': 'С уважением, Acme'},
        facts=['Tour: 120 GEL.'],
        facts_by_language={'ru': ['Тур: 120 GEL.']},
    )
    assert company.sign_off_for('ru') == 'С уважением, Acme' and company.sign_off_for('de') == 'Best, Acme'
    assert company.facts_for('ru') == ['Тур: 120 GEL.'] and company.facts_for('en') == ['Tour: 120 GEL.']
    assert company.all_facts == ['Tour: 120 GEL.', 'Тур: 120 GEL.']


@pytest.mark.parametrize(
    ('kwargs', 'match'),
    [
        ({'signatures': {'ru': ''}}, 'signatures'),
        ({'facts_by_language': {'russian': ['x']}}, 'facts_by_language'),
        ({'facts_by_language': {'ru': []}}, 'facts_by_language'),
        ({'facts_by_language': {'ru': [' ']}}, 'facts_by_language'),
    ],
)
def test__company__bad_localized_values__config_error(kwargs, match):
    with pytest.raises(ConfigError, match=match):
        CompanyProfile(name='Acme', **kwargs)


def test__example_config__labels_lead_flags_and_russian_texts():
    config = load_client_config(EXAMPLE, InboxConfig)
    booking = config.inbox.category('booking_request')
    assert booking.is_lead and booking.title('ru') == 'Заявка на бронирование'
    assert config.inbox.company.sign_off_for('ru').startswith('С уважением')
    assert config.inbox.company.facts_for('ru')[0].startswith('Однодневные')
    assert str(config.sink.csv_dir).startswith('/data/')


@pytest.mark.parametrize(
    ('auth', 'expected'),
    [
        ('password', ['INBOX_IMAP_USER', 'INBOX_IMAP_PASSWORD']),
        (
            'google_oauth',
            ['INBOX_IMAP_USER', 'INBOX_OAUTH_CLIENT_ID', 'INBOX_OAUTH_CLIENT_SECRET', 'INBOX_OAUTH_REFRESH_TOKEN'],
        ),
        (
            'microsoft_oauth',
            ['INBOX_IMAP_USER', 'INBOX_OAUTH_TENANT_ID', 'INBOX_OAUTH_CLIENT_ID', 'INBOX_OAUTH_CLIENT_SECRET'],
        ),
    ],
)
def test__auth_env__per_login_method__required_variables(auth, expected):
    assert make_config(auth=auth).inbox.auth_env == expected


@pytest.mark.parametrize(
    ('overrides', 'match'),
    [
        ({'oauth_client_id_env': 'BAD NAME'}, 'oauth_client_id_env'),
        ({'password_env': '1PASSWORD'}, 'password_env'),
        ({'phone_region': 'XX'}, 'phone_region'),
        ({'phone_fallback_regions': ['RU', 'Russia']}, 'phone_region'),
        ({'invoices': {'max_pdf_kb': 10}}, 'max_pdf_kb'),
        ({'invoices': {'max_pages': 0}}, 'max_pages'),
        ({'invoices': {'max_text_chars': 10}}, 'max_text_chars'),
        ({'invoices': {'sheet_tab': ' '}}, 'invoices.sheet_tab'),
        ({'invoices': {'categories': ['bills']}}, 'bills'),
        ({'invoices': {'enabled': True, 'sheet_tab': 'Inbox'}}, 'совпадает'),
    ],
)
def test__inbox_section__bad_auth_phone_or_invoice_values__config_error(overrides, match):
    with pytest.raises(ConfigError, match=match):
        make_config(**overrides)


def test__phone_regions__defaults_ge_then_ru_without_duplicates():
    assert make_config().inbox.phone_regions == ['GE', 'RU']
    assert make_config(phone_region='RU', phone_fallback_regions=['RU', 'KZ']).inbox.phone_regions == ['RU', 'KZ']


def test__invoice_categories__off_by_default_explicit_and_missing_invoice_category():
    # Выключено по умолчанию: обновление не добавляет клиенту Basic вызовы модели и вкладку.
    assert make_config().inbox.invoice_category_ids == []
    assert make_config(invoices={'enabled': True}).inbox.invoice_category_ids == ['invoice']
    on = {'enabled': True, 'categories': ['support']}
    assert make_config(invoices=on).inbox.invoice_category_ids == ['support']
    cats = [
        {'id': 'lead', 'description': 'x'},
        {'id': 'marketing', 'description': 'y'},
        {'id': 'other', 'description': 'z'},
    ]
    assert make_config(categories=cats, invoices={'enabled': True}).inbox.invoice_category_ids == []


def test__example_config__oauth_ready_fields_and_invoices_section():
    config = load_client_config(EXAMPLE, InboxConfig)
    assert config.inbox.auth == 'password' and config.inbox.phone_regions == ['GE', 'RU']
    assert config.inbox.invoices.sheet_tab == 'Invoices' and config.inbox.invoice_category_ids == ['invoice']


def test__config_file__unknown_auth_method__config_error_names_field(tmp_path):
    text = EXAMPLE.read_text(encoding='utf-8').replace('auth = "password"', 'auth = "basic"')
    path = tmp_path / 'inbox.toml'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ConfigError, match=r'inbox\.auth'):
        load_client_config(path, InboxConfig)


def test__config_file__fractional_timeout__toml_decimal_converted_to_float(tmp_path):
    # Ядро читает дробные числа TOML как Decimal (parse_float): поле-float должно это пережить.
    text = EXAMPLE.read_text(encoding='utf-8').replace(
        'max_body_chars = 8000', 'max_body_chars = 8000\ntimeout_seconds = 45.5'
    )
    path = tmp_path / 'inbox.toml'
    path.write_text(text, encoding='utf-8')
    timeout = load_client_config(path, InboxConfig).inbox.timeout_seconds
    assert isinstance(timeout, float) and timeout == 45.5

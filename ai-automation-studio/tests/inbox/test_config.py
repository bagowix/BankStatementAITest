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

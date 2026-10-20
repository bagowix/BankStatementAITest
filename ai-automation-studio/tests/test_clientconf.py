from pathlib import Path

import msgspec
import pytest

from autostudio.clientconf import (
    ClientInfo,
    NotifyConfig,
    SinkConfig,
    client_data_dir,
    load_client_config,
    load_client_info,
    require_env,
)
from autostudio.errors import ConfigError


class _Conf(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    client: ClientInfo
    notify: NotifyConfig = NotifyConfig()
    sink: SinkConfig = SinkConfig()


def test__load_client_config__full_file__parsed_with_paths(write_toml):
    path = write_toml(
        '[client]\nid = "acme"\nname = "Acme"\ntimezone = "Europe/Berlin"\n'
        '[sink]\nbackend = "csv"\ncsv_dir = "out/acme"\n'
    )
    conf = load_client_config(path, _Conf)
    assert conf.client.zone.key == 'Europe/Berlin'
    assert conf.sink.csv_dir == Path('out/acme')


def test__load_client_config__missing_file__config_error(tmp_path):
    with pytest.raises(ConfigError, match='не найден'):
        load_client_config(tmp_path / 'nope.toml', _Conf)


def test__load_client_config__broken_toml__config_error(write_toml):
    with pytest.raises(ConfigError, match='битый TOML'):
        load_client_config(write_toml('[client\n'), _Conf)


def test__load_client_config__unknown_field__config_error(write_toml):
    with pytest.raises(ConfigError, match='unknown'):
        load_client_config(write_toml('[client]\nid = "acme"\nname = "A"\ncolour = "red"\n'), _Conf)


def test__client_info__bad_timezone__config_error(write_toml):
    with pytest.raises(ConfigError, match='часовой пояс'):
        load_client_config(write_toml('[client]\nid = "acme"\nname = "A"\ntimezone = "Mars/Base"\n'), _Conf)


@pytest.mark.parametrize('bad_id', ['A', 'acme_shop', '-acme', 'x'])
def test__client_info__bad_id__config_error(bad_id):
    with pytest.raises(ConfigError, match=r'client\.id'):
        ClientInfo(id=bad_id, name='A')


def test__sink_config__google_without_id__config_error():
    with pytest.raises(ConfigError, match='spreadsheet_id'):
        SinkConfig(backend='google')


def test__load_client_info__other_sections__ignored(write_toml):
    path = write_toml('[client]\nid = "acme"\nname = "Acme"\n[booking]\nanything = 1\n')
    assert load_client_info(path).id == 'acme'


def test__client_data_dir__by_id():
    assert client_data_dir(Path('var'), ClientInfo(id='acme', name='Acme')) == Path('var/acme')


def test__require_env__present__value():
    assert require_env('X', {'X': 'v'}) == 'v'


def test__require_env__missing__names_variable():
    with pytest.raises(ConfigError, match='MY_TOKEN'):
        require_env('MY_TOKEN', {})


def test__load_client_config__float_in_toml__decimal_without_float_noise(write_toml):
    from decimal import Decimal

    class _Money(msgspec.Struct, frozen=True):
        client: ClientInfo
        price: Decimal
        delay: float

    conf = load_client_config(write_toml('price = 0.1\ndelay = 1.5\n[client]\nid = "acme"\nname = "A"\n'), _Money)
    assert conf.price == Decimal('0.1')
    assert conf.delay == 1.5


def test__load_env_file__formats_and_no_override(tmp_path):
    from autostudio.clientconf import load_env_file

    env = tmp_path / '.env'
    env.write_text('# comment\n\nexport A=1\nB = "two words"\nC=\'x=y\'\nD=keep\n', encoding='utf-8')
    target = {'D': 'already'}
    assert load_env_file(env, target) == ['A', 'B', 'C']
    assert target == {'A': '1', 'B': 'two words', 'C': 'x=y', 'D': 'already'}


def test__load_env_file__garbage_line__config_error(tmp_path):
    from autostudio.clientconf import load_env_file

    env = tmp_path / '.env'
    env.write_text('JUST_TEXT\n', encoding='utf-8')
    with pytest.raises(ConfigError, match=r'\.env:1'):
        load_env_file(env, {})


def test__load_sibling_env__present_and_absent(tmp_path):
    from autostudio.clientconf import load_sibling_env

    config = tmp_path / 'booking.toml'
    assert load_sibling_env(config, {}) == []
    (tmp_path / '.env').write_text('TOKEN=1\n', encoding='utf-8')
    target: dict[str, str] = {}
    assert load_sibling_env(config, target) == ['TOKEN']


def test__events_path__under_client_dir():
    from autostudio.clientconf import events_path

    assert events_path(Path('var'), ClientInfo(id='acme', name='A')) == Path('var/acme/events.sqlite')

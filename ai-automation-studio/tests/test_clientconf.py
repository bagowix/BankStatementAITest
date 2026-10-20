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
    with pytest.raises(ConfigError, match='client.id'):
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

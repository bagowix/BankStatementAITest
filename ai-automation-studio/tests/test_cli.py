import datetime as dt
import logging

import pytest

from autostudio import cli
from autostudio.clientconf import ClientInfo, events_path
from autostudio.errors import ConfigError
from autostudio.events import EventLog


def test__quiet_http_loggers__warning_level():
    cli.quiet_http_loggers()
    assert logging.getLogger('httpx').level == logging.WARNING
    assert logging.getLogger('httpcore').level == logging.WARNING


def test__report__markdown_from_events(tmp_path, monkeypatch, capsys, write_toml):
    monkeypatch.setenv('AUTOSTUDIO_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.delenv('AUTOSTUDIO_LLM_PROVIDER', raising=False)
    config = write_toml('[client]\nid = "acme"\nname = "Acme Barbers"\nlanguage = "ru"\ntimezone = "Asia/Tbilisi"\n')
    log = EventLog(events_path(tmp_path / 'data', ClientInfo(id='acme', name='Acme Barbers')))
    log.record('prices', 'system.run_ok', dt.datetime(2026, 10, 5, 9, tzinfo=dt.UTC))
    cli.report(config, month='2026-10')
    out = capsys.readouterr().out
    assert 'Acme Barbers — 2026-10' in out
    assert 'Успешных запусков: 1 из 1' in out


def test__report__html_to_file(tmp_path, monkeypatch, write_toml):
    monkeypatch.setenv('AUTOSTUDIO_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.delenv('AUTOSTUDIO_LLM_PROVIDER', raising=False)
    config = write_toml('[client]\nid = "acme"\nname = "Acme"\n')
    out = tmp_path / 'r' / 'report.html'
    cli.report(config, month='2026-10', fmt='html', out=out)
    assert out.read_text(encoding='utf-8').startswith('<!doctype html>')


def test__report__bad_month__config_error(write_toml):
    with pytest.raises(ConfigError, match='ГГГГ-ММ'):
        cli.report(write_toml('[client]\nid = "acme"\nname = "A"\n'), month='10/2026')


def test__new_client__replaces_every_demo_id_and_copies_env(tmp_path, capsys):
    examples = tmp_path / 'examples'
    (examples / 'inbox').mkdir(parents=True)
    (examples / 'inbox' / 'client.toml').write_text(
        '[client]\nid = "demo-inbox"\nname = "Demo"\n[sink]\ncsv_dir = "/data/demo-inbox/out"\n', encoding='utf-8'
    )
    (examples / 'inbox' / '.env.example').write_text('INBOX_IMAP_USER=\n', encoding='utf-8')
    cli.new_client('acme-tours', package='inbox', root=tmp_path / 'clients', examples=examples)
    text = (tmp_path / 'clients' / 'acme-tours' / 'inbox.toml').read_text(encoding='utf-8')
    assert 'demo-inbox' not in text
    assert 'id = "acme-tours"' in text
    assert '/data/acme-tours/out' in text
    assert (tmp_path / 'clients' / 'acme-tours' / '.env').exists()
    assert 'демо-данные' in capsys.readouterr().out
    with pytest.raises(ConfigError, match='уже существует'):
        cli.new_client('acme-tours', package='inbox', root=tmp_path / 'clients', examples=examples)


def test__new_client__missing_example__config_error(tmp_path):
    with pytest.raises(ConfigError, match='нет образца'):
        cli.new_client('acme', package='prices', root=tmp_path / 'clients', examples=tmp_path / 'nothing')


def test__main__package_error__exit_2(monkeypatch, capsys):
    def boom():
        raise ConfigError('плохой конфиг')

    monkeypatch.setattr(cli, 'app', boom)
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert 'плохой конфиг' in capsys.readouterr().err

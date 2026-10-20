import datetime as dt
import json
from pathlib import Path

import pytest

from autostudio.booking import cli
from autostudio.booking.domain import Customer, Slot
from autostudio.booking.store import BookingStore
from autostudio.errors import ConfigError
from tests.booking.helpers import WED, local

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'booking' / 'client.toml'
TOKEN = '123456789:' + 'A' * 35


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ('AUTOSTUDIO_LLM_PROVIDER', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'BOOKING_BOT_TOKEN'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AUTOSTUDIO_DATA_DIR', str(tmp_path / 'data'))
    return tmp_path


def test__check__offline__config_summary_and_slots(env, capsys):
    cli.check(EXAMPLE, offline=True)
    out = capsys.readouterr().out
    assert 'конфиг:' in out and 'секреты: не проверялись' in out
    assert 'Demo Barbers (demo-booking)' in out and 'ближайшие свободные слоты:' in out
    assert out.count('(Giorgi)') >= 3
    assert not (env / 'data').exists()  # без побочных эффектов


def test__check__online__token_required_and_validated(env, monkeypatch, capsys):
    with pytest.raises(ConfigError, match='BOOKING_BOT_TOKEN'):
        cli.check(EXAMPLE)
    monkeypatch.setenv('BOOKING_BOT_TOKEN', 'oops')
    with pytest.raises(ConfigError, match='не похоже на токен'):
        cli.check(EXAMPLE)
    monkeypatch.setenv('BOOKING_BOT_TOKEN', TOKEN)
    cli.check(EXAMPLE)
    out = capsys.readouterr().out
    assert 'токен бота: BOOKING_BOT_TOKEN задан' in out and TOKEN not in out


def test__check_environment__google_sink_and_notify_channels(env, write_toml):
    text = EXAMPLE.read_text(encoding='utf-8').replace(
        'backend = "csv"', 'backend = "google"\nspreadsheet_id = "sheet-1"'
    )
    text = text.replace('console = true', 'console = true\ntelegram_chat_id = "42"\nwebhook_url_env = "HOOK"')
    config = cli.load_booking_config(write_toml(text))
    environ = {'BOOKING_BOT_TOKEN': TOKEN}
    with pytest.raises(ConfigError, match='GOOGLE_APPLICATION_CREDENTIALS'):
        cli.check_environment(config, environ)
    environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(env / 'missing.json')
    with pytest.raises(ConfigError, match='файл ключа'):
        cli.check_environment(config, environ)
    key = env / 'key.json'
    key.write_text(json.dumps({}), encoding='utf-8')
    environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(key)
    with pytest.raises(ConfigError, match='AUTOSTUDIO_NOTIFY_BOT_TOKEN'):
        cli.check_environment(config, environ)
    environ['AUTOSTUDIO_NOTIFY_BOT_TOKEN'] = 't'
    with pytest.raises(ConfigError, match='HOOK'):
        cli.check_environment(config, environ)
    environ['HOOK'] = 'https://hooks.example.test/x'
    assert cli.check_environment(config, environ)[-1].startswith('Google Sheets')


def test__describe__warnings_for_thin_config(env, write_toml):
    text = EXAMPLE.read_text(encoding='utf-8').replace('admin_chat_ids = [111111111]', 'admin_chat_ids = []')
    text = text.replace('contact_phone = "+995 555 12 34 56"\n', '')
    text = text.split('[[booking.faq]]')[0]
    config = cli.load_booking_config(write_toml(text))
    lines = cli.describe(config, cli.Settings(data_dir=env / 'data'), dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC))
    joined = '\n'.join(lines)
    assert 'admin_chat_ids пуст' in joined and 'FAQ пуст' in joined and 'contact_phone не задан' in joined
    assert 'модель fake' in joined


def test__agenda__reads_store_readonly(env, capsys):
    config = cli.load_booking_config(EXAMPLE)
    store = BookingStore(env / 'data' / config.client.id / 'booking.sqlite')
    start = local(WED, 12)
    store.create(
        Slot(service_id='haircut', staff_id='giorgi', start=start, end=start + dt.timedelta(minutes=45)),
        customer=Customer(key='tg:1', name='Anna', phone='+995555000111'),
        source='buttons',
        language='en',
        now=start - dt.timedelta(days=1),
        buffer=dt.timedelta(0),
    )
    cli.agenda(EXAMPLE, day='2026-10-21', days=2)
    out = capsys.readouterr().out
    assert '📋 Wed 21 Oct' in out and '12:00–12:45 Giorgi · Haircut · Anna' in out and '📋 Thu 22 Oct' in out
    cli.agenda(EXAMPLE)
    with pytest.raises(ConfigError, match='--day'):
        cli.agenda(EXAMPLE, day='21.10.2026')
    with pytest.raises(ConfigError, match='--days'):
        cli.agenda(EXAMPLE, days=0)


def test__chat__sandbox_dir_and_quit(env, monkeypatch, capsys):
    answers = iter(['3', '/quit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    cli.chat(EXAMPLE, data_dir=env / 'sandbox', language='ru')
    out = capsys.readouterr().out
    assert 'Здравствуйте' in out and 'Наши услуги' in out
    assert (env / 'sandbox' / 'demo-booking' / 'booking.sqlite').exists()
    assert not (env / 'data').exists()


def test__run__wires_container_and_stops(env, monkeypatch, capsys):
    monkeypatch.setenv('BOOKING_BOT_TOKEN', TOKEN)
    seen: list[list[str]] = []

    async def fake_polling(bot, booking_bot, loop, languages):
        seen.append(list(languages))

    monkeypatch.setattr(cli, 'run_polling', fake_polling)
    cli.run(EXAMPLE)
    assert seen == [['en', 'ru']]
    assert 'бот остановлен' in capsys.readouterr().out


def test__demo__command_prints_summary(env, capsys):
    cli.demo(out=env / 'demo')
    assert 'офлайн-демо бота записи' in capsys.readouterr().out


def test__root_cli__booking_subcommand_registered():
    from autostudio.cli import app

    assert 'booking' in app

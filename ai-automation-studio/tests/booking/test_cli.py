import datetime as dt
import json
import logging
from pathlib import Path

import httpx
import pytest
import respx

from autostudio.booking import cli
from autostudio.booking.domain import Customer, Slot
from autostudio.booking.store import BookingStore
from autostudio.errors import ConfigError
from autostudio.notify import TelegramNotifier
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
    assert 'конфиг:' in out and 'секреты и ключ модели: не проверялись' in out
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


def test__check_environment__google_sink_and_notify_channels__each_secret_required(env, write_toml):
    text = EXAMPLE.read_text(encoding='utf-8').replace(
        'backend = "csv"', 'backend = "google"\nspreadsheet_id = "sheet-1"'
    )
    text = text.replace('console = false', 'console = false\ntelegram_chat_id = "42"\nwebhook_url_env = "HOOK"')
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


def test__describe__thin_config__warnings(env, write_toml):
    text = EXAMPLE.read_text(encoding='utf-8').replace('admin_chat_ids = [111111111]', 'admin_chat_ids = []')
    text = text.replace('contact_phone = "+995 555 12 34 56"\n', '')
    text = text.split('[[booking.faq]]')[0]
    config = cli.load_booking_config(write_toml(text))
    lines = cli.describe(config, cli.Settings(data_dir=env / 'data'), dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC))
    joined = '\n'.join(lines)
    assert 'admin_chat_ids пуст' in joined and 'FAQ пуст' in joined and 'contact_phone не задан' in joined
    assert 'модель fake' in joined


def test__agenda__existing_store__printed_readonly(env, capsys):
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


def test__chat__scripted_input__sandbox_used_live_data_untouched(env, monkeypatch, capsys):
    answers = iter(['3', '/quit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    cli.chat(EXAMPLE, data_dir=env / 'sandbox', language='ru')
    out = capsys.readouterr().out
    assert 'Здравствуйте' in out and 'Наши услуги' in out
    assert (env / 'sandbox' / 'demo-booking' / 'booking.sqlite').exists()
    assert not (env / 'data').exists()


def test__run__serve_returns__container_wired_and_stopped(env, monkeypatch, capsys):
    monkeypatch.setenv('BOOKING_BOT_TOKEN', TOKEN)
    seen: list[tuple] = []

    async def fake_serve(*, config, bot, booking_bot, loop, whatsapp, telegram_secret, port):
        seen.append((config.booking.languages, whatsapp, telegram_secret, port))

    monkeypatch.setattr(cli, 'serve', fake_serve)
    cli.run(EXAMPLE)
    assert seen == [(['en', 'ru'], None, None, 8080)]
    assert 'бот остановлен' in capsys.readouterr().out


def test__demo__command__summary_printed(env, capsys):
    cli.demo(out=env / 'demo')
    assert 'офлайн-демо бота записи' in capsys.readouterr().out


def test__root_cli__import__booking_subcommand_registered():
    from autostudio.cli import app

    assert 'booking' in app


API = f'https://api.telegram.org/bot{TOKEN}'


def test__check__offline_with_openai_and_no_key__passes_key_not_checked(env, monkeypatch, capsys):
    monkeypatch.setenv('AUTOSTUDIO_LLM_PROVIDER', 'openai')
    cli.check(EXAMPLE, offline=True)
    out = capsys.readouterr().out
    assert 'ключ модели: не проверялись' in out and 'модель: openai (gpt-5.4-mini)' in out
    with pytest.raises(ConfigError, match='OPENAI_API_KEY'):
        cli.check(EXAMPLE)  # без --offline ключ обязателен


def test__check__no_admins_and_no_notify_channel__config_error(env, write_toml, monkeypatch):
    text = EXAMPLE.read_text(encoding='utf-8').replace('admin_chat_ids = [111111111]', 'admin_chat_ids = []')
    path = write_toml(text.replace('console = false', 'console = true'))
    with pytest.raises(ConfigError, match='никто не узнает'):
        cli.check(path, offline=True)
    monkeypatch.setenv('BOOKING_BOT_TOKEN', TOKEN)
    with pytest.raises(ConfigError, match='никто не узнает'):
        cli.run(path)
    hooked = write_toml(text.replace('console = false', 'webhook_url_env = "HOOK"'), name='hooked.toml')
    cli.check(hooked, offline=True)  # вебхук — настоящий канал


def test__check__offline_and_online__refused(env):
    with pytest.raises(ConfigError, match='взаимоисключающи'):
        cli.check(EXAMPLE, offline=True, online=True)


@respx.mock
def test__check__online_token_alive_admin_started__ok(env, monkeypatch, capsys):
    monkeypatch.setenv('BOOKING_BOT_TOKEN', TOKEN)
    respx.get(f'{API}/getMe').mock(
        return_value=httpx.Response(200, json={'ok': True, 'result': {'username': 'demo_bot'}})
    )
    respx.get(f'{API}/getChat').mock(return_value=httpx.Response(200, json={'ok': True, 'result': {'id': 111111111}}))
    cli.check(EXAMPLE, online=True)
    out = capsys.readouterr().out
    assert 'бот @demo_bot отвечает' in out and 'чаты администраторов доступны (1)' in out and TOKEN not in out


@respx.mock
async def test__probe_telegram__admin_never_started_or_token_revoked__config_error_without_token():
    async with httpx.AsyncClient() as http:
        respx.get(f'{API}/getMe').mock(return_value=httpx.Response(200, json={'ok': True, 'result': {'username': 'b'}}))
        respx.get(f'{API}/getChat').mock(
            return_value=httpx.Response(400, json={'ok': False, 'description': 'Bad Request: chat not found'})
        )
        with pytest.raises(ConfigError, match='нажать /start') as caught:
            await cli.probe_telegram(http, TOKEN, [111111111])
        assert '111111111' in str(caught.value) and TOKEN not in str(caught.value)
        respx.get(f'{API}/getMe').mock(
            return_value=httpx.Response(401, json={'ok': False, 'description': 'Unauthorized'})
        )
        with pytest.raises(ConfigError, match='Unauthorized'):
            await cli.probe_telegram(http, TOKEN, [])
        respx.get(f'{API}/getMe').mock(side_effect=httpx.ConnectError(f'{API}/getMe'))
        with pytest.raises(ConfigError) as caught:
            await cli.probe_telegram(http, TOKEN, [])
        assert str(caught.value) == 'Telegram недоступен: ConnectError'
        respx.get(f'{API}/getMe').mock(return_value=httpx.Response(502, text='<html>bad gateway</html>'))
        with pytest.raises(ConfigError, match='не JSON'):
            await cli.probe_telegram(http, TOKEN, [])


def test__describe__idle_staff_console_notify_unwritable_csv__warnings(env, write_toml, monkeypatch):
    from autostudio.booking import providers

    text = EXAMPLE.read_text(encoding='utf-8')
    text = text.replace('wed = ["12:00-20:00"]\nthu = ["12:00-20:00"]\nfri = ["12:00-20:00"]\nsat = ["10:00-18:00"]',
                        'sun = ["12:00-20:00"]')  # fmt: skip
    text = text.replace('console = false', 'console = true')
    config = cli.load_booking_config(write_toml(text))
    monkeypatch.setattr(providers.os, 'access', lambda path, mode: False)
    lines = cli.describe(config, cli.Settings(data_dir=env / 'data'), dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC))
    joined = '\n'.join(lines)
    assert 'мастер Nika (nika) не работает ни в один день' in joined
    assert 'console = true: в боевом run уведомления в консоль не пишутся' in joined
    assert 'CSV-выгрузка не сможет писать' in joined
    assert f'таблица: csv → {env / "data" / "demo-booking" / "out"}' in joined


OLD_BOOKINGS_HEADER = 'Logged at,Action,Booking,Date,Time,Service,Staff,Customer,Phone,Price,Source\n'


def test__describe__bookings_csv_from_previous_version__warning_to_archive(env):
    from autostudio.booking.texts import BOOKINGS_HEADER

    config = cli.load_booking_config(EXAMPLE)
    out = env / 'data' / 'demo-booking' / 'out'
    out.mkdir(parents=True)
    # Первая версия писала 11 колонок (цена «40 GEL», без канала); заголовок пишется только в новый файл.
    (out / 'Bookings.csv').write_text(OLD_BOOKINGS_HEADER + 'row\n', encoding='utf-8')
    now = dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC)
    joined = '\n'.join(cli.describe(config, cli.Settings(data_dir=env / 'data'), now))
    assert 'Bookings.csv: заголовок от прежней версии или другого языка (11 колонок, сейчас 13)' in joined
    assert 'переименуйте файл' in joined
    header = BOOKINGS_HEADER[config.owner_language]
    assert cli.stale_bookings_header(out, header) is not None
    (out / 'Bookings.csv').write_text(','.join(header) + '\n', encoding='utf-8')
    assert cli.stale_bookings_header(out, header) is None
    (out / 'Bookings.csv').write_text('', encoding='utf-8')
    assert cli.stale_bookings_header(out, header) is None
    assert cli.stale_bookings_header(env / 'nowhere', header) is None


def test__describe__service_only_idle_staff__warning(env, write_toml):
    text = EXAMPLE.read_text(encoding='utf-8').replace('staff_ids = ["giorgi"]', 'staff_ids = ["nika"]')
    text = text.replace('wed = ["12:00-20:00"]\nthu = ["12:00-20:00"]\nfri = ["12:00-20:00"]\nsat = ["10:00-18:00"]',
                        'sun = ["12:00-20:00"]')  # fmt: skip
    config = cli.load_booking_config(write_toml(text))
    lines = cli.describe(config, cli.Settings(data_dir=env / 'data'), dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC))
    assert any(
        line.endswith('услуга combo: никто из её мастеров не работает — записаться на неё нельзя') for line in lines
    )


OPERATOR_TOKEN = '987654321:' + 'B' * 35


def run_root_cli(monkeypatch, *argv: str) -> int:
    from autostudio.cli import main

    monkeypatch.setattr('sys.argv', ['autostudio', *argv])
    # Корневой main() настраивает логирование процесса насовсем; в общем прогоне тестов
    # уровни логгеров возвращаются, чтобы не менять поведение соседних тестов.
    names = ('httpx', 'httpcore', 'hpack', 'aiogram.event')
    saved = {name: logging.getLogger(name).level for name in names}
    try:
        main()
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)
    return 0


# Корневой `main()` читает sys.argv — как в бою; cyclopts предупреждает об этом под pytest.
@pytest.mark.filterwarnings('ignore:Cyclopts application invoked without tokens')
@respx.mock
def test__check_online__root_cli_at_info__token_not_in_logs(env, monkeypatch, caplog, capsys):
    caplog.set_level(logging.INFO)  # как `autostudio` по умолчанию (AUTOSTUDIO_LOG_LEVEL=INFO)
    monkeypatch.setenv('BOOKING_BOT_TOKEN', TOKEN)
    respx.get(f'{API}/getMe').mock(return_value=httpx.Response(200, json={'ok': True, 'result': {'username': 'b'}}))
    respx.get(f'{API}/getChat').mock(return_value=httpx.Response(200, json={'ok': True, 'result': {'id': 1}}))
    assert run_root_cli(monkeypatch, 'booking', 'check', str(EXAMPLE), '--online') == 0
    assert 'чаты администраторов доступны' in capsys.readouterr().out
    # Проверяются сами записи, а не отформатированный текст: ядро маскирует токены в форматтере
    # (`logsafe`), а здесь важно, что запись с URL даже не создаётся.
    assert not any(TOKEN in record.getMessage() for record in caplog.records)
    # Ловушка настоящая: вне команды тот же запрос попадает в запись лога вместе с токеном.
    logging.getLogger('httpx').setLevel(logging.INFO)
    httpx.get(f'{API}/getMe')
    assert any(TOKEN in record.getMessage() for record in caplog.records)


@respx.mock
def test__run__notifications_over_httpx_at_debug__tokens_not_in_logs(env, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv('BOOKING_BOT_TOKEN', TOKEN)
    route = respx.post(f'https://api.telegram.org/bot{OPERATOR_TOKEN}/sendMessage').mock(
        return_value=httpx.Response(200, json={'ok': True})
    )

    async def fake_serve(**kwargs):
        # Так ядро шлёт сигнал оператору и уведомление в чат [notify], пока бот работает.
        async with httpx.AsyncClient() as http:
            await TelegramNotifier(http, OPERATOR_TOKEN, '42').send('⚠️ alert')

    monkeypatch.setattr(cli, 'serve', fake_serve)
    cli.run(EXAMPLE)
    assert route.called
    assert OPERATOR_TOKEN not in caplog.text and TOKEN not in caplog.text


def test__chat__session__transport_logs_quiet(env, monkeypatch):
    seen: list[int] = []

    async def fake_repl(engine, *, admin, language):
        seen.append(logging.getLogger('httpx').getEffectiveLevel())
        return 0

    monkeypatch.setattr(cli, 'repl', fake_repl)
    cli.chat(EXAMPLE, data_dir=env / 'sandbox')
    assert seen == [logging.WARNING]


def test__quiet_transport_logs__raised_then_restored__higher_level_kept():
    transport, pool, frames = (logging.getLogger(name) for name in cli.TRANSPORT_LOGGERS)
    saved = transport.level, pool.level, frames.level
    transport.setLevel(logging.NOTSET)
    pool.setLevel(logging.ERROR)
    try:
        with cli.quiet_transport_logs():
            assert transport.level == logging.WARNING and pool.level == logging.ERROR
            assert frames.getEffectiveLevel() >= logging.WARNING
        assert transport.level == logging.NOTSET and pool.level == logging.ERROR
    finally:
        for item, level in zip((transport, pool, frames), saved, strict=True):
            item.setLevel(level)


CHANNELS = """
[booking.telegram]
mode = "webhook"

[booking.server]
public_url = "https://bot.example.test"

[booking.calendar]
calendar_id = "salon@example.test"
staff = { giorgi = "giorgi@example.test" }

[booking.whatsapp]
phone_number_id = "106540352242922"
"""
GRAPH = 'https://graph.facebook.com/v25.0/106540352242922'
CHANNEL_SECRETS = {
    'BOOKING_TELEGRAM_WEBHOOK_SECRET': 'tg_secret-1',
    'WHATSAPP_TOKEN': 'EAAG-token',
    'WHATSAPP_APP_SECRET': 'meta-app-secret-77',
    'WHATSAPP_VERIFY_TOKEN': 'hub-verify-42',
}


def channel_config(write_toml, extra: str = CHANNELS):
    return write_toml(EXAMPLE.read_text(encoding='utf-8') + extra)


def key_file(path: Path) -> Path:
    path.write_text(json.dumps({'type': 'service_account', 'client_email': 'bot@demo.iam.gserviceaccount.com'}))
    return path


def test__check_environment__webhook_calendar_whatsapp__each_secret_required(env, write_toml):
    config = cli.load_booking_config(channel_config(write_toml))
    environ = {'BOOKING_BOT_TOKEN': TOKEN}
    with pytest.raises(ConfigError, match='BOOKING_TELEGRAM_WEBHOOK_SECRET'):
        cli.check_environment(config, environ)
    environ['BOOKING_TELEGRAM_WEBHOOK_SECRET'] = 'has spaces'
    with pytest.raises(ConfigError, match='1–256 символов'):
        cli.check_environment(config, environ)
    environ['BOOKING_TELEGRAM_WEBHOOK_SECRET'] = 'tg_secret-1'
    with pytest.raises(ConfigError, match='GOOGLE_APPLICATION_CREDENTIALS'):
        cli.check_environment(config, environ)
    environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(env / 'missing.json')
    with pytest.raises(ConfigError, match='файл ключа'):
        cli.check_environment(config, environ)
    environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(key_file(env / 'key.json'))
    with pytest.raises(ConfigError, match='WHATSAPP_TOKEN'):
        cli.check_environment(config, environ)
    environ.update(CHANNEL_SECRETS)
    lines = cli.check_environment(config, environ)
    assert 'Telegram: вебхук https://bot.example.test/telegram/webhook, секрет задан' in lines
    assert any('делится календарями с bot@demo.iam.gserviceaccount.com' in line for line in lines)
    assert lines[-1].endswith('Callback URL в кабинете Meta — https://bot.example.test/whatsapp/webhook')
    assert not any(value in '\n'.join(lines) for value in CHANNEL_SECRETS.values())


def test__describe__channels__lines_and_warnings(env, write_toml):
    config = cli.load_booking_config(channel_config(write_toml))
    lines = cli.describe(config, cli.Settings(data_dir=env / 'data'), dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC))
    joined = '\n'.join(lines)
    assert 'Telegram: вебхук' in joined
    assert 'Google Calendar: календарей 2, кеш 60 с, события записей: пишутся; giorgi → giorgi@example.test' in joined
    assert 'WhatsApp: номер 106540352242922, Graph API v25.0, шаблон напоминания не задан' in joined
    assert 'ближайшие свободные слоты (без занятости из календаря):' in joined
    assert 'WhatsApp без reminder_template' in joined
    staff_only = CHANNELS.replace('calendar_id = "salon@example.test"\n', '').replace(
        'public_url = "https://bot.example.test"', ''
    )
    staff_only = staff_only.replace('mode = "webhook"', 'mode = "polling"')
    other = cli.load_booking_config(channel_config(write_toml, staff_only))
    warnings = cli.describe(other, cli.Settings(data_dir=env / 'data'), dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC))
    text = '\n'.join(warnings)
    assert 'у мастеров nika нет календаря' in text and 'public_url не задан' in text


@respx.mock
async def test__probe_whatsapp__number_visible_or_config_error():
    config = cli.load_booking_config(EXAMPLE)
    async with httpx.AsyncClient() as http:
        assert await cli.probe_whatsapp(http, config, 'EAAG') == []
    from tests.booking.helpers import make_config

    wa = make_config(whatsapp={'phone_number_id': '106540352242922'})
    route = respx.get(GRAPH).mock(
        side_effect=[
            httpx.Response(
                200,
                json={'display_phone_number': '+995 32 2 00 00 00', 'verified_name': 'Demo', 'quality_rating': 'GREEN'},
            ),
            httpx.Response(401, json={'error': {'code': 190}}),
            httpx.Response(200, text='<html>'),
            httpx.ConnectError('down'),
        ]
    )
    async with httpx.AsyncClient() as http:
        assert await cli.probe_whatsapp(http, wa, 'EAAG') == [
            'WhatsApp: номер +995 32 2 00 00 00 (Demo), качество GREEN'
        ]
        with pytest.raises(ConfigError, match='не виден с этим токеном'):
            await cli.probe_whatsapp(http, wa, 'EAAG')
        with pytest.raises(ConfigError, match='не JSON'):
            await cli.probe_whatsapp(http, wa, 'EAAG')
        with pytest.raises(ConfigError, match='ConnectError') as caught:
            await cli.probe_whatsapp(http, wa, 'EAAG')
    assert 'EAAG' not in str(caught.value)
    assert route.calls[0].request.headers['Authorization'] == 'Bearer EAAG'
    assert route.calls[0].request.url.params['fields'] == 'display_phone_number,verified_name,quality_rating'


async def test__probe_calendar__readable_not_shared_or_down():
    from autostudio.booking.gcal import FakeCalendar
    from tests.booking.helpers import make_config

    now = dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC)
    assert await cli.probe_calendar(FakeCalendar(), make_config(), now) == []
    config = make_config(calendar={'calendar_id': 'salon@x', 'staff': {'giorgi': 'g@x'}})
    fake = FakeCalendar()
    assert await cli.probe_calendar(fake, config, now) == ['Google Calendar: календари читаются (2)']
    fake.missing.add('salon@x')
    with pytest.raises(ConfigError, match=r'salon@x \(notFound\)'):
        await cli.probe_calendar(fake, config, now)
    fake.fail = True
    with pytest.raises(ConfigError, match='Google Calendar'):
        await cli.probe_calendar(fake, config, now)


def test__server_port__env_override_or_config(write_toml, env):
    config = cli.load_booking_config(channel_config(write_toml))
    assert cli.server_port(config, {}) == 8080
    assert cli.server_port(config, {'BOOKING_WEBHOOK_PORT': ' 9000 '}) == 9000
    for bad in ('http', '0', '99999', '٣'):
        with pytest.raises(ConfigError, match='BOOKING_WEBHOOK_PORT'):
            cli.server_port(config, {'BOOKING_WEBHOOK_PORT': bad})


def test__check__sibling_env__secrets_picked_up_outside_docker(env, monkeypatch, tmp_path, capsys):
    client = tmp_path / 'clients' / 'acme'
    client.mkdir(parents=True)
    config = client / 'booking.toml'
    config.write_text(EXAMPLE.read_text(encoding='utf-8'), encoding='utf-8')
    (client / '.env').write_text(f'BOOKING_BOT_TOKEN={TOKEN}\n', encoding='utf-8')
    # setenv + delenv: monkeypatch запомнит исходное состояние и вернёт его после теста.
    monkeypatch.setenv('BOOKING_BOT_TOKEN', 'x')
    monkeypatch.delenv('BOOKING_BOT_TOKEN')
    cli.check(config)
    assert 'токен бота: BOOKING_BOT_TOKEN задан' in capsys.readouterr().out


def test__run__webhook_and_whatsapp__serve_gets_secret_port_and_whatsapp(env, monkeypatch, write_toml):
    for name, value in {**CHANNEL_SECRETS, 'BOOKING_BOT_TOKEN': TOKEN, 'BOOKING_WEBHOOK_PORT': '9100'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', str(key_file(env / 'key.json')))
    seen: list[tuple] = []

    async def fake_serve(*, config, bot, booking_bot, loop, whatsapp, telegram_secret, port):
        seen.append((whatsapp is not None, telegram_secret, port, config.booking.telegram.mode))

    monkeypatch.setattr(cli, 'serve', fake_serve)
    cli.run(channel_config(write_toml))
    assert seen == [(True, 'tg_secret-1', 9100, 'webhook')]


@respx.mock
def test__check_online__whatsapp_and_calendar_probed(env, monkeypatch, write_toml, capsys):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    key = env / 'sa.json'
    key.write_text(
        json.dumps(
            {
                'type': 'service_account',
                'client_email': 'bot@demo.iam.gserviceaccount.com',
                'private_key': pem.decode(),
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        )
    )
    for name, value in {
        **CHANNEL_SECRETS,
        'BOOKING_BOT_TOKEN': TOKEN,
        'GOOGLE_APPLICATION_CREDENTIALS': str(key),
    }.items():
        monkeypatch.setenv(name, value)
    respx.get(f'{API}/getMe').mock(
        return_value=httpx.Response(200, json={'ok': True, 'result': {'username': 'acme_bot'}})
    )
    respx.get(f'{API}/getChat').mock(return_value=httpx.Response(200, json={'ok': True, 'result': {'id': 1}}))
    respx.get(GRAPH).mock(
        return_value=httpx.Response(200, json={'display_phone_number': '+995', 'verified_name': 'Demo'})
    )
    respx.post('https://oauth2.googleapis.com/token').mock(
        return_value=httpx.Response(200, json={'access_token': 'ya29.x', 'expires_in': 3600})
    )
    respx.post('https://www.googleapis.com/calendar/v3/freeBusy').mock(
        return_value=httpx.Response(200, json={'calendars': {'salon@example.test': {}, 'giorgi@example.test': {}}})
    )
    cli.check(channel_config(write_toml), online=True)
    out = capsys.readouterr().out
    assert 'https://t.me/acme_bot?start=site' in out
    assert 'WhatsApp: номер +995 (Demo), качество —' in out and 'Google Calendar: календари читаются (2)' in out

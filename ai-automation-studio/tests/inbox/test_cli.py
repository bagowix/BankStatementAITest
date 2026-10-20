import asyncio
from pathlib import Path

import httpx
import msgspec
import pytest
import respx

from autostudio.errors import ConfigError
from autostudio.events import EventLog
from autostudio.inbox import cli, imap
from autostudio.inbox.config import InboxSection
from autostudio.inbox.errors import InboxError
from autostudio.inbox.pipeline import RunResult
from tests.inbox.fake_imap import FakeImapServer
from tests.inbox.helpers import NOW, make_email

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'inbox' / 'client.toml'
FIXTURES = EXAMPLE.parent / 'emails'
TENANT = 'contoso.onmicrosoft.com'
MS_ENV = (('INBOX_OAUTH_TENANT_ID', TENANT), ('INBOX_OAUTH_CLIENT_ID', 'app'), ('INBOX_OAUTH_CLIENT_SECRET', 's'))

CONFIG = """
[client]
id = "acme-tours"
name = "Acme Tours"
timezone = "Asia/Tbilisi"
language = "en"

[sink]
csv_dir = "{out}"

[inbox]
imap_host = "{host}"
drafts_folder = "Drafts"
notify_on_urgency = "normal"

[inbox.company]
name = "Acme Tours"
facts = ["Kazbegi day tour costs 120 GEL per person."]
"""


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ('AUTOSTUDIO_LLM_PROVIDER', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'AUTOSTUDIO_OPERATOR_CHAT_ID'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('INBOX_IMAP_USER', 'info@acme.example')
    monkeypatch.setenv('INBOX_IMAP_PASSWORD', 'app-pass-1234')
    monkeypatch.setenv('AUTOSTUDIO_DATA_DIR', str(tmp_path / 'data'))
    return monkeypatch


@pytest.fixture
def server(env):
    srv = FakeImapServer(messages={1: make_email(subject='Tour price', body='What is the price?')})
    env.setattr(imap, '_default_connector', lambda host, port, timeout: srv)
    # Боевой запуск на заглушке — только если она выбрана явно.
    env.setenv('AUTOSTUDIO_LLM_PROVIDER', 'fake')
    return srv


def _config(tmp_path: Path, host: str = 'imap.example.com') -> Path:
    path = tmp_path / 'inbox.toml'
    path.write_text(CONFIG.format(out=tmp_path / 'out', host=host), encoding='utf-8')
    return path


def test__app__commands_registered():
    names = set(cli.app)
    assert {'check', 'run', 'dry-run', 'demo'} <= names


def test__check__skip_env__example_ok(capsys):
    cli.check(EXAMPLE, skip_env=True)
    out = capsys.readouterr().out
    assert '✓ конфиг' in out and 'booking_request 🔔 ✍' in out and 'invoice →Invoices' in out


def test__check__missing_env__all_problems_listed_then_error(monkeypatch, capsys):
    for name in ('INBOX_IMAP_USER', 'INBOX_IMAP_PASSWORD', 'AUTOSTUDIO_LLM_PROVIDER'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AUTOSTUDIO_LLM_PROVIDER', 'openai')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(ConfigError, match='проблем 3'):
        cli.check(EXAMPLE)
    out = capsys.readouterr().out
    assert 'INBOX_IMAP_USER' in out and 'INBOX_IMAP_PASSWORD' in out and 'OPENAI_API_KEY' in out


def test__check__env_ok_fake_model__warns_and_passes(env, tmp_path, capsys):
    cli.check(_config(tmp_path))
    out = capsys.readouterr().out
    assert 'модель-заглушка' in out and '✓ конфиг в порядке' in out


def test__check__sheet_and_notify_env_missing__reported(env, tmp_path, capsys):
    path = tmp_path / 'g.toml'
    text = CONFIG.format(out=tmp_path, host='imap.example.com').replace(
        '[sink]\ncsv_dir',
        '[notify]\ntelegram_chat_id = "1"\nwebhook_url_env = "HOOK_URL"\n\n[sink]\nbackend = "google"\nspreadsheet_id = "abc"\ncsv_dir',
    )
    path.write_text(text, encoding='utf-8')
    env.delenv('GOOGLE_APPLICATION_CREDENTIALS', raising=False)
    env.delenv('AUTOSTUDIO_NOTIFY_BOT_TOKEN', raising=False)
    env.delenv('HOOK_URL', raising=False)
    with pytest.raises(ConfigError):
        cli.check(path)
    out = capsys.readouterr().out
    assert 'GOOGLE_APPLICATION_CREDENTIALS' in out and 'AUTOSTUDIO_NOTIFY_BOT_TOKEN' in out and 'HOOK_URL' in out


def test__check__outlook_host_with_password__problem_points_to_microsoft_oauth(env, tmp_path, capsys):
    with pytest.raises(ConfigError):
        cli.check(_config(tmp_path, host='outlook.office365.com'), skip_env=True)
    out = capsys.readouterr().out
    assert 'auth = "microsoft_oauth"' in out and 'IMAP.AccessAsApp' in out


def test__check__no_facts__warning(tmp_path, capsys):
    path = tmp_path / 'nf.toml'
    path.write_text(
        CONFIG.format(out=tmp_path, host='imap.example.com').replace(
            'facts = ["Kazbegi day tour costs 120 GEL per person."]', ''
        ),
        encoding='utf-8',
    )
    cli.check(path, skip_env=True)
    assert 'фактов о компании нет' in capsys.readouterr().out


def test__check__connect__folders_verified(server, tmp_path, capsys):
    cli.check(_config(tmp_path), connect=True)
    out = capsys.readouterr().out
    assert '✓ вход в imap.example.com' in out and '✓ папка черновиков: Drafts' in out and '✓ готово' in out
    assert server.selected == ('"INBOX"', True) and server.logged_out


def test__check__connect__drafts_missing__special_use_suggested(server, tmp_path, capsys):
    server.folders = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"']
    path = tmp_path / 'm.toml'
    text = CONFIG.format(out=tmp_path, host='imap.gmail.com') + (
        '\n[[inbox.categories]]\nid = "invoice"\ndescription = "Invoices."\nmove_to = "Invoices"\n'
        '\n[[inbox.categories]]\nid = "marketing"\ndescription = "Ads."\n'
        '\n[[inbox.categories]]\nid = "other"\ndescription = "Else."\n'
    )
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ConfigError):
        cli.check(path, connect=True)
    out = capsys.readouterr().out
    assert 'inbox.drafts_folder = "[Gmail]/Drafts"' in out and 'папки «Invoices» нет' in out


def test__check__connect__login_rejected__provider_hint(server, tmp_path):
    server.password = 'other'
    with pytest.raises(ConfigError, match='пароль приложения'):
        cli.check(_config(tmp_path, host='imap.yandex.ru'), connect=True)


@pytest.mark.parametrize(
    ('host', 'needle'),
    [
        ('imap.gmail.com', 'apppasswords'),
        ('imap.yandex.ru', 'Пароли приложений'),
        ('imap.zoho.eu', 'Application-Specific'),
        ('imap.mail.ru', 'внешнего приложения'),
        ('outlook.office365.com', 'microsoft_oauth'),
        ('mail.example.com', 'IMAP включён'),
    ],
)
def test__provider_hint__by_host(host, needle):
    assert needle in cli.provider_hint(host)


def test__run__one_shot__processes_and_logs_run_ok(server, tmp_path, capsys):
    cli.run(_config(tmp_path))
    out = capsys.readouterr().out
    assert 'inbox: новых 1' in out and 'черновиков 1' in out and '📩 New inquiry from Jane Doe' in out
    assert 'модель-заглушка' in out  # заглушка выбрана явно — и об этом сказано в выводе
    mailbox, flags, _, message = server.appended[0]
    assert (mailbox, flags) == ('"Drafts"', '(\\Draft \\Seen)') and b'In-Reply-To: <m1@client.example>' in message
    assert server.flags[1] == set()  # письмо клиента осталось непрочитанным
    assert (tmp_path / 'out' / 'Inbox.csv').exists()
    log = EventLog(tmp_path / 'data' / 'acme-tours' / 'events.sqlite')
    kinds = [event.kind for event in log.between(NOW.replace(year=2000), NOW.replace(year=2100))]
    assert 'system.run_ok' in kinds and 'inbox.triaged' in kinds


def test__run__mailbox_failure__run_failed_and_exit_error(server, tmp_path):
    server.password = 'rotated'
    with pytest.raises(InboxError, match='ошибкой'):
        cli.run(_config(tmp_path))
    log = EventLog(tmp_path / 'data' / 'acme-tours' / 'events.sqlite')
    kinds = [event.kind for event in log.between(NOW.replace(year=2000), NOW.replace(year=2100))]
    assert kinds == ['system.run_failed']


def test__run__every_minutes__loops_until_interrupted(server, tmp_path, monkeypatch, capsys):
    async def stop(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.asyncio, 'sleep', stop)
    cli.run(_config(tmp_path), every_minutes=5)
    assert 'inbox: остановлено' in capsys.readouterr().out


def test__run__bad_interval__config_error(tmp_path):
    with pytest.raises(ConfigError):
        cli.run(_config(tmp_path), every_minutes=0)


def test__dry_run_command__table_without_side_effects(server, tmp_path, capsys):
    for uid, path in enumerate(sorted(FIXTURES.glob('*.eml')), start=10):
        server.messages[uid] = path.read_bytes()
    cli.dry_run_command(_config(tmp_path), limit=5)
    out = capsys.readouterr().out
    assert 'Received' in out and 'Would do' in out and 'модель-заглушка' in out
    assert 'stub' in out and ' AI ' not in out  # ключевые слова не выдаются за ИИ
    assert 'вызовов модели' in out
    assert server.appended == [] and server.copied == []
    assert not (tmp_path / 'out' / 'Inbox.csv').exists()
    assert all(command not in ('STORE', 'EXPUNGE', 'APPEND', 'COPY') for command, _ in server.commands)


def test__dry_run_command__bad_limit__config_error(tmp_path):
    with pytest.raises(ConfigError):
        cli.dry_run_command(_config(tmp_path), limit=0)


def test__demo_command__prints_summary(tmp_path, capsys):
    cli.demo(out=tmp_path)
    assert 'AI inbox triage — offline demo' in capsys.readouterr().out


def test__app__parses_flags(tmp_path, monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, 'run_demo', lambda out: _record(seen, out))
    cli.app(['demo', '--out', str(tmp_path)], result_action='return_value', exit_on_error=False)
    assert seen['out'] == tmp_path


async def _record(seen: dict[str, object], out: Path) -> str:
    seen['out'] = out
    await asyncio.sleep(0)
    return 'ok'


def test__format_result__aborted_errors_and_invoices():
    text = cli.format_result(RunResult(folder='INBOX', aborted='3 подряд', errors=['таблица']))
    assert '✗ остановлено: 3 подряд' in text and '✗ таблица' in text and 'категории: —' in text
    assert 'счета' not in text
    text = cli.format_result(RunResult(folder='INBOX', invoices_verified=2, invoices_unverified=1, invoice_rows=3))
    assert 'счета: сверено 2, на ручную проверку 1, строк во вкладку счетов 3' in text


def test__run__model_provider_not_set__refuses_to_run_on_stub(env, tmp_path):
    with pytest.raises(ConfigError, match='AUTOSTUDIO_LLM_PROVIDER'):
        cli.run(_config(tmp_path))


def test__check__example__owner_and_operator_channels_reported(env, capsys):
    env.setenv('AUTOSTUDIO_LLM_PROVIDER', 'fake')
    cli.check(EXAMPLE)
    out = capsys.readouterr().out
    assert 'сигналы владельцу: консоль сервера' in out
    assert '⚠ сигналы о горячих лидах уйдут только в лог сервера' in out
    assert '⚠ канал оператора не настроен' in out and 'подозрительные письма' in out
    assert 'данные клиента:' in out and 'demo-inbox' in out


def test__check__telegram_and_operator_configured__no_channel_warnings(env, tmp_path, capsys):
    env.setenv('AUTOSTUDIO_NOTIFY_BOT_TOKEN', '1:abc')
    env.setenv('AUTOSTUDIO_OPERATOR_CHAT_ID', '42')
    env.setenv('AUTOSTUDIO_OPERATOR_BOT_TOKEN', '2:def')
    path = tmp_path / 't.toml'
    path.write_text(
        CONFIG.format(out=tmp_path, host='imap.example.com').replace(
            '[sink]', '[notify]\ntelegram_chat_id = "777"\n\n[sink]'
        ),
        encoding='utf-8',
    )
    cli.check(path)
    out = capsys.readouterr().out
    assert 'сигналы владельцу: telegram 777' in out and 'сигналы оператору: telegram 42' in out
    assert '⚠ сигналы о горячих' not in out and '⚠ канал оператора' not in out


def test__check__missing_env__hint_how_to_load_client_env(monkeypatch, capsys):
    for name in ('INBOX_IMAP_USER', 'INBOX_IMAP_PASSWORD'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AUTOSTUDIO_LLM_PROVIDER', 'fake')
    with pytest.raises(ConfigError):
        cli.check(EXAMPLE)
    assert 'clients/<id>/.env подхватывается сам' in capsys.readouterr().out


def test__check__connect__server_reason_shown_not_only_password_hint(server, tmp_path):
    import imaplib

    server.fail['LOGIN'] = imaplib.IMAP4.error("b'[ALERT] Too many simultaneous connections. (Failure)'")
    with pytest.raises(ConfigError, match='Too many simultaneous connections'):
        cli.check(_config(tmp_path, host='imap.gmail.com'), connect=True)


def test__dry_run_command__unreadable_message__error_row_and_count(server, tmp_path, capsys):
    server.messages[2] = b'\x00\xff garbage that is not an email'
    cli.dry_run_command(_config(tmp_path), limit=5)
    captured = capsys.readouterr()
    assert 'ERROR' in captured.out and 'с ошибкой: 1' in captured.out
    assert 'разобрано 2/2' in captured.err


def test__format_dry_rows__real_model__labelled_ai():
    from autostudio.inbox.pipeline import DryRow

    row = DryRow(
        date=NOW, sender='a@b.example', subject='s', category='lead', urgency='high', confidence=0.9,
        source='model', would=[], notes=[],
    )  # fmt: skip
    assert ' AI ' in cli.format_dry_rows([row], NOW.tzinfo or imap.dt.UTC)


def test__require_model__real_provider__passes_silently(capsys):
    from autostudio.settings import Settings

    settings = Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'openai', 'OPENAI_API_KEY': 'sk-test'})
    cli.require_model(settings, {'AUTOSTUDIO_LLM_PROVIDER': 'openai'})
    assert capsys.readouterr().out == ''


def test__run__copy_folder_missing__run_failed_operator_signal_and_error_exit(server, tmp_path):
    path = tmp_path / 'c.toml'
    path.write_text(
        CONFIG.format(out=tmp_path / 'out', host='imap.example.com')
        + '\n[[inbox.categories]]\nid = "invoice"\ndescription = "Invoices."\nmove_to = "Archive"\n'
        + '\n[[inbox.categories]]\nid = "marketing"\ndescription = "Ads."\n'
        + '\n[[inbox.categories]]\nid = "other"\ndescription = "Else."\n',
        encoding='utf-8',
    )
    server.messages[1] = make_email(subject='Invoice INV-1', body='Please find attached invoice INV-1 for payment.')
    with pytest.raises(InboxError, match='ошибкой'):
        cli.run(path)
    log = EventLog(tmp_path / 'data' / 'acme-tours' / 'events.sqlite')
    kinds = [event.kind for event in log.between(NOW.replace(year=2000), NOW.replace(year=2100))]
    assert kinds == ['system.run_failed']


def test__check__sibling_env_next_to_config__loaded_before_settings(monkeypatch, tmp_path, capsys):
    for name in ('INBOX_IMAP_USER', 'INBOX_IMAP_PASSWORD', 'AUTOSTUDIO_LLM_PROVIDER', 'AUTOSTUDIO_OPERATOR_CHAT_ID'):
        monkeypatch.delenv(name, raising=False)
    path = _config(tmp_path)
    (tmp_path / '.env').write_text(
        'INBOX_IMAP_USER=info@acme.example\nINBOX_IMAP_PASSWORD=app-pass\nAUTOSTUDIO_LLM_PROVIDER=fake\n',
        encoding='utf-8',
    )
    cli.check(path)
    out = capsys.readouterr().out
    assert '.env рядом с конфигом: загружено переменных 3' in out and 'модель: fake' in out
    assert '✓ конфиг в порядке' in out


def test__check__google_oauth_without_variables__all_oauth_names_listed(env, tmp_path, capsys):
    path = tmp_path / 'g.toml'
    path.write_text(
        CONFIG.format(out=tmp_path, host='imap.gmail.com').replace('[inbox]\n', '[inbox]\nauth = "google_oauth"\n'),
        encoding='utf-8',
    )
    for name in ('INBOX_OAUTH_CLIENT_ID', 'INBOX_OAUTH_CLIENT_SECRET', 'INBOX_OAUTH_REFRESH_TOKEN'):
        env.delenv(name, raising=False)
    with pytest.raises(ConfigError, match='проблем 3'):
        cli.check(path)
    out = capsys.readouterr().out
    assert 'вход: OAuth Google Workspace' in out and 'INBOX_OAUTH_REFRESH_TOKEN' in out


def test__check__oauth_method_on_unexpected_host__warning_not_error(env, tmp_path, capsys):
    path = tmp_path / 'w.toml'
    path.write_text(
        CONFIG.format(out=tmp_path, host='imap.gmail.com').replace('[inbox]\n', '[inbox]\nauth = "microsoft_oauth"\n'),
        encoding='utf-8',
    )
    cli.check(path, skip_env=True)
    assert 'у Exchange Online это outlook.office365.com' in capsys.readouterr().out
    _, warnings = cli.auth_problems(
        msgspec.convert({'imap_host': 'imap.yandex.ru', 'auth': 'google_oauth', 'company': {'name': 'A'}}, InboxSection)
    )
    assert 'imap.gmail.com' in warnings[0]


def _microsoft_config(tmp_path: Path) -> Path:
    path = tmp_path / 'm365.toml'
    path.write_text(
        CONFIG.format(out=tmp_path, host='outlook.office365.com').replace(
            '[inbox]\n', '[inbox]\nauth = "microsoft_oauth"\n'
        ),
        encoding='utf-8',
    )
    return path


@respx.mock
def test__check__connect_microsoft_oauth__token_then_xoauth2_login(env, server, tmp_path, capsys):
    server.token = 'eyJ.ms-token'
    route = respx.post(f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token').mock(
        return_value=httpx.Response(200, json={'access_token': 'eyJ.ms-token', 'expires_in': 3599})
    )
    for name, value in MS_ENV:
        env.setenv(name, value)
    cli.check(_microsoft_config(tmp_path), connect=True)
    out = capsys.readouterr().out
    assert '✓ вход в outlook.office365.com' in out and route.call_count == 1
    assert ('AUTHENTICATE', ('XOAUTH2',)) in server.commands and not any(c == 'LOGIN' for c, _ in server.commands)


@respx.mock
def test__check__connect_microsoft_oauth_rejected_by_imap__exchange_checklist_hint(env, server, tmp_path):
    server.token = 'something-else'
    respx.post(f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token').mock(
        return_value=httpx.Response(200, json={'access_token': 'eyJ.ms-token', 'expires_in': 3599})
    )
    for name, value in MS_ENV:
        env.setenv(name, value)
    with pytest.raises(ConfigError) as caught:
        cli.check(_microsoft_config(tmp_path), connect=True)
    message = str(caught.value)
    assert 'Add-MailboxPermission' in message and 'eyJ.ms-token' not in message


def test__run__previous_run_holds_lock__skipped_not_failed_exit_ok(server, tmp_path, capsys):
    import datetime as dt

    from autostudio.inbox.store import InboxStore

    store = InboxStore(tmp_path / 'data' / 'acme-tours' / 'inbox.sqlite')
    assert store.acquire_lock(dt.datetime.now(dt.UTC), dt.timedelta(hours=1))
    cli.run(_config(tmp_path))
    assert 'предыдущий запуск ещё идёт — пропуск' in capsys.readouterr().out
    log = EventLog(tmp_path / 'data' / 'acme-tours' / 'events.sqlite')
    kinds = [event.kind for event in log.between(NOW.replace(year=2000), NOW.replace(year=2100))]
    assert kinds == ['system.run_skipped'] and server.commands == []


def test__oauth_google__prints_env_lines_once_with_names_from_config(tmp_path, monkeypatch, capsys):
    from autostudio.inbox.oauth import GoogleConsent

    calls: list[tuple[Path, int, bool]] = []

    def consent(path: Path, *, port: int, open_browser: bool) -> GoogleConsent:
        calls.append((path, port, open_browser))
        return GoogleConsent(client_id='cid', client_secret='csecret', refresh_token='1//rt')

    monkeypatch.setattr(cli, 'run_google_consent', consent)
    cli.oauth_google(client_secrets=tmp_path / 'cs.json', no_browser=True)
    out = capsys.readouterr().out
    assert 'INBOX_OAUTH_CLIENT_ID=cid\nINBOX_OAUTH_CLIENT_SECRET=csecret\nINBOX_OAUTH_REFRESH_TOKEN=1//rt' in out
    assert out.count('1//rt') == 1 and 'auth = "google_oauth"' in out
    assert calls == [(tmp_path / 'cs.json', 0, False)]
    path = tmp_path / 'c.toml'
    path.write_text(
        CONFIG.format(out=tmp_path, host='imap.gmail.com').replace(
            '[inbox]\n', '[inbox]\noauth_refresh_token_env = "ACME_GOOGLE_RT"\n'
        ),
        encoding='utf-8',
    )
    cli.oauth_google(client_secrets=tmp_path / 'cs.json', config=path)
    assert 'ACME_GOOGLE_RT=1//rt' in capsys.readouterr().out


@pytest.mark.parametrize(
    ('auth', 'needle'), [('google_oauth', 'https://mail.google.com/'), ('microsoft_oauth', 'IMAP.AccessAsApp')]
)
def test__provider_hint__oauth_methods__provider_checklist(auth, needle):
    assert needle in cli.provider_hint('imap.example.com', auth)


def test__app__oauth_google_command_registered():
    assert 'oauth-google' in set(cli.app)

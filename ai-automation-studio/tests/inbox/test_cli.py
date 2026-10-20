import asyncio
from pathlib import Path

import pytest

from autostudio.errors import ConfigError
from autostudio.events import EventLog
from autostudio.inbox import cli, imap
from autostudio.inbox.errors import InboxError
from autostudio.inbox.pipeline import RunResult
from tests.inbox.fake_imap import FakeImapServer
from tests.inbox.helpers import NOW, make_email

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'inbox' / 'client.toml'
FIXTURES = EXAMPLE.parent / 'emails'

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


def test__check__outlook_host__unsupported_with_workaround(env, tmp_path, capsys):
    with pytest.raises(ConfigError):
        cli.check(_config(tmp_path, host='outlook.office365.com'), skip_env=True)
    assert 'автопересылку' in capsys.readouterr().out


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
        ('outlook.office365.com', 'не поддерживает'),
        ('mail.example.com', 'IMAP включён'),
    ],
)
def test__provider_hint__by_host(host, needle):
    assert needle in cli.provider_hint(host)


def test__run__one_shot__processes_and_logs_run_ok(server, tmp_path, capsys):
    cli.run(_config(tmp_path))
    out = capsys.readouterr().out
    assert 'inbox: новых 1' in out and 'черновиков 1' in out and '🔥 New lead' in out
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


def test__format_result__locked_aborted_errors():
    assert 'пропуск' in cli.format_result(RunResult(folder='INBOX', locked=True))
    text = cli.format_result(RunResult(folder='INBOX', aborted='3 подряд', errors=['таблица']))
    assert '✗ остановлено: 3 подряд' in text and '✗ таблица' in text and 'категории: —' in text

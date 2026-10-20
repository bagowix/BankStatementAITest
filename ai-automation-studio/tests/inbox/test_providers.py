from dishka import make_async_container

from autostudio.inbox.config import InboxConfig
from autostudio.inbox.imap import ImapMailBox
from autostudio.inbox.pipeline import InboxDeps
from autostudio.inbox.providers import InboxProvider
from autostudio.llm import FakeLLM
from autostudio.providers import CoreProvider
from autostudio.settings import Settings
from tests.inbox.helpers import make_config


async def test__inbox_provider__builds_deps_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv('INBOX_IMAP_USER', 'info@acme.example')
    monkeypatch.setenv('INBOX_IMAP_PASSWORD', 'app-pass')
    settings = Settings.from_env({'AUTOSTUDIO_DATA_DIR': str(tmp_path)})
    config: InboxConfig = make_config()
    container = make_async_container(CoreProvider(), InboxProvider(), context={Settings: settings, InboxConfig: config})
    deps = await container.get(InboxDeps)
    assert isinstance(deps.mailbox, ImapMailBox) and deps.mailbox_user == 'info@acme.example'
    assert isinstance(deps.llm, FakeLLM)
    assert deps.operator is None
    assert (tmp_path / 'acme-tours' / 'inbox.sqlite').exists()
    assert (tmp_path / 'acme-tours' / 'events.sqlite').exists()
    await container.close()

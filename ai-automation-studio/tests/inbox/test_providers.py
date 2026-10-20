import msgspec
import openai
from dishka import make_async_container

from autostudio.inbox import providers
from autostudio.inbox.config import InboxConfig
from autostudio.inbox.heuristics import make_fake_llm
from autostudio.inbox.imap import ImapMailBox
from autostudio.inbox.pipeline import InboxDeps, run_once
from autostudio.inbox.providers import InboxProvider, build_llm
from autostudio.llm import FakeLLM, ResilientLLM
from autostudio.llm.openai_client import OpenAIClient
from autostudio.providers import CoreProvider
from autostudio.settings import Settings
from tests.inbox.helpers import NOW, Harness, make_config, make_email
from tests.llm.stubs import openai_stub, status_error


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


async def test__build_llm__real_provider__no_silent_fallback_to_keyword_stub(monkeypatch):
    # Регрессия: ядро оборачивает настоящую модель в ResilientLLM с эвристиками пакета. Для
    # почты это значило бы сортировку по ключевым словам и шаблонные черновики при сбое.
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    settings = Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'openai', 'OPENAI_API_KEY': 'sk-test'})
    llm = build_llm(settings)
    assert isinstance(llm, OpenAIClient) and not isinstance(llm, ResilientLLM)
    await llm.aclose()


def test__build_llm__fake_provider__package_heuristics():
    llm = build_llm(Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'fake'}))
    assert isinstance(llm, FakeLLM) and llm.calls == []


async def test__inbox_provider__container_close__llm_client_closed(monkeypatch, tmp_path):
    closed: list[bool] = []

    class Closing(FakeLLM):
        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr(providers, 'build_llm', lambda settings: Closing())
    monkeypatch.setenv('INBOX_IMAP_USER', 'info@acme.example')
    monkeypatch.setenv('INBOX_IMAP_PASSWORD', 'app-pass')
    settings = Settings.from_env({'AUTOSTUDIO_DATA_DIR': str(tmp_path)})
    container = make_async_container(
        CoreProvider(), InboxProvider(), context={Settings: settings, InboxConfig: make_config()}
    )
    await container.get(InboxDeps)
    await container.close()
    assert closed == [True]


async def test__run__real_provider_spend_limit__email_waits_no_stub_draft(tmp_path):
    # Настоящий клиент OpenAI поверх заглушки SDK: 429 доходит до пайплайна, а не до эвристик.
    sdk = openai_stub(error=status_error(openai.RateLimitError, 429))
    llm = OpenAIClient(sdk, 'gpt-5.4-mini')  # type: ignore[arg-type]  # заглушка SDK, а не AsyncOpenAI
    h = Harness(tmp_path, llm=make_fake_llm())
    h.box.add(make_email(subject='Tour price', body='What is the price of the Kazbegi tour?'))
    result = await run_once(h.config, msgspec.structs.replace(h.deps, llm=llm), NOW)
    assert not result.ok and result.aborted is None and 'model' in result.errors[0]
    assert result.processed == 0 and result.drafts == 0 and h.box.appended == [] and h.owner.sent == []
    assert h.sheet() == []


async def test__build_mailbox__password_oauth_google_and_microsoft__right_login_method(monkeypatch):
    import httpx

    from autostudio.inbox.google_oauth import GoogleRefreshTokenProvider
    from autostudio.inbox.oauth import MicrosoftClientCredentialsProvider
    from autostudio.inbox.providers import build_mailbox, build_token_provider

    env = {
        'INBOX_IMAP_USER': 'info@acme.example',
        'INBOX_IMAP_PASSWORD': 'app-pass',
        'INBOX_OAUTH_CLIENT_ID': 'cid',
        'INBOX_OAUTH_CLIENT_SECRET': 'secret',
        'INBOX_OAUTH_REFRESH_TOKEN': '1//refresh',
        'INBOX_OAUTH_TENANT_ID': 'contoso.onmicrosoft.com',
    }
    async with httpx.AsyncClient() as http:
        password = build_mailbox(make_config().inbox, env, http)
        assert password.user == 'info@acme.example' and build_token_provider(make_config().inbox, env, http) is None
        google = build_token_provider(make_config(auth='google_oauth').inbox, env, http)
        assert isinstance(google, GoogleRefreshTokenProvider)
        microsoft = build_token_provider(make_config(auth='microsoft_oauth').inbox, env, http)
        assert isinstance(microsoft, MicrosoftClientCredentialsProvider)
        box = build_mailbox(make_config(auth='microsoft_oauth').inbox, {**env, 'INBOX_IMAP_PASSWORD': ''}, http)
        assert box.user == 'info@acme.example'  # пароль при OAuth не нужен и не читается


async def test__build_mailbox__oauth_variable_missing__config_error_names_it():
    import httpx
    import pytest

    from autostudio.errors import ConfigError
    from autostudio.inbox.providers import build_mailbox

    async with httpx.AsyncClient() as http:
        with pytest.raises(ConfigError, match='INBOX_OAUTH_REFRESH_TOKEN'):
            build_mailbox(
                make_config(auth='google_oauth').inbox,
                {'INBOX_IMAP_USER': 'a@b.example', 'INBOX_OAUTH_CLIENT_ID': 'c', 'INBOX_OAUTH_CLIENT_SECRET': 's'},
                http,
            )


async def test__build_token_provider__google_auth_not_installed__config_error_with_install_hint(monkeypatch):
    # Регрессия: без extras `sheets` ленивый импорт ронял `run` и `check --connect` трассировкой
    # ModuleNotFoundError, а не строкой «ошибка: …» с подсказкой.
    import sys

    import httpx
    import pytest

    from autostudio.errors import ConfigError
    from autostudio.inbox.providers import build_token_provider

    monkeypatch.setitem(sys.modules, 'autostudio.inbox.google_oauth', None)
    env = {'INBOX_OAUTH_CLIENT_ID': 'c', 'INBOX_OAUTH_CLIENT_SECRET': 's', 'INBOX_OAUTH_REFRESH_TOKEN': 'r'}
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConfigError, match='uv sync --extra sheets'):
            build_token_provider(make_config(auth='google_oauth').inbox, env, http)


async def test__inbox_provider__events_at_shared_events_path(monkeypatch, tmp_path):
    from autostudio.clientconf import events_path

    monkeypatch.setenv('INBOX_IMAP_USER', 'info@acme.example')
    monkeypatch.setenv('INBOX_IMAP_PASSWORD', 'app-pass')
    settings = Settings.from_env({'AUTOSTUDIO_DATA_DIR': str(tmp_path)})
    config = make_config()
    container = make_async_container(CoreProvider(), InboxProvider(), context={Settings: settings, InboxConfig: config})
    await container.get(InboxDeps)
    assert events_path(tmp_path, config.client).exists()
    await container.close()

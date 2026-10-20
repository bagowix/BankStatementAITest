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

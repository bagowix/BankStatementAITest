from autostudio.llm import FakeLLM, make_llm
from autostudio.llm.anthropic_client import AnthropicClient
from autostudio.llm.openai_client import OpenAIClient
from autostudio.settings import Settings


def test__make_llm__fake__returns_given_fake():
    fake = FakeLLM()
    assert make_llm(Settings.from_env({}), fake) is fake


def test__make_llm__fake_without_given__new_fake():
    assert isinstance(make_llm(Settings.from_env({})), FakeLLM)


def test__make_llm__openai__openai_client(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    llm = make_llm(Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'openai', 'OPENAI_API_KEY': 'sk-test'}))
    assert isinstance(llm, OpenAIClient)


def test__make_llm__anthropic__anthropic_client(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'k')
    llm = make_llm(Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'anthropic', 'ANTHROPIC_API_KEY': 'k'}))
    assert isinstance(llm, AnthropicClient)
    assert llm.model == 'claude-opus-5-5'


async def test__llm_clients__aclose__closes_sdk_client():
    closed = []

    class _Sdk:
        async def close(self):
            closed.append(True)

    await OpenAIClient(_Sdk(), 'm').aclose()
    await AnthropicClient(_Sdk(), 'm').aclose()
    await FakeLLM().aclose()
    assert closed == [True, True]

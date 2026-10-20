import datetime as dt

import msgspec
import pytest

from autostudio.clock import FrozenClock
from autostudio.errors import LLMError, NotifyError
from autostudio.llm import FakeLLM, ResilientLLM, make_llm
from autostudio.settings import Settings


class Label(msgspec.Struct):
    value: str


class _Broken:
    model = 'claude-haiku-5-5'

    def __init__(self):
        from autostudio.llm import LLMUsage

        self.usage = LLMUsage()
        self.closed = False

    async def extract(self, *, system, prompt, schema):
        raise LLMError('Anthropic 429: enforced_spend_limit_reached', status_code=429)

    async def write(self, *, system, prompt):
        raise LLMError('Anthropic 529: overloaded', status_code=529)

    async def aclose(self):
        self.closed = True


def _fake():
    return FakeLLM({Label: lambda system, prompt: Label(value='heuristic')}, writer=lambda s, p: 'template')


async def test__resilient__primary_fails__fallback_answers_and_alert_rate_limited():
    alerts: list[LLMError] = []

    async def hook(exc):
        alerts.append(exc)

    clock = FrozenClock(dt.datetime(2026, 10, 20, tzinfo=dt.UTC))
    llm = ResilientLLM(_Broken(), _fake(), hook, clock=clock, alert_every=dt.timedelta(hours=1))
    assert await llm.extract(system='s', prompt='p', schema=Label) == Label(value='heuristic')
    assert await llm.write(system='s', prompt='p') == 'template'
    assert llm.fallbacks == 2
    assert [a.status_code for a in alerts] == [429]
    clock.advance(dt.timedelta(hours=1))
    await llm.write(system='s', prompt='p')
    assert len(alerts) == 2
    assert llm.model == 'claude-haiku-5-5'
    assert llm.usage.calls == 0


async def test__resilient__primary_ok__no_fallback():
    llm = ResilientLLM(_fake(), FakeLLM())
    assert await llm.extract(system='s', prompt='p', schema=Label) == Label(value='heuristic')
    assert llm.fallbacks == 0


async def test__resilient__hook_fails__still_answers():
    async def hook(exc):
        raise NotifyError('telegram down')

    llm = ResilientLLM(_Broken(), _fake(), hook)
    assert await llm.write(system='s', prompt='p') == 'template'


async def test__resilient__fallback_also_fails__llm_error():
    llm = ResilientLLM(_Broken(), FakeLLM())
    with pytest.raises(LLMError, match='нет эвристики'):
        await llm.extract(system='s', prompt='p', schema=Label)


async def test__resilient__aclose__closes_both():
    broken = _Broken()
    await ResilientLLM(broken, FakeLLM()).aclose()
    assert broken.closed


def test__make_llm__real_provider_with_fake__wrapped(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'k')
    llm = make_llm(Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'anthropic', 'ANTHROPIC_API_KEY': 'k'}), _fake())
    assert isinstance(llm, ResilientLLM)

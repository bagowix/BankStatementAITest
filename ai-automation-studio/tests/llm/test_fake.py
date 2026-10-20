import msgspec
import pytest

from autostudio.errors import LLMError
from autostudio.llm import FakeLLM


class Verdict(msgspec.Struct):
    label: str


class Other(msgspec.Struct):
    value: int


async def test__fake_llm__registered_handler__validated_result():
    llm = FakeLLM({Verdict: lambda system, prompt: Verdict(label=prompt.upper())})
    out = await llm.extract(system='s', prompt='lead', schema=Verdict)
    assert out == Verdict(label='LEAD')
    assert llm.calls[0].kind == 'extract'
    assert llm.usage.calls == 1


async def test__fake_llm__unknown_schema__llm_error():
    with pytest.raises(LLMError, match='нет эвристики'):
        await FakeLLM().extract(system='s', prompt='p', schema=Verdict)


async def test__fake_llm__handler_returns_wrong_type__llm_error():
    llm = FakeLLM()
    llm.register(Verdict, lambda system, prompt: Other(value=1))
    with pytest.raises(LLMError, match='ожидался Verdict'):
        await llm.extract(system='s', prompt='p', schema=Verdict)


async def test__fake_llm__write_default__echo_stripped():
    llm = FakeLLM()
    assert await llm.write(system='s', prompt='  hi  ') == 'hi'
    llm.set_writer(lambda system, prompt: 'draft')
    assert await llm.write(system='s', prompt='x') == 'draft'
    assert llm.model == 'fake'

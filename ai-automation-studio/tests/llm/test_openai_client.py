import msgspec
import openai
import pytest

from autostudio.errors import LLMError
from autostudio.llm.openai_client import OpenAIClient
from tests.llm.stubs import connection_error, openai_response, openai_stub, status_error


class Lead(msgspec.Struct):
    name: str
    hot: bool


async def test__openai_extract__strict_schema_sent__parsed():
    stub = openai_stub(openai_response('{"name": "Ann", "hot": true}'))
    client = OpenAIClient(stub, 'gpt-5.4-mini', 'low')
    out = await client.extract(system='sys', prompt='mail', schema=Lead)
    assert out == Lead(name='Ann', hot=True)
    call = stub.responses.calls[0]
    assert call['instructions'] == 'sys'
    assert call['text']['format']['strict'] is True
    assert call['text']['format']['schema']['additionalProperties'] is False
    assert call['reasoning'] == {'effort': 'low'}
    assert client.usage.input_tokens == 10
    assert client.model == 'gpt-5.4-mini'


async def test__openai_write__no_effort__reasoning_omitted():
    stub = openai_stub(openai_response('  Hello!  '))
    client = OpenAIClient(stub, 'm', None)
    assert await client.write(system='s', prompt='p') == 'Hello!'
    assert stub.responses.calls[0]['reasoning'] is openai.omit
    assert stub.responses.calls[0]['text'] is openai.omit


async def test__openai__incomplete_status__llm_error():
    client = OpenAIClient(openai_stub(openai_response('{}', status='incomplete')), 'm')
    with pytest.raises(LLMError, match='incomplete'):
        await client.write(system='s', prompt='p')


async def test__openai__api_status_error__llm_error():
    client = OpenAIClient(openai_stub(error=status_error(openai.APIStatusError, 429)), 'm')
    with pytest.raises(LLMError, match='429'):
        await client.write(system='s', prompt='p')


async def test__openai__connection_error__llm_error():
    client = OpenAIClient(openai_stub(error=connection_error(openai.APIConnectionError)), 'm')
    with pytest.raises(LLMError, match='недоступен'):
        await client.write(system='s', prompt='p')


async def test__openai__schema_mismatch__llm_error():
    client = OpenAIClient(openai_stub(openai_response('{"name": "Ann"}')), 'm')
    with pytest.raises(LLMError, match='Lead'):
        await client.extract(system='s', prompt='p', schema=Lead)

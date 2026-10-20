import anthropic
import msgspec
import pytest

from autostudio.errors import LLMError
from autostudio.llm.anthropic_client import AnthropicClient
from tests.llm.stubs import anthropic_message, anthropic_stub, connection_error, status_error


class Lead(msgspec.Struct):
    name: str
    hot: bool


async def test__anthropic_extract__fallback_model__beta_endpoint_with_fallbacks():
    stub = anthropic_stub(anthropic_message('{"name": "Ann", "hot": false}'))
    client = AnthropicClient(stub, 'claude-opus-5-5', 'low')
    out = await client.extract(system='sys', prompt='mail', schema=Lead)
    assert out == Lead(name='Ann', hot=False)
    call = stub.beta.messages.calls[0]
    assert call['fallbacks'] == 'default'
    assert call['betas'] == ['server-side-fallback-2026-07-01']
    assert call['output_config']['format']['type'] == 'json_schema'
    assert call['output_config']['effort'] == 'low'
    assert 'thinking' not in call
    assert stub.messages.calls == []
    assert client.usage.output_tokens == 3


async def test__anthropic_write__haiku__plain_endpoint_no_format():
    stub = anthropic_stub(anthropic_message(' Draft reply '))
    client = AnthropicClient(stub, 'claude-haiku-5-5', None)
    assert await client.write(system='s', prompt='p') == 'Draft reply'
    call = stub.messages.calls[0]
    assert call['output_config'] == {}
    assert stub.beta.messages.calls == []
    assert client.model == 'claude-haiku-5-5'


@pytest.mark.parametrize(
    ('stop_reason', 'text', 'match'),
    [('refusal', None, 'отказалась'), ('max_tokens', '{"na', 'обрезан'), ('end_turn', None, 'нет текста')],
)
async def test__anthropic__bad_stop__llm_error(stop_reason, text, match):
    client = AnthropicClient(anthropic_stub(anthropic_message(text, stop_reason)), 'claude-haiku-5-5')
    with pytest.raises(LLMError, match=match):
        await client.write(system='s', prompt='p')


async def test__anthropic__status_error__llm_error():
    client = AnthropicClient(anthropic_stub(error=status_error(anthropic.APIStatusError, 529)), 'claude-haiku-5-5')
    with pytest.raises(LLMError, match='529'):
        await client.write(system='s', prompt='p')


async def test__anthropic__connection_error__llm_error():
    client = AnthropicClient(anthropic_stub(error=connection_error(anthropic.APIConnectionError)), 'claude-opus-5-5')
    with pytest.raises(LLMError, match='недоступен'):
        await client.write(system='s', prompt='p')

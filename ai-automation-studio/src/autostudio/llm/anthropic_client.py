"""Anthropic Messages API со структурированным выводом (`output_config.format`)."""

import logging
from typing import Final

import anthropic
import msgspec
from anthropic.types import JSONOutputFormatParam, MessageParam, OutputConfigParam

from autostudio.errors import LLMError
from autostudio.llm.base import LLMUsage, decode_output, output_schema
from autostudio.settings import Effort

logger = logging.getLogger(__name__)

# Модели, у которых есть серверный откат при отказе: на них запрос идёт через
# beta-эндпоинт с `fallbacks="default"`, и редкий ложный отказ классификатора
# безопасности не роняет обработку письма или реплики.
_SERVER_FALLBACK_MODELS: Final[frozenset[str]] = frozenset(
    {'claude-fable-5-1', 'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5'},
)
_FALLBACK_BETA: Final = 'server-side-fallback-2026-07-01'
_MAX_TOKENS: Final = 8000


class _Reply(msgspec.Struct, frozen=True):
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    texts: list[str]


class AnthropicClient:
    """Реализация `LLMClient` поверх `anthropic.AsyncAnthropic`.

    Args:
        client: Готовый асинхронный клиент (ключ он берёт из ANTHROPIC_API_KEY).
        model: Идентификатор модели.
        effort: Глубина рассуждения (`low` для классификации и чата) или None.
    """

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, effort: Effort | None = 'low') -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._usage = LLMUsage()

    @property
    def model(self) -> str:
        return self._model

    @property
    def usage(self) -> LLMUsage:
        return self._usage

    async def aclose(self) -> None:
        await self._client.close()

    async def extract[T: msgspec.Struct](self, *, system: str, prompt: str, schema: type[T]) -> T:
        json_format: JSONOutputFormatParam = {'type': 'json_schema', 'schema': dict(output_schema(schema))}
        text = await self._respond(system=system, prompt=prompt, json_format=json_format)
        return decode_output(text, schema)

    async def write(self, *, system: str, prompt: str) -> str:
        return (await self._respond(system=system, prompt=prompt, json_format=None)).strip()

    async def _respond(self, *, system: str, prompt: str, json_format: JSONOutputFormatParam | None) -> str:
        output_config: OutputConfigParam = {}
        if json_format is not None:
            output_config['format'] = json_format
        if self._effort:
            output_config['effort'] = self._effort
        try:
            if self._model in _SERVER_FALLBACK_MODELS:
                reply = await self._beta_call(system, prompt, output_config)
            else:
                reply = await self._plain_call(system, prompt, output_config)
        except anthropic.APIStatusError as exc:
            raise LLMError(f'Anthropic {exc.status_code}: {exc.message}', status_code=exc.status_code) from None
        except anthropic.APIConnectionError as exc:
            raise LLMError(f'Anthropic недоступен: {type(exc).__name__}') from None
        self._usage.add(reply.input_tokens, reply.output_tokens)
        if reply.stop_reason == 'refusal':
            raise LLMError('модель отказалась отвечать на этот ввод')
        if reply.stop_reason == 'max_tokens':
            raise LLMError('ответ модели обрезан по max_tokens')
        if not reply.texts:
            raise LLMError('в ответе модели нет текста')
        return ''.join(reply.texts)

    async def _plain_call(self, system: str, prompt: str, output_config: OutputConfigParam) -> _Reply:
        messages: list[MessageParam] = [{'role': 'user', 'content': prompt}]
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=messages,
            output_config=output_config,
        )
        return _Reply(
            stop_reason=message.stop_reason,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            texts=[block.text for block in message.content if block.type == 'text'],
        )

    async def _beta_call(self, system: str, prompt: str, output_config: OutputConfigParam) -> _Reply:
        message = await self._client.beta.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{'role': 'user', 'content': prompt}],
            output_config=output_config,  # type: ignore[arg-type]
            betas=[_FALLBACK_BETA],
            fallbacks='default',
        )
        return _Reply(
            stop_reason=message.stop_reason,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            texts=[block.text for block in message.content if block.type == 'text'],
        )

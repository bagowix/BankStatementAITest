"""OpenAI через Responses API со строгой JSON-схемой."""

import logging

import msgspec
import openai
from openai.types.responses import ResponseFormatTextJSONSchemaConfigParam, ResponseTextConfigParam
from openai.types.shared_params import Reasoning

from autostudio.errors import LLMError
from autostudio.llm.base import LLMUsage, decode_output, output_schema
from autostudio.settings import Effort

logger = logging.getLogger(__name__)


class OpenAIClient:
    """Реализация `LLMClient` поверх `openai.AsyncOpenAI`.

    Args:
        client: Готовый асинхронный клиент (ключ он берёт из OPENAI_API_KEY).
        model: Идентификатор модели.
        effort: Глубина рассуждения или None, чтобы не передавать параметр.
    """

    def __init__(self, client: openai.AsyncOpenAI, model: str, effort: Effort | None = None) -> None:
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
        json_schema: dict[str, object] = dict(output_schema(schema))
        text_format: ResponseFormatTextJSONSchemaConfigParam = {
            'type': 'json_schema',
            'name': schema.__name__,
            'schema': json_schema,
            'strict': True,
        }
        text = await self._respond(system=system, prompt=prompt, text_config={'format': text_format})
        return decode_output(text, schema)

    async def write(self, *, system: str, prompt: str) -> str:
        return (await self._respond(system=system, prompt=prompt, text_config=None)).strip()

    async def _respond(self, *, system: str, prompt: str, text_config: ResponseTextConfigParam | None) -> str:
        reasoning: Reasoning | openai.Omit = {'effort': self._effort} if self._effort else openai.omit
        try:
            response = await self._client.responses.create(
                model=self._model,
                instructions=system,
                input=prompt,
                text=text_config if text_config is not None else openai.omit,
                reasoning=reasoning,
            )
        except openai.APIStatusError as exc:
            raise LLMError(f'OpenAI {exc.status_code}: {exc.message}') from None
        except openai.APIConnectionError as exc:
            raise LLMError(f'OpenAI недоступен: {type(exc).__name__}') from None
        if response.usage is not None:
            self._usage.add(response.usage.input_tokens, response.usage.output_tokens)
        if response.status != 'completed':
            raise LLMError(f'OpenAI вернул статус {response.status}')
        logger.debug('openai %s: %s', self._model, response.id)
        return response.output_text

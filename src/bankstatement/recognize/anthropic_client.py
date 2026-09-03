"""Клиент vision-модели Anthropic для ступени L2.

Официальный SDK, Messages API с картинкой в теле запроса. Серверные fallbacks
включены по умолчанию: если модель отказывается от запроса по политике, тот же
запрос выполняется запасной моделью внутри того же вызова. SDK импортируется
лениво: пакет необязателен.
"""

import base64
import logging
import os
from typing import TYPE_CHECKING, Final, Self

from bankstatement.models import BankStatementError

if TYPE_CHECKING:
    from anthropic import Anthropic

log = logging.getLogger(__name__)

DEFAULT_MODEL: Final[str] = 'claude-opus-5'
KEY_ENV: Final[str] = 'ANTHROPIC_API_KEY'
MAX_OUTPUT_TOKENS: Final[int] = 16000
FALLBACK_BETA: Final[str] = 'server-side-fallback-2026-07-01'


class AnthropicVisionClient:
    """Реализация контракта `VisionClient` поверх Anthropic."""

    name = 'anthropic'

    def __init__(self, client: 'Anthropic', model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_env(cls, model: str | None = None) -> Self:
        """Клиент по ключу из окружения; без ключа — внятная ошибка, а не стек SDK."""
        if not os.environ.get(KEY_ENV):
            raise BankStatementError(f'ступень L2 через Anthropic требует переменную {KEY_ENV}')

        from anthropic import Anthropic

        return cls(Anthropic(), model or DEFAULT_MODEL)

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, image_png: bytes, prompt: str) -> str:
        data = base64.standard_b64encode(image_png).decode('utf-8')
        response = self._client.beta.messages.create(
            model=self._model,
            max_tokens=MAX_OUTPUT_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks='default',
            # Переписать страницу — задача простая; глубокое рассуждение здесь
            # только стоило бы денег.
            output_config={'effort': 'low'},
            messages=[
                {
                    'role': 'user',
                    'content': [
                        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': data}},
                        {'type': 'text', 'text': prompt},
                    ],
                }
            ],
        )

        log.info(
            'vision-anthropic: %s, токенов на входе %d, на выходе %d',
            self._model,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        if response.stop_reason == 'refusal':
            raise BankStatementError('модель отказалась читать страницу')

        return ''.join(block.text for block in response.content if block.type == 'text')

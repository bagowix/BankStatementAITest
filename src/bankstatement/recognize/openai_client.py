"""Клиент vision-модели OpenAI для ступени L2.

Официальный SDK, Chat Completions с картинкой в теле запроса. SDK импортируется
лениво: пакет необязателен, и без него модуль не должен ломать импорт лестницы.
"""

import base64
import logging
import os
from typing import TYPE_CHECKING, Final, Self

from bankstatement.models import BankStatementError

if TYPE_CHECKING:
    from openai import OpenAI

log = logging.getLogger(__name__)

DEFAULT_MODEL: Final[str] = 'gpt-5.4-mini'
"""Умолчание выбрано замером на подшивке Ixonia: одни и те же сорок страниц
восьми несведённых выписок, сравнение по числу выписок, сошедшихся до нуля.
gpt-5.4-mini свела семь из десяти, gpt-5.6-sol — девять при цене впятеро выше,
gpt-5.6-terra — пять при цене втрое выше, gpt-4o отказалась читать пять страниц
из двадцати. Лестница платит столько, сколько нужно для сведения, поэтому
умолчание — самая дешёвая модель, которая измеримо работает; сильная включается
переменной `BANKSTATEMENT_VISION_MODEL`."""

KEY_ENV: Final[str] = 'OPENAI_API_KEY'
MAX_OUTPUT_TOKENS: Final[int] = 8000
"""Страница выписки — это одна-две тысячи токенов текста; запас четырёхкратный."""

REASONING_FAMILIES: Final[tuple[str, ...]] = ('gpt-5', 'o1', 'o3', 'o4')
"""Модели с рассуждением перед ответом. Переписать страницу — задача без
рассуждений, и без явного низкого усилия они тратят на них большую часть
бюджета вывода. Модели без рассуждения этот параметр отвергают."""


def wants_low_effort(model: str) -> bool:
    """Модель рассуждает перед ответом, и усилие надо задать явно низким."""
    return model.startswith(REASONING_FAMILIES) and 'chat' not in model


class OpenAIVisionClient:
    """Реализация контракта `VisionClient` поверх OpenAI."""

    name = 'openai'

    def __init__(self, client: 'OpenAI', model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_env(cls, model: str | None = None) -> Self:
        """Клиент по ключу из окружения; без ключа — внятная ошибка, а не стек SDK."""
        if not os.environ.get(KEY_ENV):
            raise BankStatementError(f'ступень L2 через OpenAI требует переменную {KEY_ENV}')

        from openai import OpenAI

        return cls(OpenAI(), model or DEFAULT_MODEL)

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, image_png: bytes, prompt: str) -> str:
        from openai.types.chat.completion_create_params import CompletionCreateParamsNonStreaming

        data = base64.b64encode(image_png).decode('ascii')
        params: CompletionCreateParamsNonStreaming = {
            'model': self._model,
            'max_completion_tokens': MAX_OUTPUT_TOKENS,
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {
                            'type': 'image_url',
                            'image_url': {'url': f'data:image/png;base64,{data}', 'detail': 'high'},
                        },
                    ],
                }
            ],
        }

        if wants_low_effort(self._model):
            params['reasoning_effort'] = 'low'

        response = self._client.chat.completions.create(**params)

        if response.usage is not None:
            # Счёт за ступень — это токены, и без записи о них цена страницы
            # остаётся оценкой. У моделей с рассуждением в токены вывода входит
            # и само рассуждение.
            log.info(
                'vision-openai: %s, токенов на входе %d, на выходе %d',
                self._model,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )

        return response.choices[0].message.content or ''

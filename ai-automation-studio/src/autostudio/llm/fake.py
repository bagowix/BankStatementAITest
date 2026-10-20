"""Детерминированная «модель» для демо, тестов и работы без ключа.

Каждый пакет регистрирует здесь эвристику на свою схему ответа: бот записи
разбирает дату регулярным выражением, разбор почты классифицирует по словам.
Демо-видео снимается без ключа и без сети, а тесты не зависят от провайдера.
Ответ всё равно проходит JSON-кодирование и проверку схемой — тем же путём,
что и ответ настоящей модели.
"""

from collections.abc import Callable, Mapping

import msgspec

from autostudio.errors import LLMError
from autostudio.llm.base import LLMUsage, decode_output

type ExtractHandler = Callable[[str, str], msgspec.Struct]
type WriteHandler = Callable[[str, str], str]


class FakeCall(msgspec.Struct, frozen=True):
    """Запись об одном вызове — тесты проверяют по ней, что модели передали."""

    kind: str
    system: str
    prompt: str


def _echo(system: str, prompt: str) -> str:
    return prompt


class FakeLLM:
    """Реализация `LLMClient` на правилах.

    Args:
        extractors: Обработчик на каждую схему ответа: (system, prompt) -> объект схемы.
        writer: Обработчик свободного текста; по умолчанию возвращает prompt.
    """

    def __init__(
        self,
        extractors: Mapping[type[msgspec.Struct], ExtractHandler] | None = None,
        writer: WriteHandler | None = None,
    ) -> None:
        self._extractors: dict[type[msgspec.Struct], ExtractHandler] = dict(extractors or {})
        self._writer = writer or _echo
        self._usage = LLMUsage()
        self.calls: list[FakeCall] = []

    @property
    def model(self) -> str:
        return 'fake'

    @property
    def usage(self) -> LLMUsage:
        return self._usage

    def register(self, schema: type[msgspec.Struct], handler: ExtractHandler) -> None:
        """Добавляет эвристику для схемы (последняя регистрация побеждает)."""
        self._extractors[schema] = handler

    def set_writer(self, writer: WriteHandler) -> None:
        """Заменяет обработчик свободного текста."""
        self._writer = writer

    async def extract[T: msgspec.Struct](self, *, system: str, prompt: str, schema: type[T]) -> T:
        self.calls.append(FakeCall('extract', system, prompt))
        handler = self._extractors.get(schema)
        if handler is None:
            raise LLMError(f'FakeLLM: нет эвристики для схемы {schema.__name__}')
        result = handler(system, prompt)
        if not isinstance(result, schema):
            raise LLMError(f'FakeLLM: эвристика вернула {type(result).__name__}, ожидался {schema.__name__}')
        self._usage.add(len(prompt) // 4, 0)
        return decode_output(msgspec.json.encode(result).decode(), schema)

    async def write(self, *, system: str, prompt: str) -> str:
        self.calls.append(FakeCall('write', system, prompt))
        self._usage.add(len(prompt) // 4, 0)
        return self._writer(system, prompt).strip()

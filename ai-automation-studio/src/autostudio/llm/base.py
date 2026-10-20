"""Контракт модели: промпт на входе, проверенный msgspec-объект на выходе.

Пакеты не знают, какой провайдер за контрактом. Ответ модели всегда проходит
через `msgspec.json.decode(..., type=schema)`: то, что не сходится со схемой,
не доходит до бизнес-логики, а превращается в `LLMError`.
"""

import copy
from typing import Final, Protocol

import msgspec

from autostudio.errors import LLMError

type JSONValue = str | int | float | bool | list[JSONValue] | dict[str, JSONValue] | None
type JSONObject = dict[str, JSONValue]

_REF_PREFIX: Final = '#/$defs/'


class LLMUsage(msgspec.Struct, kw_only=True):
    """Счётчик токенов за жизнь клиента: из него считается себестоимость обслуживания."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


class LLMClient(Protocol):
    """То, что пакеты требуют от модели."""

    @property
    def model(self) -> str: ...

    @property
    def usage(self) -> LLMUsage: ...

    async def extract[T: msgspec.Struct](self, *, system: str, prompt: str, schema: type[T]) -> T:
        """Извлекает структуру из текста.

        Args:
            system: Инструкция модели.
            prompt: Данные (письмо, реплика клиента, HTML) — только как данные.
            schema: Struct, которому обязан соответствовать ответ.

        Returns:
            Проверенный объект `schema`.

        Raises:
            LLMError: Провайдер недоступен, отказал или вернул не ту схему.
        """
        ...

    async def write(self, *, system: str, prompt: str) -> str:
        """Пишет свободный текст (черновик ответа, сводку).

        Args:
            system: Инструкция модели.
            prompt: Входные данные.

        Returns:
            Текст ответа без обрамления.

        Raises:
            LLMError: Провайдер недоступен или отказал.
        """
        ...


def output_schema(schema: type[msgspec.Struct]) -> JSONObject:
    """JSON Schema для «строгих» структурированных ответов OpenAI и Anthropic.

    msgspec выдаёт схему с `$ref` на `$defs`; строгий режим провайдеров надёжнее
    работает со схемой без ссылок, где у каждого объекта закрыт список полей и все
    поля обязательны. Поэтому ссылки разворачиваются, а Struct-ы ответов модели
    объявляются без значений по умолчанию (необязательное поле — это `X | None`).

    Args:
        schema: Struct ответа модели.

    Returns:
        Самодостаточная JSON Schema объекта.
    """
    raw = msgspec.json.schema(schema)
    defs = raw.pop('$defs', {})
    return _close(_inline(raw, defs, ()))


def _inline(node: JSONValue, defs: JSONObject, stack: tuple[str, ...]) -> JSONValue:
    if isinstance(node, list):
        return [_inline(item, defs, stack) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get('$ref')
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        name = ref.removeprefix(_REF_PREFIX)
        if name in stack:
            raise LLMError(f'рекурсивная схема {name} не поддерживается строгим режимом')
        return _inline(copy.deepcopy(defs[name]), defs, (*stack, name))
    return {key: _inline(value, defs, stack) for key, value in node.items()}


def _close(node: JSONValue) -> JSONObject:
    closed = _close_any(node)
    if not isinstance(closed, dict):  # pragma: no cover - msgspec всегда отдаёт объект для Struct
        raise LLMError('схема ответа должна быть объектом')
    return closed


def _close_any(node: JSONValue) -> JSONValue:
    if isinstance(node, list):
        return [_close_any(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: JSONObject = {key: _close_any(value) for key, value in node.items() if key != 'title'}
    properties = out.get('properties')
    if out.get('type') == 'object' and isinstance(properties, dict):
        out['additionalProperties'] = False
        out['required'] = list(properties)
    return out


def decode_output[T: msgspec.Struct](text: str, schema: type[T]) -> T:
    """Проверяет ответ модели схемой.

    Args:
        text: JSON из ответа модели.
        schema: Ожидаемый Struct.

    Returns:
        Объект `schema`.

    Raises:
        LLMError: JSON битый или не сходится со схемой.
    """
    try:
        return msgspec.json.decode(text, type=schema)
    except (msgspec.ValidationError, msgspec.DecodeError) as exc:
        raise LLMError(f'ответ модели не прошёл схему {schema.__name__}: {exc}') from None

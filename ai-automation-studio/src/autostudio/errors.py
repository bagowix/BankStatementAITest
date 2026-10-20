"""Ошибки, которые пакет поднимает сознательно."""

from typing import Literal

# Что сломалось: от этого зависит, кого винить. `outage` — провайдер недоступен или
# перегружен (429, 5xx, сеть): работа ждёт и повторяется. `config` — ключ, права или
# модель (401/403/404): чинит оператор, а не очередное письмо. `content` — модель не
# справилась с этим вводом (отказ, обрезка, ответ не по схеме): виноват конкретный элемент.
type LLMErrorKind = Literal['outage', 'config', 'content']


class AutoStudioError(Exception):
    """Базовая ошибка: всё, что пакет бросает намеренно, наследует её."""


class ConfigError(AutoStudioError):
    """Конфиг клиента или окружение не позволяют запуститься."""


class LLMError(AutoStudioError):
    """Модель не ответила или ответила не по схеме.

    Args:
        message: Текст для оператора (без ключей и URL с токенами).
        status_code: HTTP-код провайдера, если ошибка пришла от API.
        kind: Что сломалось: `outage`, `config` или `content`.
    """

    def __init__(self, message: str, *, status_code: int | None = None, kind: LLMErrorKind = 'content') -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind: LLMErrorKind = kind


def kind_for_status(status_code: int) -> LLMErrorKind:
    """Вид ошибки по HTTP-коду провайдера.

    Args:
        status_code: Код ответа API.

    Returns:
        `outage` для 408/409/429/5xx, `config` для 401/403/404, иначе `content`.
    """
    if status_code in (408, 409, 429) or status_code >= 500:
        return 'outage'
    if status_code in (401, 403, 404):
        return 'config'
    return 'content'


class SinkError(AutoStudioError):
    """Не удалось записать строки в таблицу клиента."""


class NotifyError(AutoStudioError):
    """Уведомление не доставлено."""

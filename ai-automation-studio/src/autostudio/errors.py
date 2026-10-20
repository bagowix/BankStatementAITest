"""Ошибки, которые пакет поднимает сознательно."""


class AutoStudioError(Exception):
    """Базовая ошибка: всё, что пакет бросает намеренно, наследует её."""


class ConfigError(AutoStudioError):
    """Конфиг клиента или окружение не позволяют запуститься."""


class LLMError(AutoStudioError):
    """Модель не ответила или ответила не по схеме.

    Args:
        message: Текст для оператора (без ключей и URL с токенами).
        status_code: HTTP-код провайдера, если ошибка пришла от API.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SinkError(AutoStudioError):
    """Не удалось записать строки в таблицу клиента."""


class NotifyError(AutoStudioError):
    """Уведомление не доставлено."""

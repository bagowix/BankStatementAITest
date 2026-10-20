"""Ошибки, которые пакет поднимает сознательно."""


class AutoStudioError(Exception):
    """Базовая ошибка: всё, что пакет бросает намеренно, наследует её."""


class ConfigError(AutoStudioError):
    """Конфиг клиента или окружение не позволяют запуститься."""


class LLMError(AutoStudioError):
    """Модель не ответила или ответила не по схеме."""


class SinkError(AutoStudioError):
    """Не удалось записать строки в таблицу клиента."""


class NotifyError(AutoStudioError):
    """Уведомление не доставлено."""

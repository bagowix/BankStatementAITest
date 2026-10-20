"""Ошибки пакета inbox: все наследуют `AutoStudioError`, чтобы `supervised_run` их ловил."""

from autostudio.errors import AutoStudioError


class InboxError(AutoStudioError):
    """Запуск разбора почты не может продолжаться (сбой подряд, таблица недоступна)."""


class MailboxError(InboxError):
    """Почтовый сервер недоступен или отказал; текст без логина и пароля."""


class MailParseError(InboxError):
    """Байты не похожи на письмо вовсе (нет ни одного заголовка)."""

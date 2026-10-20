"""Маскировка секретов в логах — последний рубеж, а не основная защита.

Основная защита — не класть секрет туда, где его увидит лог: уровень httpx поднят до
WARNING, ошибки HTTP переписываются без URL. Но сторонняя библиотека может вложить URL
с токеном в текст исключения (`aiohttp.ClientResponseError` печатает `url=`), а
трассировка уйдёт в лог целиком. Форматтер маскирует уже готовую строку, поэтому
покрывает и сообщение, и трассировку.
"""

import logging
import os
import re
from collections.abc import Iterable, Mapping
from typing import Final, override

MASK: Final = '***'

# Токен Telegram-бота: «<id бота>:<35 символов>», в том числе внутри URL `/bot<token>/`.
_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r'(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}'),
    re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*'),
    re.compile(r'\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}'),
    re.compile(
        r'(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|token|secret)'
        r'(["\']?\s*[=:]\s*["\']?)([^\s&"\',;]+)'
    ),
)
_SECRET_ENV_SUFFIXES: Final = ('_TOKEN', '_KEY', '_SECRET', '_PASSWORD', '_REFRESH_TOKEN')
# Короткие значения («1», «true») совпадали бы с обычным текстом — их не маскируем.
_MIN_SECRET_LEN: Final = 8
_KEY_VALUE_GROUPS: Final = 3


def secret_values(environ: Mapping[str, str]) -> list[str]:
    """Значения переменных окружения, похожих на секреты, — для точной маскировки.

    Args:
        environ: Окружение процесса.

    Returns:
        Значения длиннее восьми символов, от длинных к коротким.
    """
    values = {
        value
        for name, value in environ.items()
        if name.upper().endswith(_SECRET_ENV_SUFFIXES) and len(value) >= _MIN_SECRET_LEN
    }
    return sorted(values, key=len, reverse=True)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Маскирует известные значения секретов и типовые форматы токенов.

    Args:
        text: Строка лога.
        secrets: Точные значения, которые нельзя показывать.

    Returns:
        Строка, где секреты заменены на `***`.
    """
    for value in secrets:
        text = text.replace(value, MASK)
    for pattern in _PATTERNS:
        if pattern.groups == _KEY_VALUE_GROUPS:
            text = pattern.sub(lambda m: f'{m.group(1)}{m.group(2)}{MASK}', text)
        else:
            text = pattern.sub(MASK, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Форматтер, который прогоняет готовую строку (с трассировкой) через `redact`.

    Args:
        fmt: Формат записи, как у `logging.Formatter`.
        secrets: Точные значения секретов; по умолчанию — из `os.environ` на момент создания.
    """

    def __init__(self, fmt: str | None = None, secrets: Iterable[str] | None = None) -> None:
        super().__init__(fmt)
        self._secrets = list(secrets) if secrets is not None else secret_values(os.environ)

    @override
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._secrets)


def install(fmt: str, root: logging.Logger | None = None) -> None:
    """Ставит `RedactingFormatter` на все обработчики корневого логгера.

    Args:
        fmt: Формат записи.
        root: Логгер; по умолчанию корневой.
    """
    for handler in (root or logging.getLogger()).handlers:
        handler.setFormatter(RedactingFormatter(fmt))

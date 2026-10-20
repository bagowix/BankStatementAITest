"""Каналы уведомлений: Telegram, входящий вебхук, консоль и их сумма."""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Final, TextIO

import httpx

from autostudio.errors import NotifyError
from autostudio.notify.base import Notifier

logger = logging.getLogger(__name__)

# Лимит Telegram на текст сообщения — 4096 символов; запас оставлен под многоточие.
TELEGRAM_LIMIT: Final = 4000
_TELEGRAM_API: Final = 'https://api.telegram.org'
# Telegram отвечает 429 с `retry_after` при превышении лимита (≈1 сообщение в секунду в
# чат) и изредка 5xx. Пара повторов с честной паузой спасает сообщение; дольше ждать
# не будем — плановый запуск не должен висеть на уведомлении.
_TELEGRAM_RETRIES: Final = 2
_MAX_RETRY_AFTER_S: Final = 30.0


def utf16_len(text: str) -> int:
    """Длина в единицах UTF-16 — так Telegram считает лимит сообщения (эмодзи — две единицы)."""
    return len(text.encode('utf-16-le')) // 2


def clip(text: str, limit: int = TELEGRAM_LIMIT) -> str:
    """Обрезает текст до лимита канала в единицах UTF-16, честно помечая обрезку.

    Args:
        text: Текст сообщения.
        limit: Лимит канала в единицах UTF-16.

    Returns:
        Исходный текст или его начало с «…», не длиннее лимита.
    """
    if utf16_len(text) <= limit:
        return text
    budget = limit - 1
    used = 0
    for index, char in enumerate(text):
        used += 2 if ord(char) > 0xFFFF else 1
        if used > budget:
            return text[:index] + '…'
    return text  # pragma: no cover - недостижимо: длина выше лимита


class TelegramNotifier:
    """Сообщение в чат через Bot API (без зависимости от aiogram).

    Args:
        http: Общий httpx-клиент.
        token: Токен бота от @BotFather.
        chat_id: Чат получателя (личный id или id группы).
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        token: str,
        chat_id: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._token = token
        self._chat_id = chat_id
        self._sleep = sleep

    async def send(self, text: str) -> None:
        url = f'{_TELEGRAM_API}/bot{self._token}/sendMessage'
        payload = {'chat_id': self._chat_id, 'text': clip(text)}
        for attempt in range(_TELEGRAM_RETRIES + 1):
            try:
                response = await self._http.post(url, json=payload)
            except httpx.HTTPError as exc:
                # В тексте исключения httpx есть URL, а в URL — токен бота.
                raise NotifyError(f'Telegram недоступен: {type(exc).__name__}') from None
            if response.status_code == httpx.codes.OK:
                return
            retryable = response.status_code == httpx.codes.TOO_MANY_REQUESTS or response.is_server_error
            if not retryable or attempt == _TELEGRAM_RETRIES:
                break
            await self._sleep(_retry_after(response, attempt))
        raise NotifyError(f'Telegram ответил {response.status_code}: {response.text[:200]}')


def _retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        hinted = float(response.json()['parameters']['retry_after'])
    except (ValueError, KeyError, TypeError):
        hinted = float(2**attempt)
    return min(max(hinted, 0.0), _MAX_RETRY_AFTER_S)


class WebhookNotifier:
    """POST JSON на входящий вебхук: Slack, Discord, Make, n8n.

    Args:
        http: Общий httpx-клиент.
        url: Адрес вебхука.
    """

    def __init__(self, http: httpx.AsyncClient, url: str) -> None:
        self._http = http
        self._url = url

    async def send(self, text: str) -> None:
        # Slack читает `text`, Discord — `content`; лишнее поле оба игнорируют.
        payload = {'text': text, 'content': clip(text, 2000)}
        try:
            response = await self._http.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            raise NotifyError(f'вебхук недоступен: {type(exc).__name__}') from None
        if response.is_error:
            raise NotifyError(f'вебхук ответил {response.status_code}')


class ConsoleNotifier:
    """Печатает уведомление — для демо-режима и отладки."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    async def send(self, text: str) -> None:
        print(f'🔔 {text}', file=self._stream or sys.stdout, flush=True)


class FanoutNotifier:
    """Шлёт во все каналы; сбой одного не глушит остальные.

    Args:
        channels: Каналы доставки.
    """

    def __init__(self, channels: Sequence[Notifier]) -> None:
        self._channels = list(channels)

    async def send(self, text: str) -> None:
        failures: list[str] = []
        for channel in self._channels:
            try:
                await channel.send(text)
            except NotifyError as exc:
                logger.warning('уведомление не доставлено через %s: %s', type(channel).__name__, exc)
                failures.append(str(exc))
        if failures and len(failures) == len(self._channels):
            raise NotifyError('; '.join(failures))

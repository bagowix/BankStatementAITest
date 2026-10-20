"""Каналы уведомлений: Telegram, входящий вебхук, консоль и их сумма."""

import logging
import sys
from collections.abc import Sequence
from typing import Final, TextIO

import httpx

from autostudio.errors import NotifyError
from autostudio.notify.base import Notifier

logger = logging.getLogger(__name__)

# Лимит Telegram на текст сообщения — 4096 символов; запас оставлен под многоточие.
TELEGRAM_LIMIT: Final = 4000
_TELEGRAM_API: Final = 'https://api.telegram.org'


def clip(text: str, limit: int = TELEGRAM_LIMIT) -> str:
    """Обрезает текст до лимита канала, честно помечая обрезку."""
    return text if len(text) <= limit else text[: limit - 1] + '…'


class TelegramNotifier:
    """Сообщение в чат через Bot API (без зависимости от aiogram).

    Args:
        http: Общий httpx-клиент.
        token: Токен бота от @BotFather.
        chat_id: Чат получателя (личный id или id группы).
    """

    def __init__(self, http: httpx.AsyncClient, token: str, chat_id: str) -> None:
        self._http = http
        self._token = token
        self._chat_id = chat_id

    async def send(self, text: str) -> None:
        url = f'{_TELEGRAM_API}/bot{self._token}/sendMessage'
        try:
            response = await self._http.post(url, json={'chat_id': self._chat_id, 'text': clip(text)})
        except httpx.HTTPError as exc:
            # В тексте исключения httpx есть URL, а в URL — токен бота.
            raise NotifyError(f'Telegram недоступен: {type(exc).__name__}') from None
        if response.status_code != httpx.codes.OK:
            raise NotifyError(f'Telegram ответил {response.status_code}: {response.text[:200]}')


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

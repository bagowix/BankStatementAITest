"""Уведомления владельцу бизнеса и оператору."""

import os

import httpx

from autostudio.clientconf import NotifyConfig, require_env
from autostudio.notify.base import Notifier
from autostudio.notify.channels import (
    ConsoleNotifier,
    FanoutNotifier,
    TelegramNotifier,
    WebhookNotifier,
    clip,
)

__all__ = [
    'ConsoleNotifier',
    'FanoutNotifier',
    'Notifier',
    'TelegramNotifier',
    'WebhookNotifier',
    'clip',
    'make_notifier',
]


def make_notifier(config: NotifyConfig, http: httpx.AsyncClient) -> Notifier:
    """Собирает каналы уведомлений из конфига клиента.

    Args:
        config: Секция `[notify]`.
        http: Общий httpx-клиент.

    Returns:
        Один канал или их сумма; без настроек — консоль.
    """
    channels: list[Notifier] = []
    if config.telegram_chat_id:
        token = require_env(config.telegram_token_env, os.environ)
        channels.append(TelegramNotifier(http, token, config.telegram_chat_id))
    if config.webhook_url_env:
        channels.append(WebhookNotifier(http, require_env(config.webhook_url_env, os.environ)))
    if config.console or not channels:
        channels.append(ConsoleNotifier())
    return channels[0] if len(channels) == 1 else FanoutNotifier(channels)

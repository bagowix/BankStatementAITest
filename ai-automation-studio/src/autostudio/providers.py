"""Общие зависимости пакетов (dishka, Scope.APP).

Ничего не строится при импорте: контейнер собирает команда CLI или тест, отдавая
в контекст `Settings`. Пакет добавляет свой провайдер поверх этого.
"""

import os
from collections.abc import AsyncIterator

import httpx
from dishka import Provider, Scope, from_context, provide

from autostudio.clock import Clock, system_now
from autostudio.notify import Notifier, TelegramNotifier
from autostudio.runlog import Heartbeat
from autostudio.settings import Settings


class OperatorAlerts:
    """Канал оператора (фрилансера), который ведёт клиента; может отсутствовать.

    Args:
        notifier: Канал или None, если оператор не настроил Telegram.
    """

    def __init__(self, notifier: Notifier | None) -> None:
        self.notifier = notifier


class CoreProvider(Provider):
    """HTTP-клиент, часы, пинги «жив» и канал оператора."""

    scope = Scope.APP

    settings = from_context(provides=Settings, scope=Scope.APP)

    @provide
    async def http(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True) as client:
            yield client

    @provide
    def clock(self) -> Clock:
        return system_now

    @provide
    def heartbeat(self, http: httpx.AsyncClient, settings: Settings) -> Heartbeat:
        return Heartbeat(http, settings.healthcheck_url)

    @provide
    def operator(self, http: httpx.AsyncClient, settings: Settings) -> OperatorAlerts:
        token = os.environ.get(settings.operator_token_env, '')
        if settings.operator_chat_id and token:
            return OperatorAlerts(TelegramNotifier(http, token, settings.operator_chat_id))
        return OperatorAlerts(None)

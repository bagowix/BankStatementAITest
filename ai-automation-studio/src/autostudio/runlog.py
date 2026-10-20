"""Плановый запуск под присмотром: журнал, пинг «жив» и сигнал оператору о сбое.

Абонентская поддержка стоит денег, только если сбой замечает оператор раньше
клиента. Поэтому каждый плановый запуск (проверка цен, разбор почты) обёрнут
здесь: успех и сбой пишутся в журнал (из него — надёжность в отчёте), пинг уходит
на healthchecks-сервис (он сам поднимет тревогу, если пинги прекратились), а
текст ошибки — оператору в Telegram.
"""

import datetime as dt
import logging
from collections.abc import Awaitable, Callable

import httpx

from autostudio.errors import AutoStudioError
from autostudio.events import EventLog
from autostudio.notify import Notifier

logger = logging.getLogger(__name__)


class Heartbeat:
    """Пинги на healthchecks.io-совместимый URL (`<url>`, `<url>/start`, `<url>/fail`).

    Args:
        http: Общий httpx-клиент.
        url: URL проверки или None — тогда пинги не отправляются.
    """

    def __init__(self, http: httpx.AsyncClient, url: str | None) -> None:
        self._http = http
        self._url = url.rstrip('/') if url else None

    async def ping(self, suffix: str = '', body: str = '') -> None:
        """Отправляет пинг; сбой пинга не роняет саму работу.

        Args:
            suffix: '' (успех), '/start' или '/fail'.
            body: Текст для журнала healthchecks (например, ошибка).
        """
        if self._url is None:
            return
        try:
            await self._http.post(f'{self._url}{suffix}', content=body[:10_000].encode())
        except httpx.HTTPError as exc:
            logger.warning('пинг healthcheck не ушёл: %s', type(exc).__name__)


async def supervised_run[T](
    *,
    package: str,
    job: Callable[[], Awaitable[T]],
    events: EventLog,
    heartbeat: Heartbeat,
    operator: Notifier | None,
    client_name: str,
    now: Callable[[], dt.datetime],
) -> T | None:
    """Запускает плановую работу с журналом, пингом и сигналом о сбое.

    Ловятся только ошибки пакета (`AutoStudioError`): баг в коде должен упасть
    громко, с трассировкой, а не превратиться в строчку журнала.

    Args:
        package: Код пакета для журнала.
        job: Сама работа.
        events: Журнал клиента.
        heartbeat: Пинги «жив».
        operator: Канал оператора (фрилансера) или None.
        client_name: Имя клиента — для текста тревоги.
        now: Часы (в тестах — замороженные).

    Returns:
        Результат работы или None, если она завершилась ошибкой пакета.
    """
    await heartbeat.ping('/start')
    try:
        result = await job()
    except AutoStudioError as exc:
        events.record(package, 'system.run_failed', now(), {'error': str(exc)[:500]})
        await heartbeat.ping('/fail', str(exc))
        if operator is not None:
            try:
                await operator.send(f'⚠️ {client_name} / {package}: {exc}')
            except AutoStudioError as notify_exc:
                logger.error('оператор не уведомлён о сбое %s: %s', package, notify_exc)
        logger.error('%s: запуск завершился ошибкой: %s', package, exc)
        return None
    events.record(package, 'system.run_ok', now())
    await heartbeat.ping()
    return result

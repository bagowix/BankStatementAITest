"""Напоминания покупателям и вкладка `Schedule` в таблице владельца.

Напоминание — главный довод «бот снижает неявки», поэтому оно не должно ни
теряться, ни дублироваться: отметка ставится в базе до отправки (см.
`BookingStore.claim_reminder`), временный сбой доставки её снимает, а покупатель,
заблокировавший бота, больше не получает попыток.

Цикл напоминаний работает в том же процессе, что и бот, раз в минуту. Раз в час он
же переписывает вкладку `Schedule` (расписание на горизонт записи) — через
`supervised_run`, чтобы пинг healthcheck и сигнал оператору о сбое таблицы шли
по общему для всех пакетов пути.
"""

import asyncio
import datetime as dt
import logging
from typing import Final

from autostudio.booking.callbacks import BOOKING, join
from autostudio.booking.config import BookingConfig
from autostudio.booking.domain import Booking, day_bounds, local_today
from autostudio.booking.messages import Button, DeliveryError, Outgoing, RecipientGoneError, Sender
from autostudio.booking.service import PACKAGE, SCHEDULE_TAB
from autostudio.booking.store import BookingStore
from autostudio.booking.texts import SCHEDULE_HEADER, Texts
from autostudio.clock import Clock
from autostudio.errors import AutoStudioError
from autostudio.events import EventLog
from autostudio.notify import Notifier
from autostudio.runlog import Heartbeat, supervised_run
from autostudio.sinks import TableSink

logger = logging.getLogger(__name__)

TICK: Final = dt.timedelta(minutes=1)
SCHEDULE_EVERY: Final = dt.timedelta(hours=1)


def reminder_message(booking: Booking, config: BookingConfig, texts: Texts) -> Outgoing:
    """Текст напоминания на языке покупателя с кнопкой «Изменить или отменить»."""
    lang = booking.language
    text = texts.get(
        lang,
        'reminder',
        service=texts.service_name(booking.service_id, lang),
        when=texts.when(booking.start, lang),
        staff=texts.staff_name(booking.staff_id),
        id=booking.id,
        business=config.client.name,
    )
    return Outgoing(text=text, buttons=[[Button(texts.get(lang, 'btn_manage'), join(BOOKING, booking.id))]])


async def send_due_reminders(
    store: BookingStore,
    sender: Sender,
    now: dt.datetime,
    *,
    config: BookingConfig,
    texts: Texts,
    events: EventLog,
) -> int:
    """Отправляет напоминания, которым пора, — каждое один раз.

    Args:
        store: База записей.
        sender: Канал.
        now: Текущий момент.
        config: Конфиг клиента.
        texts: Тексты.
        events: Журнал событий.

    Returns:
        Сколько напоминаний доставлено.
    """
    delivered = 0
    for due in store.due_reminders(now, config.booking.reminders_min_before):
        if not store.claim_reminder(due, now):
            continue
        try:
            await sender.send(due.booking.customer, reminder_message(due.booking, config, texts))
        except RecipientGoneError as exc:
            # Отметка остаётся: покупатель заблокировал бота, повторять бессмысленно.
            logger.info('напоминание по записи %s не доставлено навсегда: %s', due.booking.id, exc)
            continue
        except DeliveryError as exc:
            store.release_reminder(due)
            logger.warning('напоминание по записи %s отложено: %s', due.booking.id, exc)
            continue
        events.record(
            PACKAGE,
            'booking.reminded',
            now,
            {'minutes_before': str(due.minutes_before), 'service': due.booking.service_id},
        )
        delivered += 1
    return delivered


def schedule_rows(bookings: list[Booking], texts: Texts, language: str) -> list[list[str]]:
    """Строки вкладки `Schedule`."""
    return [
        [
            f'{texts.local(booking.start):%Y-%m-%d}',
            texts.time(booking.start),
            texts.time(booking.end),
            texts.staff_name(booking.staff_id),
            texts.service_name(booking.service_id, language),
            booking.name,
            booking.phone,
            booking.id,
        ]
        for booking in bookings
    ]


async def refresh_schedule(
    store: BookingStore, sink: TableSink, now: dt.datetime, *, config: BookingConfig, texts: Texts
) -> int:
    """Переписывает вкладку `Schedule`: все активные записи с сегодняшнего дня до конца горизонта.

    Args:
        store: База записей.
        sink: Таблица владельца.
        now: Текущий момент.
        config: Конфиг клиента.
        texts: Тексты.

    Returns:
        Сколько строк записано.

    Raises:
        SinkError: Таблица недоступна.
    """
    zone = config.client.zone
    today = local_today(config, now)
    start = day_bounds(today, zone)[0]
    end = day_bounds(today + dt.timedelta(days=config.booking.horizon_days - 1), zone)[1]
    language = config.owner_language
    rows = schedule_rows(store.upcoming(start, end), texts, language)
    await sink.replace(SCHEDULE_TAB, SCHEDULE_HEADER[language], rows)
    return len(rows)


class ReminderLoop:
    """Фоновый цикл бота: напоминания раз в минуту, вкладка `Schedule` раз в час.

    Args:
        config: Конфиг клиента.
        store: База записей.
        sender: Канал.
        sink: Таблица владельца.
        events: Журнал событий.
        texts: Тексты.
        heartbeat: Пинги «жив» (раз в час, вместе с обновлением расписания).
        operator: Канал оператора для сигнала о сбое или None.
        clock: Часы.
        tick: Пауза между проходами.
    """

    def __init__(
        self,
        *,
        config: BookingConfig,
        store: BookingStore,
        sender: Sender,
        sink: TableSink,
        events: EventLog,
        texts: Texts,
        heartbeat: Heartbeat,
        operator: Notifier | None,
        clock: Clock,
        tick: dt.timedelta = TICK,
    ) -> None:
        self._config = config
        self._store = store
        self._sender = sender
        self._sink = sink
        self._events = events
        self._texts = texts
        self._heartbeat = heartbeat
        self._operator = operator
        self._clock = clock
        self._tick = tick
        self._last_schedule: dt.datetime | None = None

    async def tick(self) -> int:
        """Один проход: напоминания и, если пора, обновление расписания.

        Returns:
            Сколько напоминаний доставлено.
        """
        now = self._clock()
        delivered = 0
        try:
            delivered = await send_due_reminders(
                self._store, self._sender, now, config=self._config, texts=self._texts, events=self._events
            )
        except AutoStudioError as exc:
            logger.error('проход напоминаний не удался: %s', exc)
        if self._last_schedule is None or now - self._last_schedule >= SCHEDULE_EVERY:
            self._last_schedule = now

            async def job() -> int:
                return await refresh_schedule(self._store, self._sink, now, config=self._config, texts=self._texts)

            await supervised_run(
                package=PACKAGE,
                job=job,
                events=self._events,
                heartbeat=self._heartbeat,
                operator=self._operator,
                client_name=self._config.client.name,
                now=self._clock,
            )
        return delivered

    async def run(self, stop: asyncio.Event) -> None:
        """Крутит проходы, пока не выставлен `stop`."""
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                # Баг в цикле напоминаний не должен ронять приём записей: трассировка в лог,
                # а молчание пингов healthcheck через час заметит оператор.
                logger.exception('цикл напоминаний упал на проходе')
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._tick.total_seconds())
            except TimeoutError:
                continue

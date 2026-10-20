"""Напоминания покупателям и вкладка `Schedule` в таблице владельца.

Напоминание — главный довод «бот снижает неявки», поэтому оно не должно ни
теряться, ни дублироваться: отметка ставится в базе до отправки (см.
`BookingStore.claim_reminder`), временный сбой доставки её снимает, а покупатель,
заблокировавший бота, больше не получает попыток.

Цикл напоминаний работает в том же процессе, что и бот, раз в минуту. Раз в час он
же переписывает вкладку `Schedule` (расписание на горизонт записи) — через
`supervised_run`, чтобы пинг healthcheck и сигнал оператору о сбое таблицы шли
по общему для всех пакетов пути. Обновление ограничено по времени (`SCHEDULE_TIMEOUT`):
зависший Google не должен останавливать напоминания. Тот же цикл повторяет
несинхронизированные события Google Calendar.

Рассылка идёт с темпом (`limits.SendThrottle`): не больше 25 сообщений в секунду и одно
в секунду в чат — утренняя пачка напоминаний не должна упереться в лимит Telegram и
заглушить ответы живым покупателям. Напоминание несёт параметры (`Reminder`): WhatsApp вне
24-часового окна отправит их одобренным шаблоном.
"""

import asyncio
import datetime as dt
import logging
from typing import Final

from autostudio.booking.callbacks import BOOKING, join
from autostudio.booking.calsync import CalendarSync
from autostudio.booking.config import BookingConfig
from autostudio.booking.domain import Booking, day_bounds, format_phone, local_today
from autostudio.booking.limits import SendThrottle
from autostudio.booking.messages import Button, DeliveryError, Outgoing, RecipientGoneError, Reminder, Sender
from autostudio.booking.service import PACKAGE, SCHEDULE_TAB
from autostudio.booking.store import BookingStore
from autostudio.booking.texts import SCHEDULE_HEADER, Texts
from autostudio.clock import Clock
from autostudio.errors import AutoStudioError, SinkError
from autostudio.events import EventLog
from autostudio.notify import Notifier
from autostudio.runlog import Heartbeat, supervised_run
from autostudio.sinks import Cell, TableSink

logger = logging.getLogger(__name__)

TICK: Final = dt.timedelta(minutes=1)
SCHEDULE_EVERY: Final = dt.timedelta(hours=1)
SCHEDULE_TIMEOUT_S: Final = 60.0


def reminder_message(booking: Booking, config: BookingConfig, texts: Texts, now: dt.datetime) -> Outgoing:
    """Текст напоминания на языке покупателя.

    Кнопка «Изменить или отменить» — только пока политика отмены это позволяет: за
    2 часа до визита при политике «не позже чем за 2 часа» она привела бы к «уже поздно».
    Тогда вместо кнопки — телефон для связи.
    """
    lang = booking.language
    service = texts.service_name(booking.service_id, lang)
    when = texts.when(booking.start, lang)
    reminder = Reminder(service=service, when=when, language=lang)
    text = texts.get(
        lang,
        'reminder',
        service=service,
        when=when,
        staff=texts.staff_name(booking.staff_id),
        id=booking.id,
        business=config.client.name,
    )
    if booking.start - now > dt.timedelta(minutes=config.booking.cancellation_min_before):
        manage = [[Button(texts.get(lang, 'btn_manage'), join(BOOKING, booking.id))]]
        return Outgoing(text=text, buttons=manage, reminder=reminder)
    phone = config.booking.contact_phone
    if phone:
        text = f'{text}\n{texts.get(lang, "call_us", phone=phone)}'
    return Outgoing(text=text, reminder=reminder)


async def send_due_reminders(
    store: BookingStore,
    sender: Sender,
    now: dt.datetime,
    *,
    config: BookingConfig,
    texts: Texts,
    events: EventLog,
    throttle: SendThrottle | None = None,
) -> int:
    """Отправляет напоминания, которым пора, — каждое один раз.

    Args:
        store: База записей.
        sender: Канал.
        now: Текущий момент.
        config: Конфиг клиента.
        texts: Тексты.
        events: Журнал событий.
        throttle: Темп рассылки или None (без пауз — тесты).

    Returns:
        Сколько напоминаний доставлено.
    """
    delivered = 0
    for due in store.due_reminders(now, config.booking.reminders_min_before):
        # Текст строится по записи, перечитанной при отметке: её могли перенести за эти секунды.
        booking = store.claim_reminder(due, now)
        if booking is None:
            continue
        if throttle is not None:
            await throttle.wait(booking.customer)
        try:
            await sender.send(booking.customer, reminder_message(booking, config, texts, now))
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


def schedule_rows(config: BookingConfig, bookings: list[Booking], texts: Texts, language: str) -> list[list[Cell]]:
    """Строки вкладки `Schedule`: длительность в минутах и цена — числами."""
    rows: list[list[Cell]] = []
    for booking in bookings:
        service = config.booking.service(booking.service_id)
        rows.append(
            [
                f'{texts.local(booking.start):%Y-%m-%d}',
                texts.time(booking.start),
                texts.time(booking.end),
                int((booking.end - booking.start).total_seconds() // 60),
                texts.staff_name(booking.staff_id),
                texts.service_name(booking.service_id, language),
                '' if service is None else service.price,
                '' if service is None else service.currency,
                booking.name,
                format_phone(booking.phone),
                booking.id,
            ]
        )
    return rows


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
    rows = schedule_rows(config, store.upcoming(start, end), texts, language)
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
        schedule_timeout: Сколько секунд ждать обновления вкладки `Schedule`.
        throttle: Темп рассылки напоминаний (по умолчанию — 25 в секунду, одно в секунду в чат).
        calendar_sync: Повтор несинхронизированных событий Google Calendar или None.
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
        schedule_timeout: float = SCHEDULE_TIMEOUT_S,
        throttle: SendThrottle | None = None,
        calendar_sync: CalendarSync | None = None,
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
        self._schedule_timeout = schedule_timeout
        self._throttle = throttle or SendThrottle()
        self._calendar_sync = calendar_sync
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
                self._store,
                self._sender,
                now,
                config=self._config,
                texts=self._texts,
                events=self._events,
                throttle=self._throttle,
            )
        except AutoStudioError as exc:
            logger.error('проход напоминаний не удался: %s', exc)
        if self._calendar_sync is not None:
            try:
                await self._calendar_sync.run_due()
            except AutoStudioError as exc:
                logger.error('синхронизация с Google Calendar не удалась: %s', exc)
        if self._last_schedule is None or now - self._last_schedule >= SCHEDULE_EVERY:
            self._last_schedule = now

            async def job() -> int:
                refresh = refresh_schedule(self._store, self._sink, now, config=self._config, texts=self._texts)
                try:
                    return await asyncio.wait_for(refresh, timeout=self._schedule_timeout)
                except TimeoutError:
                    # Для supervised_run это сбой таблицы: журнал, пинг /fail и сигнал оператору.
                    raise SinkError(f'вкладка {SCHEDULE_TAB} не обновилась за {self._schedule_timeout:.0f} с') from None

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

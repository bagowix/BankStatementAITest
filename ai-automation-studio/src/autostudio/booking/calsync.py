"""Google Calendar клиента: занятость для расчёта слотов и события записей.

**Занятость.** Перед ответом покупателю бот берёт freeBusy календарей мастеров на
горизонт записи и передаёт занятость в чистый расчёт слотов как данные
(`domain.free_slots(busy=…)`) — личные дела мастера закрывают время так же, как запись.
Ответ кешируется на `(календарь, день)` на `cache_ttl_s`: один запрос к Google на пачку
сообщений, а не на каждое нажатие (квота — 600 запросов в минуту на пользователя).

Если Google недоступен, бот **не останавливает запись**: слоты считаются только по базе
(как без календаря), а оператор получает один сигнал и сообщение о восстановлении. Риск
этого выбора — двойная запись на время, которое мастер занял в календаре во время сбоя;
он меньше, чем потерянные записи за всё время сбоя, и описан в документации пакета.

**События.** Каждая запись, отмена и перенос ставят отметку в базе (в той же транзакции),
а `CalendarSync` приводит календарь к базе: создаёт событие без гостей, меняет время,
удаляет. Событие находится по частному свойству с кодом записи, поэтому повтор после
обрыва связи не создаёт дубликат. Покупатель календаря не ждёт — синхронизация идёт в
фоне, повторы — с нарастающей паузой из цикла напоминаний.
"""

import asyncio
import datetime as dt
import logging
from collections.abc import Sequence
from typing import Final

from autostudio.booking.alarm import OperatorAlarm
from autostudio.booking.config import BookingConfig, CalendarSection
from autostudio.booking.domain import Booking, BusyMap, Span, day_bounds, format_phone, horizon_days, subtract_spans
from autostudio.booking.gcal import CalendarApi, CalendarError, EventDraft, EventGoneError
from autostudio.booking.store import BookingStore, CalendarTask
from autostudio.booking.texts import Texts
from autostudio.clock import Clock

logger = logging.getLogger(__name__)

ALARM_READ: Final = 'calendar_read'
ALARM_WRITE: Final = 'calendar_write'
SYNC_BATCH: Final = 20
_RETRY_MAX: Final = dt.timedelta(hours=1)


class CalendarAvailability:
    """Занятость мастеров из Google Calendar с кешем и откатом на базу при сбое.

    Args:
        config: Конфиг клиента (секция `[booking.calendar]` обязательна).
        api: Календарь.
        alarm: Сигналы оператору.
        clock: Часы (время жизни кеша).
    """

    def __init__(self, *, config: BookingConfig, api: CalendarApi, alarm: OperatorAlarm, clock: Clock) -> None:
        section = config.booking.calendar
        if section is None:
            raise ValueError('CalendarAvailability без [booking.calendar]')
        self._config = config
        self._section: CalendarSection = section
        self._api = api
        self._alarm = alarm
        self._clock = clock
        self._ttl = dt.timedelta(seconds=section.cache_ttl_s)
        self._cache: dict[tuple[str, dt.date], tuple[dt.datetime, list[Span]]] = {}
        self._quiet_until: dt.datetime | None = None
        self._lock = asyncio.Lock()

    def forget(self, calendar_id: str) -> None:
        """Забывает кеш календаря: бот сам изменил там событие (перенос освобождает старый день)."""
        for key in [key for key in self._cache if key[0] == calendar_id]:
            del self._cache[key]

    async def busy(self, bookings: Sequence[Booking], now: dt.datetime) -> BusyMap:
        """Занятость мастеров на горизонт записи — без событий самих записей.

        Args:
            bookings: Активные записи на горизонт (их события вычитаются из занятости
                календаря, иначе запись к одному мастеру закрыла бы общий календарь всем).
            now: Текущий момент.

        Returns:
            id мастера → занятые промежутки (UTC); мастера без календаря или с недоступным
            календарём отсутствуют — для них работает только база.
        """
        days = horizon_days(self._config, now)
        async with self._lock:
            await self._refresh(days, now)
        out: dict[str, list[Span]] = {}
        for member in self._config.booking.staff:
            calendar = self._section.calendar_for(member.id)
            if calendar is None:
                continue
            spans: list[Span] = []
            for day in days:
                cached = self._cache.get((calendar, day))
                # Только свежий ответ: при сбое Google старый снимок не выдаётся за занятость — слоты
                # считаются по базе, и оператор об этом знает (сигнал выше).
                if cached is not None and now - cached[0] < self._ttl:
                    spans += cached[1]
            if not spans:
                continue
            own = [
                (booking.start, booking.end)
                for booking in bookings
                if booking.active and self._section.calendar_for(booking.staff_id) == calendar
            ]
            out[member.id] = subtract_spans(_merge(spans), own)
        return out

    async def _refresh(self, days: Sequence[dt.date], now: dt.datetime) -> None:
        calendars = self._section.calendars
        stale = sorted(
            {
                day
                for day in days
                for calendar in calendars
                if (cached := self._cache.get((calendar, day))) is None or now - cached[0] >= self._ttl
            }
        )
        if not stale:
            return
        if self._quiet_until is not None and now < self._quiet_until:
            # После сбоя не бьём Google на каждом сообщении: покупатель не должен ждать таймаут.
            return
        zone = self._config.client.zone
        start, end = day_bounds(stale[0], zone)[0], day_bounds(stale[-1], zone)[1]
        try:
            answer = await self._api.free_busy(calendars, start, end)
        except CalendarError as exc:
            self._quiet_until = now + self._ttl
            logger.warning('занятость из Google Calendar не получена, слоты — по базе: %s', exc)
            await self._alarm.raise_(
                ALARM_READ,
                f'Google Calendar недоступен ({exc}): свободное время считается только по базе бота — '
                'возможна двойная запись на время, занятое в календаре',
            )
            return
        self._quiet_until = None
        day = stale[0]
        while day <= stale[-1]:
            first, last = day_bounds(day, zone)
            for calendar, spans in answer.busy.items():
                self._cache[(calendar, day)] = (now, [(a, b) for a, b in spans if a < last and b > first])
            day += dt.timedelta(days=1)
        if answer.errors:
            problems = ', '.join(f'{calendar}: {reason}' for calendar, reason in sorted(answer.errors.items()))
            await self._alarm.raise_(
                ALARM_READ,
                f'календари Google не читаются ({problems}) — их занятость не учитывается. '
                'notFound обычно значит, что календарь не расшарен на сервисный аккаунт',
            )
        else:
            await self._alarm.clear(ALARM_READ, 'Google Calendar снова отвечает — занятость учитывается')


def _merge(spans: Sequence[Span]) -> list[Span]:
    merged: list[Span] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def booking_key(config: BookingConfig, booking_id: str) -> str:
    """Значение частного свойства события: клиент и код записи (два клиента в одном календаре не спутаются)."""
    return f'{config.client.id}/{booking_id}'


def event_draft(config: BookingConfig, texts: Texts, booking: Booking) -> EventDraft:
    """Событие записи: `<услуга> — <имя покупателя>`, в описании — код, мастер и телефон.

    Args:
        config: Конфиг клиента.
        texts: Тексты (название услуги на языке владельца).
        booking: Запись.

    Returns:
        Черновик события.
    """
    language = config.owner_language
    first_name = booking.name.split()[0] if booking.name.split() else booking.name
    service = texts.service_name(booking.service_id, language)
    description = '\n'.join(
        [
            texts.get(language, 'calendar_booking', id=booking.id, staff=texts.staff_name(booking.staff_id)),
            format_phone(booking.phone),
        ]
    )
    return EventDraft(
        summary=f'{service} — {first_name}',
        description=description,
        start=booking.start,
        end=booking.end,
        timezone=config.client.timezone,
        booking_key=booking_key(config, booking.id),
    )


class CalendarSync:
    """Приводит события Google Calendar к записям в базе.

    Args:
        config: Конфиг клиента.
        store: База записей (отметки синхронизации).
        api: Календарь.
        texts: Тексты.
        alarm: Сигналы оператору.
        clock: Часы.
        availability: Кеш занятости — сбрасывается для дней, где бот изменил событие.
    """

    def __init__(
        self,
        *,
        config: BookingConfig,
        store: BookingStore,
        api: CalendarApi,
        texts: Texts,
        alarm: OperatorAlarm,
        clock: Clock,
        availability: CalendarAvailability | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._api = api
        self._texts = texts
        self._alarm = alarm
        self._clock = clock
        self._availability = availability
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[int]] = set()

    def kick(self) -> None:
        """Запускает проход в фоне: покупатель не ждёт ответа Google."""
        task = asyncio.get_running_loop().create_task(self.run_due())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Дожидается фоновых проходов (остановка, демо, тесты)."""
        while self._tasks:
            await asyncio.wait(set(self._tasks))

    async def run_due(self) -> int:
        """Один проход: все отметки, которым пора.

        Returns:
            Сколько записей синхронизировано.
        """
        async with self._lock:
            now = self._clock()
            done = 0
            failed: str | None = None
            for task in self._store.calendar_due(now, SYNC_BATCH):
                try:
                    await self._reconcile(task)
                except CalendarError as exc:
                    failed = str(exc)
                    delay = min(dt.timedelta(minutes=2**task.attempts), _RETRY_MAX)
                    self._store.calendar_failed(task.booking.id, task.version, now + delay)
                    logger.warning('событие записи %s не синхронизировано: %s', task.booking.id, exc)
                    continue
                done += 1
            if failed is not None:
                await self._alarm.raise_(
                    ALARM_WRITE,
                    f'события записей не пишутся в Google Calendar ({failed}); записи в боте сохранены, '
                    'календарь догонит после восстановления',
                )
            elif done:
                await self._alarm.clear(ALARM_WRITE, 'события записей снова пишутся в Google Calendar')
            return done

    async def _reconcile(self, task: CalendarTask) -> None:
        booking = task.booking
        calendar = task.calendar_id
        key = booking_key(self._config, booking.id)
        if booking.active:
            draft = event_draft(self._config, self._texts, booking)
            event_id = task.event_id
            if event_id is not None:
                try:
                    await self._api.patch_event(calendar, event_id, draft)
                except EventGoneError:
                    # Событие удалили руками в календаре, а запись жива: создаём заново.
                    event_id = None
            if event_id is None:
                # Поиск перед созданием: прошлая попытка могла создать событие и не успеть
                # сохранить его id (обрыв связи, перезапуск) — второй раз создавать нельзя.
                event_id = await self._api.find_event(calendar, key)
                if event_id is not None:
                    await self._api.patch_event(calendar, event_id, draft)
                else:
                    event_id = await self._api.insert_event(calendar, draft)
            self._store.calendar_done(booking.id, task.version, event_id)
        else:
            event_id = task.event_id or await self._api.find_event(calendar, key)
            if event_id is not None:
                await self._api.delete_event(calendar, event_id)
            self._store.calendar_done(booking.id, task.version, None)
        if self._availability is not None:
            self._availability.forget(calendar)

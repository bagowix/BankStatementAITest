"""Действия с записями и их последствия: база, журнал событий, таблица владельца.

Разговор (`engine`) и служебные команды (`admin`) меняют записи только через этот
модуль, поэтому любая запись, отмена или перенос одинаково попадает в журнал (из
него — месячный отчёт) и во вкладку `Bookings` таблицы владельца.

Таблица — зеркало, а не источник истины: строка во вкладку `Bookings` пишется в фоне
(`mirror.SheetMirror`), поэтому покупатель не ждёт Google. Если Google Sheets сейчас
недоступен, запись всё равно создаётся, строка повторяется со следующей, а вкладка
`Schedule` догонит состояние при ежечасном обновлении (и при сбое — сигнал оператору
через `supervised_run`).
"""

import datetime as dt
from collections.abc import Mapping
from typing import Final

from autostudio.booking.config import WEEKDAYS, BookingConfig
from autostudio.booking.domain import (
    Booking,
    BookingNotFoundError,
    Customer,
    Slot,
    SlotTakenError,
    Source,
    TimeWindow,
    bookable_days,
    find_slot,
    format_phone,
    free_slots,
    is_open_at,
    local_today,
    nearest_slots,
    next_free_slots,
)
from autostudio.booking.mirror import SheetMirror
from autostudio.booking.store import BookingStore
from autostudio.booking.texts import BOOKINGS_HEADER, Texts
from autostudio.events import EventLog
from autostudio.sinks import TableSink

PACKAGE: Final = 'booking'
WALK_IN_EVENT: Final = 'booking.walk_in'
BOOKINGS_TAB: Final = 'Bookings'
SCHEDULE_TAB: Final = 'Schedule'


class BookingService:
    """Записи клиента: расчёт слотов поверх базы и изменения с журналом и таблицей.

    Args:
        config: Конфиг клиента.
        store: База записей.
        events: Журнал событий клиента.
        sink: Таблица владельца.
        texts: Тексты (язык владельца для таблицы и журнала).
        mirror: Фоновая запись строк в таблицу; по умолчанию — поверх `sink`.
    """

    def __init__(
        self,
        *,
        config: BookingConfig,
        store: BookingStore,
        events: EventLog,
        sink: TableSink,
        texts: Texts,
        mirror: SheetMirror | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._events = events
        self._mirror = mirror or SheetMirror(sink)
        self._texts = texts

    @property
    def config(self) -> BookingConfig:
        return self._config

    @property
    def store(self) -> BookingStore:
        return self._store

    async def drain(self) -> None:
        """Дожидается фоновой записи строк в таблицу (остановка процесса, демо, тесты)."""
        await self._mirror.drain()

    def bookings(self, now: dt.datetime) -> list[Booking]:
        """Активные записи на весь горизонт (с запасом в сутки по краям)."""
        start = now - dt.timedelta(days=1)
        end = now + dt.timedelta(days=self._config.booking.horizon_days + 1)
        return self._store.overlapping(start, end)

    def free_slots(
        self, service_id: str, day: dt.date, now: dt.datetime, *, staff_id: str | None = None, ignore: str | None = None
    ) -> list[Slot]:
        """Свободные слоты дня (см. `domain.free_slots`)."""
        return free_slots(
            self._config, service_id, day, self.bookings(now), now, staff_id=staff_id, ignore_booking=ignore
        )

    def bookable_days(
        self,
        service_id: str,
        now: dt.datetime,
        *,
        staff_id: str | None = None,
        start_day: dt.date | None = None,
        limit: int | None = None,
        ignore: str | None = None,
    ) -> list[dt.date]:
        """Дни со свободными слотами (см. `domain.bookable_days`)."""
        return bookable_days(
            self._config,
            service_id,
            self.bookings(now),
            now,
            staff_id=staff_id,
            start_day=start_day,
            limit=limit,
            ignore_booking=ignore,
        )

    def next_slots(
        self,
        service_id: str,
        now: dt.datetime,
        *,
        limit: int,
        staff_id: str | None = None,
        start_day: dt.date | None = None,
        window: TimeWindow | None = None,
    ) -> list[Slot]:
        """Ближайшие свободные слоты (см. `domain.next_free_slots`)."""
        return next_free_slots(
            self._config,
            service_id,
            self.bookings(now),
            now,
            limit=limit,
            staff_id=staff_id,
            start_day=start_day,
            window=window,
        )

    def nearest(
        self,
        service_id: str,
        target: dt.datetime,
        now: dt.datetime,
        *,
        limit: int,
        staff_id: str | None = None,
        ignore: str | None = None,
    ) -> list[Slot]:
        """Альтернативы занятому времени (см. `domain.nearest_slots`)."""
        return nearest_slots(
            self._config,
            service_id,
            target,
            self.bookings(now),
            now,
            limit=limit,
            staff_id=staff_id,
            ignore_booking=ignore,
        )

    def find(
        self, service_id: str, staff_id: str | None, start: dt.datetime, now: dt.datetime, *, ignore: str | None = None
    ) -> Slot | None:
        """Свободен ли ещё этот слот (см. `domain.find_slot`)."""
        return find_slot(self._config, service_id, staff_id, start, self.bookings(now), now, ignore_booking=ignore)

    async def book(
        self,
        *,
        service_id: str,
        staff_id: str | None,
        start: dt.datetime,
        customer: Customer,
        language: str,
        source: Source,
        now: dt.datetime,
        max_active: int | None = None,
    ) -> Booking:
        """Создаёт запись после повторной проверки слота.

        Args:
            service_id: Услуга.
            staff_id: Мастер или None — любой свободный в это время.
            start: Начало, UTC.
            customer: Чат покупателя с именем и телефоном для записи.
            language: Язык покупателя.
            source: Откуда пришла запись.
            now: Текущий момент.
            max_active: Лимит предстоящих записей чата или None (администратор).

        Returns:
            Созданная запись.

        Raises:
            SlotTakenError: Время уже занято (или вне расписания).
            TooManyBookingsError: Лимит предстоящих записей исчерпан.
            BookingError: Нет имени или телефона.
        """
        booking = self._create(service_id, staff_id, start, customer, language, source, now, max_active)
        # Запись, которую внёс администратор, — его работа, а не сэкономленное ботом время:
        # она идёт в журнал отдельным событием и не раздувает «записи» и «пока вы были закрыты».
        self.record(
            WALK_IN_EVENT if source == 'admin' else 'booking.created',
            now,
            {
                **self._service_payload(booking),
                'staff': booking.staff_id,
                'source': source,
                'weekday': WEEKDAYS[booking.start.astimezone(self._config.client.zone).weekday()],
                'hour': f'{booking.start.astimezone(self._config.client.zone).hour:02d}',
                'after_hours': 'no' if is_open_at(self._config, now) else 'yes',
                'lead_days': str((self._texts.local(booking.start).date() - local_today(self._config, now)).days),
            },
        )
        self._log_row('action_created', booking, now)
        return booking

    def _create(
        self,
        service_id: str,
        staff_id: str | None,
        start: dt.datetime,
        customer: Customer,
        language: str,
        source: Source,
        now: dt.datetime,
        max_active: int | None,
    ) -> Booking:
        buffer = dt.timedelta(minutes=self._config.booking.buffer_min)
        # Две попытки: при «любом свободном» мастер назначается в момент проверки, и если
        # его за эти миллисекунды занял другой процесс, на то же время может быть свободен другой.
        for attempt in range(2):
            slot = self.find(service_id, staff_id, start, now)
            if slot is None:
                break
            try:
                return self._store.create(
                    slot,
                    customer=customer,
                    source=source,
                    language=language,
                    now=now,
                    buffer=buffer,
                    max_active=max_active,
                )
            except SlotTakenError:
                if staff_id is not None or attempt:
                    raise
        raise SlotTakenError(f'{service_id} {start.isoformat()}')

    async def cancel(
        self, booking_id: str, *, now: dt.datetime, by_admin: bool, customer: str | None = None
    ) -> Booking:
        """Отменяет запись (покупатель — только свою и не позже политики отмены).

        Args:
            booking_id: Код записи.
            now: Текущий момент.
            by_admin: Отменяет владелец.
            customer: Ключ покупателя (для проверки владения) или None.

        Returns:
            Отменённая запись.

        Raises:
            BookingNotFoundError: Нет такой записи (или чужая).
            BookingInactiveError: Уже отменена.
            CancellationTooLateError: Слишком поздно для покупателя.
        """
        booking = self._store.cancel(booking_id, now=now, by_admin=by_admin, customer=customer, notice=self.notice)
        self.record(
            'booking.cancelled',
            now,
            {**self._service_payload(booking), 'by': 'admin' if by_admin else 'customer'},
        )
        self._log_row('action_cancelled', booking, now)
        return booking

    async def reschedule(
        self,
        booking_id: str,
        start: dt.datetime,
        *,
        now: dt.datetime,
        by_admin: bool,
        customer: str | None = None,
    ) -> tuple[Booking, Booking]:
        """Переносит запись на новое время к тому же мастеру.

        Args:
            booking_id: Код записи.
            start: Новое начало, UTC.
            now: Текущий момент.
            by_admin: Переносит владелец.
            customer: Ключ покупателя или None.

        Returns:
            Запись до и после переноса.

        Raises:
            BookingNotFoundError: Нет такой записи (или чужая).
            BookingInactiveError: Уже отменена.
            CancellationTooLateError: Слишком поздно для покупателя.
            SlotTakenError: Новое время занято.
        """
        current = self._store.get(booking_id)
        if current is None or (customer is not None and current.customer != customer):
            raise BookingNotFoundError(booking_id)
        slot = self.find(current.service_id, current.staff_id, start, now, ignore=booking_id)
        if slot is None:
            raise SlotTakenError(f'{current.service_id} {start.isoformat()}')
        old, new = self._store.reschedule(
            booking_id,
            slot,
            now=now,
            by_admin=by_admin,
            customer=customer,
            notice=self.notice,
            buffer=dt.timedelta(minutes=self._config.booking.buffer_min),
        )
        self.record(
            'booking.rescheduled',
            now,
            {**self._service_payload(new), 'by': 'admin' if by_admin else 'customer'},
        )
        self._log_row('action_rescheduled', new, now)
        return old, new

    @property
    def notice(self) -> dt.timedelta:
        """Политика отмены и переноса для покупателя."""
        return dt.timedelta(minutes=self._config.booking.cancellation_min_before)

    def record(self, kind: str, now: dt.datetime, payload: Mapping[str, str] | None = None) -> None:
        """Пишет событие пакета в журнал клиента (без персональных данных)."""
        self._events.record(PACKAGE, kind, now, payload)

    def _service_payload(self, booking: Booking) -> dict[str, str]:
        language = self._config.owner_language
        return {'service': booking.service_id, 'service_name': self._texts.service_name(booking.service_id, language)}

    def sheet_row(self, action_key: str, booking: Booking, now: dt.datetime) -> list[str]:
        """Строка вкладки `Bookings` на языке владельца."""
        language = self._config.owner_language
        texts = self._texts
        service = self._config.booking.service(booking.service_id)
        price = '' if service is None else texts.money(service.price, service.currency)
        local = texts.local(booking.start)
        return [
            f'{texts.local(now):%Y-%m-%d %H:%M}',
            texts.get(language, action_key),
            booking.id,
            f'{local:%Y-%m-%d}',
            f'{local:%H:%M}',
            texts.service_name(booking.service_id, language),
            texts.staff_name(booking.staff_id),
            booking.name,
            format_phone(booking.phone),
            price,
            texts.get(language, f'source_{booking.source}'),
        ]

    def _log_row(self, action_key: str, booking: Booking, now: dt.datetime) -> None:
        header = BOOKINGS_HEADER[self._config.owner_language]
        self._mirror.append(BOOKINGS_TAB, header, [self.sheet_row(action_key, booking, now)])

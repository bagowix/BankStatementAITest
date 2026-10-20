"""Расписание и записи: чистые функции без ввода-вывода.

Свободен ли слот, считает только этот модуль — ни модель, ни кнопка из старого
сообщения не могут его обойти. Внутри всё время в UTC; часы работы — в местном
времени клиента, и переход между ними сделан здесь, с учётом перевода часов:

- весной местного времени 02:00–03:00 не существует — такие начала пропускаются;
- осенью 02:00–03:00 бывает дважды — предлагается только первое (летнее) вхождение,
  чтобы покупатель не увидел два одинаковых «02:30»; конец интервала берётся по самому
  раннему толкованию, чтобы услуга точно уложилась до закрытия.
"""

import datetime as dt
from collections import Counter
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Final, Literal
from zoneinfo import ZoneInfo

import msgspec

from autostudio.booking.config import MINUTES_PER_DAY, BookingConfig, BookingSection, Interval, Staff
from autostudio.errors import AutoStudioError

type Source = Literal['buttons', 'ai']


class BookingStatus(StrEnum):
    """Статус записи; `pending` зарезервирован под ручное подтверждение владельцем."""

    PENDING = 'pending'
    CONFIRMED = 'confirmed'
    CANCELLED = 'cancelled'


ACTIVE_STATUSES: Final[frozenset[BookingStatus]] = frozenset({BookingStatus.PENDING, BookingStatus.CONFIRMED})


class BookingError(AutoStudioError):
    """Запись нельзя создать или изменить по правилам бизнеса."""


class SlotTakenError(BookingError):
    """Слот заняли (другой покупатель успел раньше или кнопка устарела)."""


class BookingNotFoundError(BookingError):
    """Записи нет — или она чужая: покупателю это неотличимо, и так и задумано."""


class BookingInactiveError(BookingError):
    """Запись уже отменена."""


class CancellationTooLateError(BookingError):
    """До визита меньше, чем позволяет политика отмены, — покупатель не может отменить сам."""


class Slot(msgspec.Struct, frozen=True, kw_only=True):
    """Свободный промежуток для конкретной услуги и мастера.

    Attributes:
        service_id: Услуга.
        staff_id: Назначенный мастер.
        start: Начало, UTC.
        end: Конец, UTC.
    """

    service_id: str
    staff_id: str
    start: dt.datetime
    end: dt.datetime


class Booking(msgspec.Struct, frozen=True, kw_only=True):
    """Запись покупателя.

    Attributes:
        id: Короткий код (`K7Q2MX`): его называют по телефону и вводят в `/cancel_booking`.
        service_id: Услуга.
        staff_id: Мастер.
        start: Начало, UTC.
        end: Конец, UTC.
        customer: Ключ покупателя в канале (`tg:123456`).
        name: Имя для записи.
        phone: Телефон.
        status: Статус.
        created_at: Когда создана.
        scheduled_at: Когда назначено текущее время (создание или перенос) — от него
            зависит, какие напоминания ещё уместны.
        source: `buttons` или `ai` (слот предложен по сообщению в свободной форме).
        language: Язык покупателя — на нём придут напоминания.
    """

    id: str
    service_id: str
    staff_id: str
    start: dt.datetime
    end: dt.datetime
    customer: str
    name: str
    phone: str
    status: BookingStatus
    created_at: dt.datetime
    scheduled_at: dt.datetime
    source: Source
    language: str

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES


class Customer(msgspec.Struct, frozen=True, kw_only=True):
    """Что бот помнит о покупателе между записями: имя, телефон, язык.

    Attributes:
        key: Ключ покупателя в канале.
        name: Имя или None.
        phone: Телефон или None.
        language: Язык или None.
    """

    key: str
    name: str | None = None
    phone: str | None = None
    language: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.name and self.phone)


class TimeWindow(msgspec.Struct, frozen=True):
    """Желаемое время начала в минутах от местной полуночи (обе границы включительно).

    Attributes:
        start: «после 17:00» → 1020; None — без нижней границы.
        end: «до 12:00» → 720; None — без верхней границы. Совпадает со `start` — точное время.
    """

    start: int | None = None
    end: int | None = None

    @property
    def exact(self) -> bool:
        return self.start is not None and self.start == self.end

    def contains(self, minute: int) -> bool:
        """Попадает ли местное время начала в окно."""
        return (self.start is None or minute >= self.start) and (self.end is None or minute <= self.end)


def local_today(config: BookingConfig, now: dt.datetime) -> dt.date:
    """Сегодняшняя дата в часовом поясе клиента."""
    return now.astimezone(config.client.zone).date()


def horizon_days(config: BookingConfig, now: dt.datetime) -> list[dt.date]:
    """Дни, открытые для записи: сегодня и ещё `horizon_days - 1` вперёд."""
    today = local_today(config, now)
    return [today + dt.timedelta(days=offset) for offset in range(config.booking.horizon_days)]


def in_horizon(config: BookingConfig, day: dt.date, now: dt.datetime) -> bool:
    """Открыт ли день для записи (не в прошлом и не дальше горизонта)."""
    today = local_today(config, now)
    return today <= day < today + dt.timedelta(days=config.booking.horizon_days)


def minute_of_day(moment: dt.datetime, zone: ZoneInfo) -> int:
    """Минута от местной полуночи."""
    local = moment.astimezone(zone)
    return local.hour * 60 + local.minute


def _wall(day: dt.date, minute: int, zone: ZoneInfo, fold: int) -> dt.datetime:
    # Сложение с aware-временем идёт по «настенным» часам — это и нужно: 10:00 местного
    # остаётся 10:00 и в день перевода; какое из двух 02:30 имеется в виду, решает fold.
    moment = dt.datetime.combine(day, dt.time(0), tzinfo=zone) + dt.timedelta(minutes=minute)
    return moment.replace(fold=fold)


def _exists(local: dt.datetime) -> bool:
    back = local.astimezone(dt.UTC).astimezone(local.tzinfo)
    return back.replace(tzinfo=None, fold=0) == local.replace(tzinfo=None, fold=0)


def wall_to_utc(day: dt.date, minute: int, zone: ZoneInfo) -> dt.datetime | None:
    """Начало слота: местное время → UTC.

    Args:
        day: Местная дата.
        minute: Минута от местной полуночи.
        zone: Часовой пояс клиента.

    Returns:
        Момент в UTC; None, если такого местного времени нет (весенний перевод).
        Для повторяющегося осеннего часа — первое вхождение.
    """
    local = _wall(day, minute, zone, fold=0)
    if not _exists(local):
        return None
    return local.astimezone(dt.UTC)


def boundary_to_utc(day: dt.date, minute: int, zone: ZoneInfo) -> dt.datetime:
    """Конец рабочего интервала: самое раннее толкование местного времени в UTC.

    Args:
        day: Местная дата.
        minute: Минута от местной полуночи (1440 — полночь следующего дня).
        zone: Часовой пояс клиента.

    Returns:
        Момент в UTC; для несуществующего или двойного времени — раньший вариант.
    """
    return min(_wall(day, minute, zone, fold).astimezone(dt.UTC) for fold in (0, 1))


def _intersect(left: Sequence[Interval], right: Sequence[Interval]) -> tuple[Interval, ...]:
    out: list[Interval] = []
    for a_start, a_end in left:
        for b_start, b_end in right:
            start, end = max(a_start, b_start), min(a_end, b_end)
            if start < end:
                out.append((start, end))
    return tuple(sorted(out))


def working_intervals(section: BookingSection, member: Staff, weekday: int) -> tuple[Interval, ...]:
    """Часы мастера в день недели: часы бизнеса, пересечённые с его собственными."""
    business = section.hours.for_weekday(weekday)
    if member.hours is None:
        return business
    return _intersect(business, member.hours.for_weekday(weekday))


def _clashes(
    busy: Iterable[tuple[dt.datetime, dt.datetime]], start: dt.datetime, end: dt.datetime, buffer: dt.timedelta
) -> bool:
    return any(start < busy_end + buffer and busy_start < end + buffer for busy_start, busy_end in busy)


def free_slots(
    config: BookingConfig,
    service_id: str,
    day: dt.date,
    bookings: Sequence[Booking],
    now: dt.datetime,
    *,
    staff_id: str | None = None,
    ignore_booking: str | None = None,
) -> list[Slot]:
    """Свободные начала услуги в местный день.

    Слот предлагается, если свободен хоть один подходящий мастер; из свободных
    назначается тот, у кого в этот день меньше записей, при равенстве — первый по
    конфигу. Так «любой свободный» распределяет нагрузку, а результат детерминирован.

    Args:
        config: Конфиг клиента.
        service_id: Услуга (неизвестная — пустой список).
        day: Местная дата.
        bookings: Записи вокруг этого дня (неактивные игнорируются).
        now: Текущий момент.
        staff_id: Только этот мастер; None — любой подходящий.
        ignore_booking: Не считать занятой эту запись (перенос самой себя).

    Returns:
        Слоты по возрастанию времени, по одному на начало.
    """
    section = config.booking
    service = section.service(service_id)
    if service is None or not in_horizon(config, day, now) or day in section.closed_dates:
        return []
    zone = config.client.zone
    candidates = section.eligible_staff(service)
    if staff_id is not None:
        candidates = [member for member in candidates if member.id == staff_id]
    duration = dt.timedelta(minutes=service.duration_min)
    buffer = dt.timedelta(minutes=section.buffer_min)
    earliest = now + dt.timedelta(minutes=section.min_lead_min)
    active = [booking for booking in bookings if booking.active and booking.id != ignore_booking]
    busy: dict[str, list[tuple[dt.datetime, dt.datetime]]] = {}
    for booking in active:
        busy.setdefault(booking.staff_id, []).append((booking.start, booking.end))
    load = Counter(booking.staff_id for booking in active if booking.start.astimezone(zone).date() == day)
    order = {member.id: index for index, member in enumerate(section.staff)}
    step = section.slot_step_min
    offers: dict[dt.datetime, Staff] = {}
    for member in candidates:
        for first, last in working_intervals(section, member, day.weekday()):
            limit = boundary_to_utc(day, last, zone)
            for minute in range(-(-first // step) * step, last, step):
                start = wall_to_utc(day, minute, zone)
                if start is None or start < earliest:
                    continue
                if start + duration > limit or _clashes(busy.get(member.id, ()), start, start + duration, buffer):
                    continue
                current = offers.get(start)
                if current is None or (load[member.id], order[member.id]) < (load[current.id], order[current.id]):
                    offers[start] = member
    return [
        Slot(service_id=service.id, staff_id=offers[start].id, start=start, end=start + duration)
        for start in sorted(offers)
    ]


def find_slot(
    config: BookingConfig,
    service_id: str,
    staff_id: str | None,
    start: dt.datetime,
    bookings: Sequence[Booking],
    now: dt.datetime,
    *,
    ignore_booking: str | None = None,
) -> Slot | None:
    """Проверка в момент подтверждения: свободен ли ещё именно этот слот.

    Args:
        config: Конфиг клиента.
        service_id: Услуга.
        staff_id: Мастер или None — любой свободный в это время.
        start: Начало, UTC.
        bookings: Актуальные записи.
        now: Текущий момент.
        ignore_booking: Запись, которую переносят.

    Returns:
        Слот (с назначенным мастером) или None, если время занято, в прошлом или вне расписания.
    """
    day = start.astimezone(config.client.zone).date()
    slots = free_slots(config, service_id, day, bookings, now, staff_id=staff_id, ignore_booking=ignore_booking)
    return next((slot for slot in slots if slot.start == start), None)


def bookable_days(
    config: BookingConfig,
    service_id: str,
    bookings: Sequence[Booking],
    now: dt.datetime,
    *,
    staff_id: str | None = None,
    start_day: dt.date | None = None,
    limit: int | None = None,
    ignore_booking: str | None = None,
) -> list[dt.date]:
    """Дни горизонта, в которые есть хоть один свободный слот.

    Args:
        config: Конфиг клиента.
        service_id: Услуга.
        bookings: Записи на горизонт.
        now: Текущий момент.
        staff_id: Мастер или None.
        start_day: Не раньше этого дня.
        limit: Не больше стольких дней.
        ignore_booking: Запись, которую переносят.

    Returns:
        Даты по возрастанию.
    """
    out: list[dt.date] = []
    for day in horizon_days(config, now):
        if start_day is not None and day < start_day:
            continue
        if free_slots(config, service_id, day, bookings, now, staff_id=staff_id, ignore_booking=ignore_booking):
            out.append(day)
            if limit is not None and len(out) >= limit:
                break
    return out


def next_free_slots(
    config: BookingConfig,
    service_id: str,
    bookings: Sequence[Booking],
    now: dt.datetime,
    *,
    limit: int,
    staff_id: str | None = None,
    start_day: dt.date | None = None,
    window: TimeWindow | None = None,
    ignore_booking: str | None = None,
) -> list[Slot]:
    """Ближайшие свободные слоты по всем дням горизонта.

    Args:
        config: Конфиг клиента.
        service_id: Услуга.
        bookings: Записи на горизонт.
        now: Текущий момент.
        limit: Сколько слотов вернуть.
        staff_id: Мастер или None.
        start_day: Не раньше этого дня.
        window: Окно времени начала (местное) или None.
        ignore_booking: Запись, которую переносят.

    Returns:
        До `limit` слотов по возрастанию времени.
    """
    zone = config.client.zone
    out: list[Slot] = []
    for day in horizon_days(config, now):
        if start_day is not None and day < start_day:
            continue
        for slot in free_slots(
            config, service_id, day, bookings, now, staff_id=staff_id, ignore_booking=ignore_booking
        ):
            if window is None or window.contains(minute_of_day(slot.start, zone)):
                out.append(slot)
                if len(out) >= limit:
                    return out
    return out


def nearest_slots(
    config: BookingConfig,
    service_id: str,
    target: dt.datetime,
    bookings: Sequence[Booking],
    now: dt.datetime,
    *,
    limit: int,
    staff_id: str | None = None,
    ignore_booking: str | None = None,
) -> list[Slot]:
    """Альтернативы занятому времени: ближайшие к нему в тот же день, затем следующие дни.

    Args:
        config: Конфиг клиента.
        service_id: Услуга.
        target: Желаемое начало, UTC.
        bookings: Записи на горизонт.
        now: Текущий момент.
        limit: Сколько вариантов.
        staff_id: Мастер или None.
        ignore_booking: Запись, которую переносят.

    Returns:
        До `limit` слотов по возрастанию времени.
    """
    day = target.astimezone(config.client.zone).date()
    same_day = free_slots(config, service_id, day, bookings, now, staff_id=staff_id, ignore_booking=ignore_booking)
    picked = sorted(same_day, key=lambda slot: (abs(slot.start - target), slot.start))[:limit]
    if len(picked) < limit:
        picked += next_free_slots(
            config,
            service_id,
            bookings,
            now,
            limit=limit - len(picked),
            staff_id=staff_id,
            start_day=day + dt.timedelta(days=1),
            ignore_booking=ignore_booking,
        )
    return sorted(picked, key=lambda slot: slot.start)


def is_open_at(config: BookingConfig, moment: dt.datetime) -> bool:
    """Открыт ли бизнес в этот момент (для метрики «записались, пока вы были закрыты»)."""
    zone = config.client.zone
    local = moment.astimezone(zone)
    if local.date() in config.booking.closed_dates:
        return False
    minute = minute_of_day(moment, zone)
    return any(start <= minute < end for start, end in config.booking.hours.for_weekday(local.weekday()))


_NAME_PUNCT: Final = frozenset(" .'’-")
_PHONE_CHARS: Final = frozenset('0123456789+-() .')
NAME_MAX: Final = 60


def clean_name(text: str) -> str | None:
    """Имя для записи: буквы любого алфавита, пробел, точка, апостроф, дефис; 2–60 символов.

    Args:
        text: Что прислал покупатель.

    Returns:
        Нормализованное имя или None, если это не похоже на имя (цифры, ссылки, команды).
    """
    name = ' '.join(text.split())
    if not 2 <= len(name) <= NAME_MAX or not name[0].isalpha():
        return None
    if any(not (char.isalpha() or char in _NAME_PUNCT) for char in name):
        return None
    return name if sum(char.isalpha() for char in name) >= 2 else None


def clean_phone(text: str, *, international: bool = False) -> str | None:
    """Телефон: цифры (7–15, как в E.164) и необязательный `+` в начале.

    Args:
        text: Номер, набранный покупателем или из контакта Telegram.
        international: Номер из контакта Telegram — он всегда с кодом страны, `+` дописывается.

    Returns:
        `+995555123456` / `555123456` или None.
    """
    raw = text.strip()
    if not raw or any(char not in _PHONE_CHARS for char in raw) or '+' in raw[1:]:
        return None
    digits = ''.join(char for char in raw if char.isdigit())
    if not 7 <= len(digits) <= 15:
        return None
    return f'+{digits}' if raw.startswith('+') or international else digits


def minutes_until(moment: dt.datetime, now: dt.datetime) -> float:
    """Сколько минут до момента (отрицательно — уже прошёл)."""
    return (moment - now).total_seconds() / 60


def day_bounds(day: dt.date, zone: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
    """Местные сутки как полуинтервал UTC [начало, конец)."""
    return boundary_to_utc(day, 0, zone), boundary_to_utc(day, MINUTES_PER_DAY, zone)

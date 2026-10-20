"""Расписание и записи: чистые функции без ввода-вывода.

Свободен ли слот, считает только этот модуль — ни модель, ни кнопка из старого
сообщения не могут его обойти. Внутри всё время в UTC; часы работы — в местном
времени клиента, и переход между ними сделан здесь, с учётом перевода часов:

- весной местного времени 02:00–03:00 не существует — такие начала пропускаются;
- осенью 02:00–03:00 бывает дважды — предлагается только первое (летнее) вхождение,
  чтобы покупатель не увидел два одинаковых «02:30»; конец интервала берётся по самому
  раннему толкованию, чтобы услуга точно уложилась до закрытия.
"""

import bisect
import datetime as dt
import itertools
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Final, Literal
from zoneinfo import ZoneInfo

import msgspec
import phonenumbers

from autostudio.booking.config import MINUTES_PER_DAY, BookingConfig, BookingSection, Interval, Staff
from autostudio.errors import AutoStudioError

# `admin` — запись «с улицы» или по телефону, которую внёс администратор через бота.
type Source = Literal['buttons', 'ai', 'admin']
type Span = tuple[dt.datetime, dt.datetime]
"""Полуинтервал времени [начало, конец), UTC."""
type BusyMap = Mapping[str, Sequence[Span]]
"""Занятость мастеров вне базы записей (Google Calendar): id мастера → промежутки, UTC."""


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


class TooManyBookingsError(BookingError):
    """У покупателя уже столько предстоящих записей, сколько разрешено (`max_active_per_customer`)."""


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
        source: `buttons`, `ai` (слот предложен по сообщению в свободной форме) или `admin`
            (запись внёс администратор; напоминаний нет — у гостя нет чата с ботом).
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
        source: Откуда пришёл впервые (метка deep link `t.me/<bot>?start=instagram`,
            реклама Click-to-WhatsApp) или None.
    """

    key: str
    name: str | None = None
    phone: str | None = None
    language: str | None = None
    source: str | None = None

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


class _Busy:
    """Занятые промежутки одного мастера, отсортированные для поиска делением пополам.

    Проверка «пересекается ли [start, end) с чем-то, с учётом паузы» — это: среди записей,
    начавшихся раньше `end + buffer`, самая поздно заканчивающаяся кончается позже
    `start - buffer`. Префиксный максимум концов делает её O(log n) на начало слота даже
    для пересекающихся записей (их не бывает, но код на это не полагается).
    """

    def __init__(self, intervals: Iterable[tuple[dt.datetime, dt.datetime]]) -> None:
        ordered = sorted(intervals)
        self._starts = [start for start, _ in ordered]
        self._reach = list(itertools.accumulate((end for _, end in ordered), max))

    def clashes(self, start: dt.datetime, end: dt.datetime, buffer: dt.timedelta) -> bool:
        index = bisect.bisect_left(self._starts, end + buffer)
        return index > 0 and self._reach[index - 1] + buffer > start


def free_slots(
    config: BookingConfig,
    service_id: str,
    day: dt.date,
    bookings: Sequence[Booking],
    now: dt.datetime,
    *,
    staff_id: str | None = None,
    ignore_booking: str | None = None,
    busy: BusyMap | None = None,
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
        busy: Занятость мастеров из внешнего календаря — закрывает время так же, как
            запись (с паузой `buffer_min`), но не входит в «нагрузку» при выборе мастера.

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
    day_start, day_end = day_bounds(day, zone)
    # Сюда приходят записи на весь горизонт, а функцию зовут на каждый день и каждое нажатие:
    # без отсева по дню проверка каждого начала шла бы по всем записям мастера, и полный
    # календарь большой команды считался бы секундами в цикле событий.
    intervals: dict[str, list[tuple[dt.datetime, dt.datetime]]] = {}
    load: Counter[str] = Counter()
    for booking in bookings:
        if not booking.active or booking.id == ignore_booking:
            continue
        if booking.end + buffer > day_start and booking.start < day_end + buffer:
            intervals.setdefault(booking.staff_id, []).append((booking.start, booking.end))
        if day_start <= booking.start < day_end:
            load[booking.staff_id] += 1
    for member in candidates:
        for start, end in (busy or {}).get(member.id, ()):
            if end + buffer > day_start and start < day_end + buffer:
                intervals.setdefault(member.id, []).append((start, end))
    taken = {member.id: _Busy(intervals.get(member.id, ())) for member in candidates}
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
                if start + duration > limit or taken[member.id].clashes(start, start + duration, buffer):
                    continue
                current = offers.get(start)
                if current is None or (load[member.id], order[member.id]) < (load[current.id], order[current.id]):
                    offers[start] = member
    return [
        Slot(service_id=service.id, staff_id=offers[start].id, start=start, end=start + duration)
        for start in sorted(offers)
    ]


def _by_day(config: BookingConfig, bookings: Sequence[Booking]) -> dict[dt.date, list[Booking]]:
    # Многодневный перебор (дни для кнопок, ближайшие слоты) иначе фильтровал бы весь
    # горизонт записей заново на каждый день. Запись кладётся во все местные дни, которых
    # касается вместе с паузой мастера: этого достаточно для проверки пересечений в `free_slots`.
    zone = config.client.zone
    buffer = dt.timedelta(minutes=config.booking.buffer_min)
    out: dict[dt.date, list[Booking]] = {}
    for booking in bookings:
        if not booking.active:
            continue
        day = (booking.start - buffer).astimezone(zone).date()
        last = (booking.end + buffer).astimezone(zone).date()
        while day <= last:
            out.setdefault(day, []).append(booking)
            day += dt.timedelta(days=1)
    return out


def find_slot(
    config: BookingConfig,
    service_id: str,
    staff_id: str | None,
    start: dt.datetime,
    bookings: Sequence[Booking],
    now: dt.datetime,
    *,
    ignore_booking: str | None = None,
    busy: BusyMap | None = None,
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
        busy: Занятость из внешнего календаря.

    Returns:
        Слот (с назначенным мастером) или None, если время занято, в прошлом или вне расписания.
    """
    day = start.astimezone(config.client.zone).date()
    slots = free_slots(
        config, service_id, day, bookings, now, staff_id=staff_id, ignore_booking=ignore_booking, busy=busy
    )
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
    busy: BusyMap | None = None,
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
        busy: Занятость из внешнего календаря.

    Returns:
        Даты по возрастанию.
    """
    out: list[dt.date] = []
    by_day = _by_day(config, bookings)
    for day in horizon_days(config, now):
        if start_day is not None and day < start_day:
            continue
        around = by_day.get(day, [])
        if free_slots(
            config, service_id, day, around, now, staff_id=staff_id, ignore_booking=ignore_booking, busy=busy
        ):
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
    busy: BusyMap | None = None,
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
        busy: Занятость из внешнего календаря.

    Returns:
        До `limit` слотов по возрастанию времени.
    """
    zone = config.client.zone
    out: list[Slot] = []
    by_day = _by_day(config, bookings)
    for day in horizon_days(config, now):
        if start_day is not None and day < start_day:
            continue
        around = by_day.get(day, [])
        found = free_slots(
            config, service_id, day, around, now, staff_id=staff_id, ignore_booking=ignore_booking, busy=busy
        )
        for slot in found:
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
    busy: BusyMap | None = None,
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
        busy: Занятость из внешнего календаря.

    Returns:
        До `limit` слотов по возрастанию времени.
    """
    day = target.astimezone(config.client.zone).date()
    same_day = free_slots(
        config, service_id, day, bookings, now, staff_id=staff_id, ignore_booking=ignore_booking, busy=busy
    )
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
            busy=busy,
        )
    return sorted(picked, key=lambda slot: slot.start)


def subtract_spans(spans: Iterable[Span], holes: Iterable[Span]) -> list[Span]:
    """Части промежутков `spans`, не покрытые ни одним из `holes`.

    Google freeBusy возвращает занятость календаря целиком, вместе с событиями наших же
    записей. Когда календарь общий у нескольких мастеров (календарь бизнеса), событие
    записи к Гиорги закрыло бы это время и Нике; вычитание своих записей возвращает
    календарю только «чужую» занятость. Цена решения: чужое событие, которое совпадает
    по времени с нашей записью в общем календаре, на время этой записи не учитывается.

    Args:
        spans: Занятые промежутки.
        holes: Что вычесть.

    Returns:
        Оставшиеся промежутки по возрастанию начала.
    """
    cuts = sorted(hole for hole in holes if hole[0] < hole[1])
    out: list[Span] = []
    for start, end in sorted(spans):
        cursor = start
        for cut_start, cut_end in cuts:
            if cut_end <= cursor or cut_start >= end:
                continue
            if cut_start > cursor:
                out.append((cursor, cut_start))
            cursor = max(cursor, cut_end)
            if cursor >= end:
                break
        if cursor < end:
            out.append((cursor, end))
    return out


def will_remind(config: BookingConfig, booking: Booking, now: dt.datetime) -> bool:
    """Придёт ли по свежей записи хоть одно напоминание (то же правило, что в `due_reminders`).

    Запись за час до визита не получит «напоминание за сутки и за 2 часа» — и бот не
    должен его обещать.
    """
    if booking.source == 'admin':
        return False
    return any(booking.start - dt.timedelta(minutes=minutes) >= now for minutes in config.booking.reminders_min_before)


def is_open_at(config: BookingConfig, moment: dt.datetime) -> bool:
    """Открыт ли бизнес в этот момент (для метрики «записались, пока вы были закрыты»)."""
    zone = config.client.zone
    local = moment.astimezone(zone)
    if local.date() in config.booking.closed_dates:
        return False
    minute = minute_of_day(moment, zone)
    return any(start <= minute < end for start, end in config.booking.hours.for_weekday(local.weekday()))


_NAME_PUNCT: Final = frozenset(" .'’-")
_PHONE_MAX: Final = 32
NAME_MAX: Final = 60
NAME_MAX_WORDS: Final = 3
# Вводные слова, с которых люди начинают ответ на «как вас записать?»: «Меня зовут Анна»
# должно стать «Анна», а не именем записи, которое владелец увидит в расписании.
_NAME_LEAD_IN: Final = re.compile(
    r'^(?:меня\s+зовут|зовут|мо[её]\s+имя|имя\s*:|запишите\s+(?:на|как)|это|я|'
    r"my\s+name\s+is|my\s+name'?s|name\s*:|i\s*['’]\s*m|i\s+am|this\s+is|it\s*['’]\s*s|it\s+is)\s+",
    re.IGNORECASE,
)


def clean_name(text: str) -> str | None:
    """Имя для записи: буквы любого алфавита, пробел, точка, апостроф, дефис; 2–60 символов, до 3 слов.

    Вводные слова («меня зовут», «my name is», «это») отбрасываются, имя целиком в
    нижнем регистре пишется с заглавных букв.

    Args:
        text: Что прислал покупатель.

    Returns:
        Нормализованное имя или None, если это не похоже на имя (цифры, ссылки, команды, фраза).
    """
    name = _NAME_LEAD_IN.sub('', ' '.join(text.split())).rstrip('!.,')
    if not 2 <= len(name) <= NAME_MAX or not name[0].isalpha() or len(name.split()) > NAME_MAX_WORDS:
        return None
    if any(not (char.isalpha() or char in _NAME_PUNCT) for char in name):
        return None
    if sum(char.isalpha() for char in name) < 2:
        return None
    return name.title() if name.islower() else name


def clean_phone(text: str, *, region: str, shared: bool = False) -> str | None:
    """Телефон в E.164 (`+995599123456`) через libphonenumber.

    Набранный руками номер (и чужая карточка контакта) проверяется строго
    (`is_valid_number`): опечатка в номере — это клиент, до которого владелец не дозвонится.
    Собственный номер покупателя из «Отправить мой номер» Telegram берёт из его аккаунта,
    поэтому для него достаточно проверки формата: так проходят номера новых диапазонов,
    которых ещё нет в базе libphonenumber.

    Args:
        text: Номер, набранный покупателем или из контакта Telegram.
        region: Страна для номеров без кода (`GE`: «599 123 456» → +995599123456).
        shared: Собственный номер отправителя из Telegram (адаптер сверил `contact.user_id`).

    Returns:
        Номер в E.164 или None.
    """
    raw = text.strip()
    if not raw or len(raw) > _PHONE_MAX:
        return None
    candidates = [raw]
    if not raw.startswith('+'):
        # Telegram отдаёт номер контакта без «+», а люди так же набирают международный номер:
        # «995599123456». Сначала — как местный, затем — как международный.
        candidates = [f'+{raw}'] if shared else [raw, f'+{raw}']
    for candidate in candidates:
        try:
            number = phonenumbers.parse(candidate, region)
        except phonenumbers.NumberParseException:
            continue
        ok = phonenumbers.is_possible_number(number) if shared else phonenumbers.is_valid_number(number)
        if ok:
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return None


_SOURCE: Final = re.compile(r'^[a-z0-9_-]{1,64}$')


def clean_source(raw: str | None) -> str | None:
    """Метка источника из deep link: `site`, `instagram`, `maps_2026`.

    Правила — как у параметра `start` в Telegram (A-Z, a-z, 0-9, `_`, `-`, до 64 знаков);
    регистр не важен — «Instagram» и «instagram» в отчёте одно и то же. Всё остальное —
    мусор или попытка подсунуть текст в отчёт владельцу, и он отбрасывается.

    Args:
        raw: Что прислал канал.

    Returns:
        Метка в нижнем регистре или None.
    """
    if raw is None:
        return None
    label = raw.strip().lower()
    return label if _SOURCE.match(label) else None


def example_phone(region: str) -> str:
    """Пример мобильного номера страны для подсказки (`+995 555 12 34 56` для GE)."""
    number = phonenumbers.example_number_for_type(region, phonenumbers.PhoneNumberType.MOBILE)
    if number is None:  # pragma: no cover - у всех стран из SUPPORTED_REGIONS есть пример
        return '+1 201 555 0123'
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


def format_phone(value: str) -> str:
    """Телефон для людей: `+995 599 12 34 56` (в Telegram по нему можно нажать и позвонить)."""
    try:
        number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        return value
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


def minutes_until(moment: dt.datetime, now: dt.datetime) -> float:
    """Сколько минут до момента (отрицательно — уже прошёл)."""
    return (moment - now).total_seconds() / 60


def day_bounds(day: dt.date, zone: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
    """Местные сутки как полуинтервал UTC [начало, конец)."""
    return boundary_to_utc(day, 0, zone), boundary_to_utc(day, MINUTES_PER_DAY, zone)

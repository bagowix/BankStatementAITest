"""Записи, напоминания и покупатели в SQLite.

Хранится только то, без чего запись не работает: имя и телефон в записи (владелец
звонит по ним), язык покупателя (на нём напоминание) и отметки об отправленных
напоминаниях. Сообщения переписки не хранятся.

Две гарантии, ради которых модуль написан так, а не через ORM:

- запись создаётся в одной транзакции `BEGIN IMMEDIATE` с повторной проверкой
  пересечения: два покупателя, нажавшие «Подтвердить» на один слот одновременно
  (или два процесса бота по ошибке), не получат оба подтверждение;
- напоминание сначала «застолбляется» строкой в `reminders`, потом отправляется: после
  перезапуска процесса или при двух процессах одно напоминание не уходит дважды;
- лимит предстоящих записей одного покупателя проверяется в той же транзакции, что и
  вставка: два быстрых «Подтвердить» не обойдут его.
"""

import datetime as dt
import secrets
import sqlite3
from collections.abc import Generator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final

import msgspec

from autostudio.booking.domain import (
    Booking,
    BookingError,
    BookingInactiveError,
    BookingNotFoundError,
    BookingStatus,
    CancellationTooLateError,
    Customer,
    Slot,
    SlotTakenError,
    Source,
    TooManyBookingsError,
)

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    customer TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    source TEXT NOT NULL,
    language TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bookings_start ON bookings (start_at);
CREATE INDEX IF NOT EXISTS bookings_customer ON bookings (customer, start_at);
CREATE TABLE IF NOT EXISTS reminders (
    booking_id TEXT NOT NULL,
    minutes_before INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (booking_id, minutes_before)
);
CREATE TABLE IF NOT EXISTS customers (
    key TEXT PRIMARY KEY,
    name TEXT,
    phone TEXT,
    language TEXT,
    updated_at TEXT NOT NULL
);
"""

# Без I, O, 0, 1: код записи диктуют по телефону и набирают руками.
_ID_ALPHABET: Final = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_ID_LENGTH: Final = 6
_BUSY_TIMEOUT_S: Final = 10.0

# SQL — только литералы: ни одного значения в тексте запроса, всё через параметры.
# Активные статусы (`pending`, `confirmed`) повторены в запросах намеренно — см. ACTIVE_STATUSES.
_CLASH: Final = (
    "SELECT 1 FROM bookings WHERE staff_id = ? AND status IN ('pending', 'confirmed') "
    'AND start_at < ? AND end_at > ? AND id != ? LIMIT 1'
)
_INSERT: Final = 'INSERT INTO bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
_BY_ID: Final = 'SELECT * FROM bookings WHERE id = ?'
_OVERLAPPING: Final = (
    "SELECT * FROM bookings WHERE status IN ('pending', 'confirmed') AND start_at < ? AND end_at > ? ORDER BY start_at"
)
_UPCOMING: Final = (
    "SELECT * FROM bookings WHERE status IN ('pending', 'confirmed') AND start_at >= ? AND start_at < ? "
    'ORDER BY start_at, staff_id'
)
_FOR_CUSTOMER: Final = (
    "SELECT * FROM bookings WHERE customer = ? AND status IN ('pending', 'confirmed') AND start_at > ? "
    'ORDER BY start_at'
)
_ACTIVE_COUNT: Final = (
    "SELECT COUNT(*) FROM bookings WHERE customer = ? AND status IN ('pending', 'confirmed') AND start_at > ?"
)
# Записи, внесённые администратором (`admin`), без напоминаний: чат записи — администратора.
_REMINDER_CANDIDATES: Final = (
    "SELECT * FROM bookings WHERE status IN ('pending', 'confirmed') AND source != 'admin' "
    'AND start_at > ? AND start_at <= ? ORDER BY start_at'
)

_SENT: Final = 'SELECT minutes_before FROM reminders WHERE booking_id = ?'
_RELEASE: Final = 'DELETE FROM reminders WHERE booking_id = ? AND minutes_before = ?'
_CLAIM: Final = 'INSERT OR IGNORE INTO reminders (booking_id, minutes_before, sent_at) VALUES (?, ?, ?)'

type _Value = str | int | float | bytes | None
type _Row = tuple[_Value, ...]


def new_booking_id() -> str:
    """Случайный код записи из 6 знаков (~10⁹ вариантов): не угадать чужой перебором."""
    return ''.join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


def _ts(moment: dt.datetime) -> str:
    # Один формат для всех моментов: строки сравниваются в SQL лексикографически.
    if moment.tzinfo is None:
        raise ValueError('время записи должно быть с часовым поясом')
    return moment.astimezone(dt.UTC).strftime('%Y-%m-%dT%H:%M:%S+00:00')


def _text(value: _Value) -> str:
    if not isinstance(value, str):
        raise BookingError(f'повреждённое значение в базе записей: {value!r}')
    return value


def _optional(value: _Value) -> str | None:
    return value if isinstance(value, str) and value else None


def _moment(value: _Value) -> dt.datetime:
    return dt.datetime.fromisoformat(_text(value))


def _source(value: _Value) -> Source:
    text = _text(value)
    if text in ('ai', 'admin'):
        return 'ai' if text == 'ai' else 'admin'
    return 'buttons'


def _booking(row: _Row) -> Booking:
    # Порядок колонок — как в CREATE TABLE (`SELECT *`).
    return Booking(
        id=_text(row[0]),
        service_id=_text(row[1]),
        staff_id=_text(row[2]),
        start=_moment(row[3]),
        end=_moment(row[4]),
        customer=_text(row[5]),
        name=_text(row[6]),
        phone=_text(row[7]),
        status=BookingStatus(_text(row[8])),
        created_at=_moment(row[9]),
        scheduled_at=_moment(row[10]),
        source=_source(row[11]),
        language=_text(row[12]),
    )


class _AlreadyClaimedError(BookingError):
    """Отметку напоминания уже поставил другой процесс."""


class DueReminder(msgspec.Struct, frozen=True, kw_only=True):
    """Напоминание, которое пора отправить.

    Attributes:
        booking: Запись.
        minutes_before: Какое напоминание отправляется (ближайшее к визиту из просроченных).
        covers: Все просроченные отметки — они закрываются одной отправкой, чтобы после
            простоя бота покупатель не получил два напоминания подряд.
    """

    booking: Booking
    minutes_before: int
    covers: tuple[int, ...]


class BookingStore:
    """SQLite-хранилище записей одного клиента.

    Вызовы синхронные, как в журнале событий ядра: операция — доли миллисекунды на
    локальном диске, а гонки между процессами закрывает блокировка SQLite.

    Args:
        path: Файл базы; каталог создаётся.
        readonly: Только чтение (для `check` и `agenda`): файл не создаётся и не меняется.
    """

    def __init__(self, path: Path, *, readonly: bool = False) -> None:
        self._path = path
        self._readonly = readonly
        if readonly:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        if self._readonly:
            target, uri = f'{self._path.resolve().as_uri()}?mode=ro', True
        else:
            target, uri = str(self._path), False
        # isolation_level=None: транзакциями управляем сами (BEGIN IMMEDIATE ниже).
        with closing(sqlite3.connect(target, timeout=_BUSY_TIMEOUT_S, isolation_level=None, uri=uri)) as conn:
            yield conn

    @contextmanager
    def _write(self) -> Generator[sqlite3.Connection]:
        if self._readonly:
            raise BookingError('хранилище открыто только для чтения')
        with self._connect() as conn:
            # IMMEDIATE берёт блокировку записи сразу: проверка «слот свободен» и вставка
            # идут без окна, в которое успеет вклиниться второй писатель.
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
            except BaseException:
                conn.execute('ROLLBACK')
                raise
            conn.execute('COMMIT')

    def _rows(self, sql: str, params: tuple[str | int, ...]) -> list[_Row]:
        if self._readonly and not self._path.exists():
            return []
        with self._connect() as conn:
            return [tuple(row) for row in conn.execute(sql, params).fetchall()]

    def _bookings(self, sql: str, params: tuple[str | int, ...]) -> list[Booking]:
        return [_booking(row) for row in self._rows(sql, params)]

    @staticmethod
    def _get(conn: sqlite3.Connection, booking_id: str) -> Booking | None:
        row = conn.execute(_BY_ID, (booking_id,)).fetchone()
        return None if row is None else _booking(tuple(row))

    @staticmethod
    def _clash(conn: sqlite3.Connection, slot: Slot, buffer: dt.timedelta, ignore: str) -> bool:
        params = (slot.staff_id, _ts(slot.end + buffer), _ts(slot.start - buffer), ignore)
        return conn.execute(_CLASH, params).fetchone() is not None

    def create(
        self,
        slot: Slot,
        *,
        customer: Customer,
        source: Source,
        language: str,
        now: dt.datetime,
        buffer: dt.timedelta,
        max_active: int | None = None,
    ) -> Booking:
        """Создаёт запись, если слот всё ещё свободен у этого мастера.

        Args:
            slot: Проверенный слот (мастер назначен).
            customer: Чат, который ведёт запись (ему — напоминания и отмена кнопками), и имя с
                телефоном для записи; имя и телефон обязательны. При записи администратором
                «с улицы» ключ — чат администратора, а имя и телефон — гостя.
            source: Откуда пришла запись.
            language: Язык покупателя.
            now: Текущий момент.
            buffer: Пауза между записями мастера.
            max_active: Не больше стольких предстоящих записей у этого чата; None — без лимита.

        Returns:
            Созданная запись.

        Raises:
            SlotTakenError: Время у мастера уже занято.
            TooManyBookingsError: Лимит предстоящих записей исчерпан.
            BookingError: Нет имени или телефона.
        """
        if not customer.name or not customer.phone:
            raise BookingError('для записи нужны имя и телефон')
        chat = customer.key
        with self._write() as conn:
            if max_active is not None:
                row = conn.execute(_ACTIVE_COUNT, (chat, _ts(now))).fetchone()
                if row is not None and int(row[0]) >= max_active:
                    raise TooManyBookingsError(chat)
            if self._clash(conn, slot, buffer, ''):
                raise SlotTakenError(f'{slot.staff_id} {_ts(slot.start)}')
            booking_id = new_booking_id()
            while conn.execute('SELECT 1 FROM bookings WHERE id = ?', (booking_id,)).fetchone() is not None:
                booking_id = new_booking_id()  # pragma: no cover - совпадение 1 к 10⁹
            booking = Booking(
                id=booking_id,
                service_id=slot.service_id,
                staff_id=slot.staff_id,
                start=slot.start,
                end=slot.end,
                customer=chat,
                name=customer.name,
                phone=customer.phone,
                status=BookingStatus.CONFIRMED,
                created_at=now,
                scheduled_at=now,
                source=source,
                language=language,
            )
            conn.execute(
                _INSERT,
                (
                    booking.id,
                    booking.service_id,
                    booking.staff_id,
                    _ts(booking.start),
                    _ts(booking.end),
                    booking.customer,
                    booking.name,
                    booking.phone,
                    booking.status.value,
                    _ts(now),
                    _ts(now),
                    source,
                    language,
                ),
            )
        return booking

    def get(self, booking_id: str) -> Booking | None:
        """Запись по коду или None."""
        found = self._bookings(_BY_ID, (booking_id,))
        return found[0] if found else None

    def _editable(
        self,
        conn: sqlite3.Connection,
        booking_id: str,
        *,
        now: dt.datetime,
        by_admin: bool,
        customer: str | None,
        notice: dt.timedelta,
    ) -> Booking:
        booking = self._get(conn, booking_id)
        # Чужая запись неотличима от несуществующей: код из кнопки можно подделать.
        if booking is None or (customer is not None and booking.customer != customer):
            raise BookingNotFoundError(booking_id)
        if not booking.active:
            raise BookingInactiveError(booking_id)
        if not by_admin and booking.start - now < notice:
            raise CancellationTooLateError(booking_id)
        return booking

    def cancel(
        self,
        booking_id: str,
        *,
        now: dt.datetime,
        by_admin: bool,
        customer: str | None = None,
        notice: dt.timedelta = dt.timedelta(0),
    ) -> Booking:
        """Отменяет запись.

        Args:
            booking_id: Код записи.
            now: Текущий момент.
            by_admin: Отменяет владелец — политика отмены на него не действует.
            customer: Ключ покупателя: отменить можно только свою запись. None — без проверки.
            notice: Не позже чем за столько до начала (для покупателя).

        Returns:
            Отменённая запись.

        Raises:
            BookingNotFoundError: Нет такой записи (или чужая).
            BookingInactiveError: Уже отменена.
            CancellationTooLateError: Покупатель отменяет слишком поздно.
        """
        with self._write() as conn:
            booking = self._editable(conn, booking_id, now=now, by_admin=by_admin, customer=customer, notice=notice)
            conn.execute('UPDATE bookings SET status = ? WHERE id = ?', (BookingStatus.CANCELLED.value, booking_id))
        return msgspec.structs.replace(booking, status=BookingStatus.CANCELLED)

    def reschedule(
        self,
        booking_id: str,
        slot: Slot,
        *,
        now: dt.datetime,
        by_admin: bool,
        customer: str | None = None,
        notice: dt.timedelta = dt.timedelta(0),
        buffer: dt.timedelta = dt.timedelta(0),
    ) -> tuple[Booking, Booking]:
        """Переносит запись на другой слот той же услуги; код записи не меняется.

        Напоминания для нового времени отправятся заново: отметки старых удаляются.

        Args:
            booking_id: Код записи.
            slot: Новый слот (проверенный, мастер назначен).
            now: Текущий момент.
            by_admin: Переносит владелец.
            customer: Ключ покупателя или None.
            notice: Политика: не позже чем за столько до старого начала.
            buffer: Пауза между записями мастера.

        Returns:
            Запись до и после переноса.

        Raises:
            BookingNotFoundError: Нет такой записи (или чужая).
            BookingInactiveError: Уже отменена.
            CancellationTooLateError: Слишком поздно для покупателя.
            SlotTakenError: Новое время занято.
            BookingError: Слот другой услуги.
        """
        with self._write() as conn:
            old = self._editable(conn, booking_id, now=now, by_admin=by_admin, customer=customer, notice=notice)
            if slot.service_id != old.service_id:
                raise BookingError('перенос меняет время, а не услугу')
            if self._clash(conn, slot, buffer, booking_id):
                raise SlotTakenError(f'{slot.staff_id} {_ts(slot.start)}')
            conn.execute(
                'UPDATE bookings SET staff_id = ?, start_at = ?, end_at = ?, scheduled_at = ? WHERE id = ?',
                (slot.staff_id, _ts(slot.start), _ts(slot.end), _ts(now), booking_id),
            )
            conn.execute('DELETE FROM reminders WHERE booking_id = ?', (booking_id,))
        new = msgspec.structs.replace(old, staff_id=slot.staff_id, start=slot.start, end=slot.end, scheduled_at=now)
        return old, new

    def overlapping(self, start: dt.datetime, end: dt.datetime) -> list[Booking]:
        """Активные записи, пересекающие [start, end) — вход для расчёта слотов."""
        return self._bookings(_OVERLAPPING, (_ts(end), _ts(start)))

    def upcoming(self, start: dt.datetime, end: dt.datetime) -> list[Booking]:
        """Активные записи, начинающиеся в [start, end), по времени (расписание дня или недели)."""
        return self._bookings(_UPCOMING, (_ts(start), _ts(end)))

    def for_customer(self, customer: str, now: dt.datetime) -> list[Booking]:
        """Предстоящие активные записи покупателя."""
        return self._bookings(_FOR_CUSTOMER, (customer, _ts(now)))

    def active_count(self, customer: str, now: dt.datetime) -> int:
        """Сколько предстоящих активных записей у покупателя."""
        rows = self._rows(_ACTIVE_COUNT, (customer, _ts(now)))
        value = rows[0][0] if rows else 0
        return value if isinstance(value, int) else 0

    def due_reminders(self, now: dt.datetime, offsets: Sequence[int]) -> list[DueReminder]:
        """Напоминания, которые пора отправить.

        Отметка «за N минут» просрочена, если момент `начало − N` наступил, а запись была
        назначена раньше этого момента (запись за час до визита не получает «напоминание
        за сутки»). Из нескольких просроченных отправляется ближайшее к визиту.

        Args:
            now: Текущий момент.
            offsets: Отметки из конфига, минуты до начала.

        Returns:
            Напоминания по времени визита.
        """
        if not offsets:
            return []
        horizon = now + dt.timedelta(minutes=max(offsets))
        out: list[DueReminder] = []
        for booking in self._bookings(_REMINDER_CANDIDATES, (_ts(now), _ts(horizon))):
            sent = {row[0] for row in self._rows(_SENT, (booking.id,))}
            due = sorted(
                minutes
                for minutes in offsets
                if minutes not in sent
                and (moment := booking.start - dt.timedelta(minutes=minutes)) <= now
                and booking.scheduled_at <= moment
            )
            if due:
                out.append(DueReminder(booking=booking, minutes_before=due[0], covers=tuple(due)))
        return out

    def claim_reminder(self, reminder: DueReminder, now: dt.datetime) -> Booking | None:
        """Застолбляет напоминание перед отправкой.

        Запись перечитывается в той же транзакции: если её успели отменить или перенести
        после `due_reminders` (покупатель нажал «Перенести», пока цикл отправлял другие
        напоминания), отметка не ставится — иначе покупатель получил бы старое время, а
        напоминание о новом считалось бы уже отправленным.

        Args:
            reminder: Что отправляем.
            now: Текущий момент.

        Returns:
            Актуальная запись — по ней и строится текст; None — напоминание уже застолбил
            другой процесс, запись отменили или перенесли (новое время посчитает следующий проход).
        """
        due = reminder.booking
        try:
            with self._write() as conn:
                current = self._get(conn, due.id)
                if current is None or not current.active:
                    return None
                if (current.start, current.staff_id, current.scheduled_at) != (
                    due.start,
                    due.staff_id,
                    due.scheduled_at,
                ):
                    return None
                for minutes in reminder.covers:
                    if not conn.execute(_CLAIM, (due.id, minutes, _ts(now))).rowcount:
                        # Другой процесс успел первым: исключение откатит всё вставленное выше.
                        raise _AlreadyClaimedError(due.id)
        except _AlreadyClaimedError:
            return None
        return current

    def release_reminder(self, reminder: DueReminder) -> None:
        """Снимает отметку, если отправка не удалась временно: напоминание уйдёт в следующий проход."""
        with self._write() as conn:
            for minutes in reminder.covers:
                conn.execute(_RELEASE, (reminder.booking.id, minutes))

    def customer(self, key: str) -> Customer | None:
        """Что известно о покупателе, или None."""
        rows = self._rows('SELECT key, name, phone, language FROM customers WHERE key = ?', (key,))
        if not rows:
            return None
        found, name, phone, language = rows[0][:4]
        return Customer(key=_text(found), name=_optional(name), phone=_optional(phone), language=_optional(language))

    def find_customer(self, ref: str) -> str | None:
        """Ключ покупателя по ключу целиком (`tg:123`) или по номеру чата без префикса (`123`).

        Администратор набирает ключ руками в `/reply`: без префикса канала его всё равно
        стоит понять, но только если такой покупатель один.

        Args:
            ref: Что набрал администратор.

        Returns:
            Ключ известного покупателя или None.
        """
        ref = ref.strip()
        if not ref:
            return None
        if self._rows('SELECT key FROM customers WHERE key = ?', (ref,)):
            return ref
        if ':' in ref:
            return None
        rows = self._rows("SELECT key FROM customers WHERE substr(key, instr(key, ':') + 1) = ? LIMIT 2", (ref,))
        return _text(rows[0][0]) if len(rows) == 1 else None

    def save_customer(self, customer: Customer, now: dt.datetime) -> None:
        """Запоминает покупателя (поля None не затирают известные)."""
        with self._write() as conn:
            conn.execute(
                'INSERT INTO customers (key, name, phone, language, updated_at) VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(key) DO UPDATE SET name = COALESCE(excluded.name, name), '
                'phone = COALESCE(excluded.phone, phone), language = COALESCE(excluded.language, language), '
                'updated_at = excluded.updated_at',
                (customer.key, customer.name, customer.phone, customer.language, _ts(now)),
            )

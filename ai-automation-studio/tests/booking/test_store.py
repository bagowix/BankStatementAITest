import datetime as dt
import re
import threading

import pytest

from autostudio.booking import store as store_module
from autostudio.booking.domain import (
    ACTIVE_STATUSES,
    BookingError,
    BookingInactiveError,
    BookingNotFoundError,
    BookingStatus,
    CancellationTooLateError,
    Customer,
    Slot,
    SlotTakenError,
)
from autostudio.booking.store import BookingStore, DueReminder, new_booking_id
from tests.booking.helpers import START, WED, local

ANNA = Customer(key='tg:1', name='Anna', phone='+995555000111')
BOB = Customer(key='tg:2', name='Bob', phone='+995555000222')
NOTICE = dt.timedelta(minutes=120)


def slot(start: dt.datetime, minutes: int = 45, staff: str = 'giorgi', service: str = 'haircut') -> Slot:
    return Slot(service_id=service, staff_id=staff, start=start, end=start + dt.timedelta(minutes=minutes))


def create(store: BookingStore, at: dt.datetime, who: Customer = ANNA, now: dt.datetime = START, **kw):
    return store.create(slot(at, **kw), customer=who, source='buttons', language='en', now=now, buffer=dt.timedelta(0))


@pytest.fixture
def store(tmp_path) -> BookingStore:
    return BookingStore(tmp_path / 'booking.sqlite')


def test__create__roundtrip__confirmed_with_short_id(store):
    booking = create(store, local(WED, 12))
    assert re.fullmatch(r'[A-HJ-NP-Z2-9]{6}', booking.id)
    assert store.get(booking.id) == booking
    assert booking.status == BookingStatus.CONFIRMED and booking.active
    assert store.get('NOPE00') is None


def test__create__overlap_same_staff__slot_taken(store):
    create(store, local(WED, 12))
    with pytest.raises(SlotTakenError):
        create(store, local(WED, 12, 30), who=BOB)
    create(store, local(WED, 12, 30), who=BOB, staff='nika')  # другой мастер — можно
    create(store, local(WED, 12, 45), who=BOB)  # стык без паузы — можно


def test__create__buffer__respected_in_transaction(store):
    create(store, local(WED, 12))
    with pytest.raises(SlotTakenError):
        store.create(
            slot(local(WED, 12, 45)),
            customer=BOB,
            source='ai',
            language='ru',
            now=START,
            buffer=dt.timedelta(minutes=15),
        )


def test__create__without_phone__booking_error(store):
    with pytest.raises(BookingError, match='имя и телефон'):
        create(store, local(WED, 12), who=Customer(key='tg:3', name='X'))


def test__create__concurrent_same_slot__exactly_one_wins(tmp_path):
    path = tmp_path / 'race.sqlite'
    BookingStore(path)
    for round_ in range(5):
        start = local(WED, 10) + dt.timedelta(minutes=45 * round_)
        barrier = threading.Barrier(4)
        results: list[str] = []

        def attempt(index: int, start=start, barrier=barrier, results=results) -> None:
            own = BookingStore(path)  # отдельное соединение, как у второго процесса
            barrier.wait()
            try:
                create(own, start, who=Customer(key=f'tg:{index}', name='N', phone='+995555000000'))
                results.append('ok')
            except SlotTakenError:
                results.append('taken')

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == ['ok', 'taken', 'taken', 'taken']
    assert len(BookingStore(path).overlapping(local(WED, 0), local(WED, 23))) == 5


def test__cancel__customer_inside_window__refused_admin_allowed(store):
    booking = create(store, local(WED, 12))
    late = local(WED, 11)  # за час до визита, политика — 2 часа
    with pytest.raises(CancellationTooLateError):
        store.cancel(booking.id, now=late, by_admin=False, customer=ANNA.key, notice=NOTICE)
    assert store.get(booking.id).active
    cancelled = store.cancel(booking.id, now=late, by_admin=True, notice=NOTICE)
    assert cancelled.status == BookingStatus.CANCELLED
    assert store.get(booking.id).status == BookingStatus.CANCELLED


def test__cancel__customer_outside_window__ok_twice_inactive(store):
    booking = create(store, local(WED, 12))
    store.cancel(booking.id, now=START, by_admin=False, customer=ANNA.key, notice=NOTICE)
    with pytest.raises(BookingInactiveError):
        store.cancel(booking.id, now=START, by_admin=False, customer=ANNA.key, notice=NOTICE)


def test__cancel__someone_elses_booking__not_found(store):
    booking = create(store, local(WED, 12))
    with pytest.raises(BookingNotFoundError):
        store.cancel(booking.id, now=START, by_admin=False, customer=BOB.key, notice=NOTICE)
    with pytest.raises(BookingNotFoundError):
        store.cancel('ZZZZZZ', now=START, by_admin=True)
    assert store.get(booking.id).active


def test__reschedule__moves_keeps_id_and_resets_reminders(store):
    booking = create(store, local(WED, 12))
    due = DueReminder(booking=booking, minutes_before=120, covers=(120,))
    assert store.claim_reminder(due, START)
    later = START + dt.timedelta(hours=1)
    old, new = store.reschedule(
        booking.id, slot(local(WED, 12, 30)), now=later, by_admin=False, customer=ANNA.key, notice=NOTICE
    )
    assert old.start == local(WED, 12) and new.start == local(WED, 12, 30) and new.id == booking.id
    assert store.get(booking.id).scheduled_at == later
    assert store.claim_reminder(DueReminder(booking=new, minutes_before=120, covers=(120,)), later)


def test__reschedule__taken_other_service_or_late__errors(store):
    booking = create(store, local(WED, 12))
    create(store, local(WED, 15), who=BOB)
    with pytest.raises(SlotTakenError):
        store.reschedule(booking.id, slot(local(WED, 15)), now=START, by_admin=True)
    with pytest.raises(BookingError, match='не услугу'):
        store.reschedule(booking.id, slot(local(WED, 16), service='beard'), now=START, by_admin=True)
    with pytest.raises(CancellationTooLateError):
        store.reschedule(
            booking.id, slot(local(WED, 16)), now=local(WED, 11), by_admin=False, customer=ANNA.key, notice=NOTICE
        )


def test__due_reminders__booked_after_reminder_moment__skipped(store):
    # Записались в 22:40 на завтра 17:00: «за сутки» уже прошло — только «за 2 часа».
    booked_at = local(WED - dt.timedelta(days=1), 22, 40)
    booking = create(store, local(WED, 17), now=booked_at)
    assert store.due_reminders(local(WED, 9), [1440, 120]) == []
    due = store.due_reminders(local(WED, 15, 1), [1440, 120])
    assert [(d.booking.id, d.minutes_before, d.covers) for d in due] == [(booking.id, 120, (120,))]


def test__due_reminders__downtime__one_reminder_covers_both(store):
    booking = create(store, local(WED, 17), now=START)  # за 28 часов до визита
    due = store.due_reminders(local(WED, 16), [1440, 120])
    assert [(d.booking.id, d.minutes_before, d.covers) for d in due] == [(booking.id, 120, (120, 1440))]
    assert store.due_reminders(local(WED, 17, 30), [1440, 120]) == []  # визит уже начался
    assert store.due_reminders(local(WED, 16), []) == []


def test__claim_reminder__exactly_once_across_restart(tmp_path):
    path = tmp_path / 'booking.sqlite'
    first = BookingStore(path)
    create(first, local(WED, 17))
    now = local(WED, 15, 5)
    [due] = first.due_reminders(now, [120])
    assert first.claim_reminder(due, now)
    assert not first.claim_reminder(due, now)
    restarted = BookingStore(path)
    assert restarted.due_reminders(now, [120]) == []
    assert not restarted.claim_reminder(due, now)


def test__claim_reminder__cancelled_booking__false_and_release_allows_retry(store):
    booking = create(store, local(WED, 17))
    now = local(WED, 15, 5)
    [due] = store.due_reminders(now, [120])
    assert store.claim_reminder(due, now)
    store.release_reminder(due)
    assert store.due_reminders(now, [120]) != []
    store.cancel(booking.id, now=now, by_admin=True)
    assert not store.claim_reminder(due, now)


def test__queries__overlapping_upcoming_for_customer(store):
    a = create(store, local(WED, 10))
    b = create(store, local(WED, 15), who=BOB)
    cancelled = create(store, local(WED, 17))
    store.cancel(cancelled.id, now=START, by_admin=True)
    assert [x.id for x in store.overlapping(local(WED, 10, 30), local(WED, 16))] == [a.id, b.id]
    assert [x.id for x in store.upcoming(local(WED, 10, 30), local(WED, 23))] == [b.id]
    assert [x.id for x in store.for_customer(ANNA.key, START)] == [a.id]
    assert store.for_customer(ANNA.key, local(WED, 11)) == []


def test__customers__upsert_keeps_known_fields(store):
    assert store.customer('tg:9') is None
    store.save_customer(Customer(key='tg:9', language='ru'), START)
    store.save_customer(Customer(key='tg:9', name='Олег', phone='+995555'), START)
    store.save_customer(Customer(key='tg:9', language='en'), START)
    assert store.customer('tg:9') == Customer(key='tg:9', name='Олег', phone='+995555', language='en')


def test__readonly__missing_file__empty_and_no_file_created(tmp_path):
    path = tmp_path / 'absent.sqlite'
    ro = BookingStore(path, readonly=True)
    assert ro.upcoming(START, START + dt.timedelta(days=1)) == []
    assert ro.customer('tg:1') is None
    assert not path.exists()
    with pytest.raises(BookingError, match='только для чтения'):
        ro.cancel('X', now=START, by_admin=True)


def test__readonly__existing_file__reads(tmp_path):
    path = tmp_path / 'b.sqlite'
    booking = create(BookingStore(path), local(WED, 12))
    ro = BookingStore(path, readonly=True)
    assert ro.get(booking.id) == booking and ro.path == path


def test__naive_datetime__rejected(store):
    with pytest.raises(ValueError, match='часовым поясом'):
        store.upcoming(dt.datetime(2026, 1, 1), START)  # noqa: DTZ001


def test__corrupted_row__booking_error(store):
    booking = create(store, local(WED, 12))
    with store._connect() as conn:  # прямой доступ — имитация повреждённой базы
        conn.execute("UPDATE bookings SET name = x'00' WHERE id = ?", (booking.id,))
    with pytest.raises(BookingError, match='повреждённое'):
        store.get(booking.id)


def test__sql_literals__match_active_statuses():
    literal = "('pending', 'confirmed')"
    assert {status.value for status in ACTIVE_STATUSES} == {'pending', 'confirmed'}
    for name in ('_CLASH', '_OVERLAPPING', '_UPCOMING', '_FOR_CUSTOMER', '_REMINDER_CANDIDATES'):
        assert literal in vars(store_module)[name]


def test__new_booking_id__alphabet_without_ambiguous_chars():
    ids = {new_booking_id() for _ in range(200)}
    assert len(ids) == 200
    assert not any(set(i) & set('IO01') for i in ids)

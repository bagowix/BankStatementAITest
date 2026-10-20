import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from autostudio.booking import callbacks as cb
from autostudio.booking.calsync import CalendarAvailability, booking_key, event_draft
from autostudio.booking.domain import Customer
from autostudio.booking.gcal import FakeCalendar
from tests.booking.helpers import TBILISI, WED, App, all_buttons, local, make_config

SALON = 'salon@group.calendar.google.com'
GIORGI = 'giorgi@example.test'
ANNA = Customer(key='tg:1', name='Anna Beridze', phone='+995555000111')


def calendar_app(tmp_path, fake: FakeCalendar, **section) -> App:
    calendar = {'calendar_id': SALON, 'staff': {'giorgi': GIORGI}, 'cache_ttl_s': 60, **section}
    return App(tmp_path, make_config(calendar=calendar), calendar=fake)


def times(slots) -> list[str]:
    return [f'{slot.start.astimezone(ZoneInfo(TBILISI)):%H:%M}' for slot in slots]


def giorgi_busy() -> FakeCalendar:
    # Личное дело Гиорги в среду 15:00–16:00 по Тбилиси.
    return FakeCalendar({GIORGI: [(local(WED, 15), local(WED, 16))]})


async def book(app: App, staff: str, start: dt.datetime, customer: Customer = ANNA):
    booking = await app.service.book(
        service_id='haircut',
        staff_id=staff,
        start=start,
        customer=customer,
        language='en',
        source='buttons',
        now=app.clock(),
    )
    await app.service.drain()
    return booking


async def test__busy__personal_appointment__hidden_like_a_booking_other_staff_free(tmp_path):
    app = calendar_app(tmp_path, giorgi_busy())
    await app.service.refresh_busy(app.clock())
    giorgi = times(app.service.free_slots('haircut', WED, app.clock(), staff_id='giorgi'))
    assert '13:15' in giorgi and '16:00' in giorgi
    assert not {'14:15', '15:00', '15:15', '15:30', '15:45'} & set(giorgi)
    assert '15:00' in times(app.service.free_slots('haircut', WED, app.clock(), staff_id='nika'))


async def test__busy__engine_time_picker__refreshes_before_answer(tmp_path):
    fake = giorgi_busy()
    app = calendar_app(tmp_path, fake)
    replies = await app.send('tg:1', data=cb.day_data('haircut', 'giorgi', WED))
    labels = [button.label for button in all_buttons(replies)]
    assert '13:15' in labels and '15:00' not in labels and '16:00' in labels
    assert fake.calls == ['freeBusy']


async def test__busy__cache_ttl__one_request_per_ttl(tmp_path):
    fake = giorgi_busy()
    app = calendar_app(tmp_path, fake)
    for _ in range(3):
        await app.service.refresh_busy(app.clock())
    assert fake.calls == ['freeBusy']
    app.clock.advance(dt.timedelta(seconds=61))
    await app.service.refresh_busy(app.clock())
    assert fake.calls == ['freeBusy', 'freeBusy']


async def test__busy__google_down__db_only_one_alert_then_recovery(tmp_path):
    fake = giorgi_busy()
    fake.fail = True
    app = calendar_app(tmp_path, fake)
    await app.service.refresh_busy(app.clock())
    assert app.service.busy == {}
    assert '15:00' in times(app.service.free_slots('haircut', WED, app.clock(), staff_id='giorgi'))
    await app.service.refresh_busy(app.clock())  # пауза после сбоя: Google не дёргаем
    assert fake.calls == ['freeBusy']
    app.clock.advance(dt.timedelta(seconds=61))
    await app.service.refresh_busy(app.clock())
    assert len(app.operator.texts) == 1 and 'двойная запись' in app.operator.texts[0]
    fake.fail = False
    app.clock.advance(dt.timedelta(seconds=61))
    await app.service.refresh_busy(app.clock())
    assert app.service.busy and app.operator.texts[-1].startswith('✅') and len(app.operator.texts) == 2


async def test__busy__calendar_not_shared__alert_and_other_calendar_still_counted(tmp_path):
    fake = giorgi_busy()
    fake.missing.add(SALON)
    app = calendar_app(tmp_path, fake)
    await app.service.refresh_busy(app.clock())
    assert set(app.service.busy) == {'giorgi'}
    assert 'не расшарен' in app.operator.texts[0] and SALON in app.operator.texts[0]


async def test__busy__shared_salon_calendar__own_booking_event_does_not_block_other_staff(tmp_path):
    # Один календарь на салон: событие записи к Гиорги не должно закрыть это время Нике.
    fake = FakeCalendar()
    app = calendar_app(tmp_path, fake, staff={})
    await book(app, 'giorgi', local(WED, 12))
    assert len(fake.events[SALON]) == 1
    app.clock.advance(dt.timedelta(seconds=61))
    await app.service.refresh_busy(app.clock())
    assert '12:00' in times(app.service.free_slots('haircut', WED, app.clock(), staff_id='nika'))
    assert '12:00' not in times(app.service.free_slots('haircut', WED, app.clock(), staff_id='giorgi'))


async def test__sync__book_reschedule_cancel__insert_patch_delete(tmp_path):
    fake = FakeCalendar()
    app = calendar_app(tmp_path, fake)
    booking = await book(app, 'giorgi', local(WED, 12))
    [(event_id, draft)] = fake.events[GIORGI].items()
    assert draft.summary == 'Haircut — Anna'
    assert draft.description == f'Booking #{booking.id} · Giorgi\n+995 555 00 01 11'
    assert draft.booking_key == f'test-barbers/{booking.id}' and draft.timezone == 'Asia/Tbilisi'
    await app.service.reschedule(booking.id, local(WED, 16), now=app.clock(), by_admin=True)
    await app.service.drain()
    assert list(fake.events[GIORGI]) == [event_id] and fake.events[GIORGI][event_id].start == local(WED, 16)
    await app.service.cancel(booking.id, now=app.clock(), by_admin=True)
    await app.service.drain()
    assert fake.events[GIORGI] == {} and app.store.calendar_pending() == 0
    assert fake.calls == ['find', 'insert', 'patch', 'delete']


async def test__sync__google_down__retried_later_with_one_alert_and_recovery(tmp_path):
    fake = FakeCalendar()
    fake.fail = True
    app = calendar_app(tmp_path, fake)
    await book(app, 'giorgi', local(WED, 12))
    assert app.store.calendar_pending() == 1 and 'не пишутся' in app.operator.texts[0]
    fake.fail = False
    assert await app.calendar_sync.run_due() == 0  # повтор — не раньше паузы
    app.clock.advance(dt.timedelta(minutes=2))
    assert await app.calendar_sync.run_due() == 1
    assert len(fake.events[GIORGI]) == 1 and app.operator.texts[-1].startswith('✅')


async def test__sync__response_lost_after_insert__found_by_property_not_duplicated(tmp_path):
    fake = FakeCalendar()
    app = calendar_app(tmp_path, fake, write_events=True)
    booking = app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 12))
    # Прошлая попытка создала событие, но не успела сохранить его id (обрыв, перезапуск).
    with app.store._write() as conn:
        conn.execute(
            'INSERT INTO calendar_sync VALUES (?, ?, NULL, 1, 1, 0, ?)',
            (booking.id, GIORGI, '2026-10-20T00:00:00+00:00'),
        )
    await fake.insert_event(GIORGI, event_draft(app.config, app.texts, booking))
    assert await app.calendar_sync.run_due() == 1
    assert len(fake.events[GIORGI]) == 1 and fake.calls[-2:] == ['find', 'patch']


async def test__sync__event_deleted_by_hand__recreated_on_next_change(tmp_path):
    fake = FakeCalendar()
    app = calendar_app(tmp_path, fake)
    booking = await book(app, 'giorgi', local(WED, 12))
    fake.events[GIORGI].clear()  # владелец удалил событие в календаре руками
    await app.service.reschedule(booking.id, local(WED, 16), now=app.clock(), by_admin=True)
    await app.service.drain()
    [draft] = fake.events[GIORGI].values()
    assert draft.start == local(WED, 16)


async def test__sync__changed_during_request__newer_change_still_pending(tmp_path):
    fake = FakeCalendar()
    app = calendar_app(tmp_path, fake)
    booking = app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 12))
    with app.store._write() as conn:
        conn.execute(
            'INSERT INTO calendar_sync VALUES (?, ?, NULL, 1, 1, 0, ?)',
            (booking.id, GIORGI, '2026-10-20T00:00:00+00:00'),
        )
    [task] = app.store.calendar_due(app.clock(), 10)
    await app.service.reschedule(booking.id, local(WED, 16), now=app.clock(), by_admin=True)
    await app.calendar_sync._reconcile(task)  # проход по старой версии записи
    assert app.store.calendar_pending() == 1
    await app.calendar_sync.run_due()
    assert app.store.calendar_pending() == 0
    [draft] = fake.events[GIORGI].values()
    assert draft.start == local(WED, 16)


async def test__sync__write_events_off__no_marks_busy_still_read(tmp_path):
    fake = giorgi_busy()
    app = calendar_app(tmp_path, fake, write_events=False)
    await book(app, 'nika', local(WED, 12))
    assert app.store.calendar_pending() == 0 and fake.events == {}
    await app.service.refresh_busy(app.clock())
    assert 'giorgi' in app.service.busy


async def test__sync__cancelled_without_event__nothing_to_delete(tmp_path):
    fake = FakeCalendar()
    fake.fail = True
    app = calendar_app(tmp_path, fake)
    booking = await book(app, 'giorgi', local(WED, 12))
    fake.fail = False
    await app.service.cancel(booking.id, now=app.clock(), by_admin=True)
    await app.service.drain()
    assert fake.calls[-1] == 'find' and app.store.calendar_pending() == 0


def test__availability__without_section__refused(tmp_path):
    app = App(tmp_path, make_config())
    with pytest.raises(ValueError, match=r'booking\.calendar'):
        CalendarAvailability(config=app.config, api=FakeCalendar(), alarm=app.alarm, clock=app.clock)


def test__booking_key__client_and_code():
    assert booking_key(make_config(), 'K7Q2MX') == 'test-barbers/K7Q2MX'


async def test__busy__outage_after_success__stale_snapshot_not_used(tmp_path):
    fake = giorgi_busy()
    app = calendar_app(tmp_path, fake)
    await app.service.refresh_busy(app.clock())
    assert 'giorgi' in app.service.busy
    fake.fail = True
    app.clock.advance(dt.timedelta(seconds=61))
    await app.service.refresh_busy(app.clock())
    assert app.service.busy == {}  # только база — как и сказано оператору
    assert 'только по базе' in app.operator.texts[0]

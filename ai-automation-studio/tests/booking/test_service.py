import datetime as dt

import pytest

from autostudio.booking.domain import BookingNotFoundError, Customer, SlotTakenError
from autostudio.booking.service import BookingService
from autostudio.errors import SinkError
from tests.booking.helpers import START, WED, App, local

ANNA = Customer(key='tg:1', name='Anna', phone='+995555000111')


class DownSink:
    async def append(self, tab, header, rows):
        raise SinkError('quota exceeded')

    async def replace(self, tab, header, rows):
        raise SinkError('quota exceeded')


async def test__book__sheet_down__booking_still_created_and_logged(app: App, caplog):
    service = BookingService(config=app.config, store=app.store, events=app.events, sink=DownSink(), texts=app.texts)
    booking = await service.book(
        service_id='haircut', staff_id=None, start=local(WED, 12), customer=ANNA, language='en', source='ai', now=START
    )
    assert app.store.get(booking.id) is not None
    assert 'не попала в таблицу' in caplog.text
    assert app.events_of('booking.created')[0].payload['lead_days'] == '1'


async def test__book__off_grid_or_taken__slot_taken(app: App):
    with pytest.raises(SlotTakenError):
        await app.service.book(
            service_id='haircut',
            staff_id='giorgi',
            start=local(WED, 12, 5),
            customer=ANNA,
            language='en',
            source='buttons',
            now=START,
        )
    app.book_directly('tg:9', 'haircut', 'giorgi', local(WED, 12))
    with pytest.raises(SlotTakenError):
        await app.service.book(
            service_id='haircut', staff_id='giorgi', start=local(WED, 12), customer=ANNA, language='en',
            source='buttons', now=START,
        )  # fmt: skip


async def test__book__race_lost_with_any_staff__second_staff_tried(app: App, monkeypatch):
    original = app.store.create
    calls: list[str] = []

    def racing(slot, **kw):
        calls.append(slot.staff_id)
        if len(calls) == 1:
            # Пока мы проверяли слот, другой процесс занял этого мастера.
            rival = Customer(key='tg:9', name='Z', phone='+995555000999')
            original(slot, **{**kw, 'customer': rival})
        return original(slot, **kw)

    monkeypatch.setattr(app.store, 'create', racing)
    booking = await app.service.book(
        service_id='haircut', staff_id=None, start=local(WED, 12), customer=ANNA, language='en', source='buttons',
        now=START,
    )  # fmt: skip
    assert calls == ['giorgi', 'nika'] and booking.staff_id == 'nika'


async def test__reschedule__foreign_or_missing__not_found(app: App):
    booking = app.book_directly('tg:9', 'haircut', 'giorgi', local(WED, 12))
    with pytest.raises(BookingNotFoundError):
        await app.service.reschedule(booking.id, local(WED, 15), now=START, by_admin=False, customer='tg:1')
    with pytest.raises(BookingNotFoundError):
        await app.service.reschedule('NOPE22', local(WED, 15), now=START, by_admin=True)
    with pytest.raises(SlotTakenError):
        await app.service.reschedule(booking.id, local(WED, 14), now=START, by_admin=True)  # обед


async def test__sheet_row__owner_language_and_removed_service(app: App):
    booking = app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 12), name='Anna')
    row = app.service.sheet_row('action_created', booking, START)
    assert row == ['2026-10-20 12:30', 'created', booking.id, '2026-10-21', '12:00', 'Haircut', 'Giorgi', 'Anna',
                   '+995555000111', '40 GEL', 'buttons']  # fmt: skip
    from msgspec.structs import replace

    gone = app.service.sheet_row('action_cancelled', replace(booking, service_id='removed'), START)
    assert gone[5] == 'removed' and gone[9] == ''
    assert app.service.notice == dt.timedelta(minutes=120)

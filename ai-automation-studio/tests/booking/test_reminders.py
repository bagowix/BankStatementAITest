import asyncio
import datetime as dt

import httpx

from autostudio.booking.messages import DeliveryError, RecipientGoneError
from autostudio.booking.reminders import ReminderLoop, refresh_schedule, reminder_message, send_due_reminders
from autostudio.booking.store import BookingStore
from autostudio.errors import SinkError
from autostudio.runlog import Heartbeat
from tests.booking.helpers import START, WED, App, Recorder, local


class FailingSink:
    def __init__(self) -> None:
        self.calls = 0

    async def append(self, tab, header, rows):
        raise SinkError('PERMISSION_DENIED')

    async def replace(self, tab, header, rows):
        self.calls += 1
        raise SinkError('PERMISSION_DENIED')


async def send(app: App, sender, store: BookingStore | None = None, now: dt.datetime | None = None) -> int:
    return await send_due_reminders(
        store or app.store, sender, now or app.clock(), config=app.config, texts=app.texts, events=app.events
    )


async def test__send_due_reminders__repeat_and_restart__sent_exactly_once(app, tmp_path):
    booking = app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 17))
    sender = Recorder()
    app.clock.set(local(WED, 15, 1))
    assert await send(app, sender) == 1
    assert await send(app, sender) == 0
    restarted = BookingStore(app.store.path)
    assert await send(app, sender, restarted) == 0
    [(chat, message)] = sender.sent
    assert chat == 'tg:1' and message.text.startswith('⏰ Reminder: Haircut Wed 21 Oct, 17:00, with Giorgi')
    # За 2 часа при политике «не позже чем за 2 часа» кнопка «Изменить» привела бы к «уже поздно».
    assert message.buttons == [] and 'call us: +995 555 00 00 00' in message.text
    assert booking.id in message.text
    [event] = app.events_of('booking.reminded')
    assert event.payload == {'minutes_before': '120', 'service': 'haircut'}


async def test__send_due_reminders__russian_customer__russian_text(app):
    booking = app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 17))
    day_before = local(WED, 17) - dt.timedelta(days=1)
    message = reminder_message(booking, app.config, app.texts, day_before)
    assert message.text.startswith('⏰ Reminder')
    assert message.buttons[0][0].data == f'bk:{booking.id}'
    from msgspec.structs import replace

    ru = reminder_message(replace(booking, language='ru'), app.config, app.texts, day_before)
    assert ru.text.startswith('⏰ Напоминаем: Стрижка ср, 21 окт, 17:00')


async def test__send_due_reminders__temporary_failure__released_and_retried(app):
    app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 17))
    app.clock.set(local(WED, 15, 1))
    assert await send(app, Recorder(fail=DeliveryError('timeout'))) == 0
    ok = Recorder()
    assert await send(app, ok) == 1 and len(ok.sent) == 1


async def test__send_due_reminders__customer_blocked_bot__not_retried(app):
    app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 17))
    app.clock.set(local(WED, 15, 1))
    assert await send(app, Recorder(fail=RecipientGoneError('Forbidden'))) == 0
    ok = Recorder()
    assert await send(app, ok) == 0 and ok.sent == []
    assert app.events_of('booking.reminded') == []


async def test__send_due_reminders__cancelled_booking__nothing_sent(app):
    booking = app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 17))
    app.store.cancel(booking.id, now=START, by_admin=True)
    app.clock.set(local(WED, 15, 1))
    assert await send(app, Recorder()) == 0


async def test__refresh_schedule__twice__tab_replaced(app):
    app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 17), name='Anna')
    app.book_directly('tg:2', 'beard', 'nika', local(WED, 12), name='Bob')
    rows = await refresh_schedule(app.store, app.sink, app.clock(), config=app.config, texts=app.texts)
    assert rows == 2
    content = app.sink.path_for('Schedule').read_text(encoding='utf-8').splitlines()
    assert content[0] == 'Date,Start,End,Staff,Service,Customer,Phone,Booking'
    assert content[1].startswith('2026-10-21,12:00,12:30,Nika,Beard trim,Bob')
    assert await refresh_schedule(app.store, app.sink, app.clock(), config=app.config, texts=app.texts) == 2
    assert len(app.sink.path_for('Schedule').read_text(encoding='utf-8').splitlines()) == 3


def make_loop(app: App, sender, sink=None, operator=None, http=None) -> ReminderLoop:
    return ReminderLoop(
        config=app.config,
        store=app.store,
        sender=sender,
        sink=sink or app.sink,
        events=app.events,
        texts=app.texts,
        heartbeat=Heartbeat(http, None),
        operator=operator,
        clock=app.clock,
        tick=dt.timedelta(milliseconds=10),
    )


async def test__reminder_loop__ticks__schedule_hourly_with_run_events(app):
    app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 17))
    loop = make_loop(app, Recorder())
    await loop.tick()
    await loop.tick()
    assert len(app.events_of('system.run_ok')) == 1
    app.clock.advance(dt.timedelta(hours=1))
    await loop.tick()
    assert len(app.events_of('system.run_ok')) == 2
    app.clock.set(local(WED, 15, 1))
    assert await loop.tick() == 1


async def test__reminder_loop__sheet_down__run_failed_and_operator_alerted(app):
    operator = Recorder()
    sink = FailingSink()
    async with httpx.AsyncClient() as http:
        loop = make_loop(app, Recorder(), sink=sink, operator=operator, http=http)
        await loop.tick()
    assert sink.calls == 1
    [failed] = app.events_of('system.run_failed')
    assert 'PERMISSION_DENIED' in failed.payload['error']
    assert operator.texts and 'Test Barbers / booking' in operator.texts[0]


async def test__reminder_loop__store_error__logged_loop_continues(app, monkeypatch, caplog):
    from autostudio.booking.domain import BookingError

    def boom(now, offsets):
        raise BookingError('db locked')

    monkeypatch.setattr(app.store, 'due_reminders', boom)
    loop = make_loop(app, Recorder())
    assert await loop.tick() == 0
    assert 'db locked' in caplog.text


async def test__reminder_loop__run__stops_on_event_and_survives_bugs(app, monkeypatch, caplog):
    loop = make_loop(app, Recorder())
    calls = 0

    async def flaky() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('bug')
        if calls >= 3:
            stop.set()
        return 0

    monkeypatch.setattr(loop, 'tick', flaky)
    stop = asyncio.Event()
    await asyncio.wait_for(loop.run(stop), timeout=5)
    assert calls >= 3 and 'цикл напоминаний упал' in caplog.text


class HangingSink:
    async def append(self, tab, header, rows):  # pragma: no cover - цикл пишет только replace
        await asyncio.Event().wait()

    async def replace(self, tab, header, rows):
        await asyncio.Event().wait()


async def test__reminder_loop__sheet_hangs__reminders_sent_refresh_timed_out(app):
    app.book_directly('tg:1', 'haircut', 'giorgi', local(WED, 17))
    app.clock.set(local(WED, 15, 1))
    sender, operator = Recorder(), Recorder()
    loop = ReminderLoop(
        config=app.config, store=app.store, sender=sender, sink=HangingSink(), events=app.events,
        texts=app.texts, heartbeat=Heartbeat(None, None), operator=operator, clock=app.clock,
        schedule_timeout=0.05,
    )  # fmt: skip
    assert await asyncio.wait_for(loop.tick(), timeout=5) == 1
    assert len(sender.sent) == 1
    [failed] = app.events_of('system.run_failed')
    assert 'не обновилась за' in failed.payload['error'] and operator.texts


class ReschedulingSender(Recorder):
    """Пока уходит первое напоминание прохода, другой покупатель переносит свою запись."""

    def __init__(self, app: App, booking_id: str, new_start: dt.datetime) -> None:
        super().__init__()
        self.app, self.booking_id, self.new_start = app, booking_id, new_start

    async def send(self, chat, message=None):
        if not self.sent:
            await self.app.service.reschedule(
                self.booking_id, self.new_start, now=self.app.clock(), by_admin=False, customer='tg:2'
            )
        await super().send(chat, message)


async def test__send_due_reminders__reschedule_during_pass__no_stale_time_new_time_later(app):
    thu = WED + dt.timedelta(days=1)
    app.book_directly('tg:1', 'haircut', 'giorgi', local(thu, 16), name='Anna')
    bob = app.book_directly('tg:2', 'haircut', 'giorgi', local(thu, 17), name='Bob')
    app.clock.set(local(WED, 17, 5))
    sender = ReschedulingSender(app, bob.id, local(thu, 19))
    assert await send(app, sender) == 1
    assert [chat for chat, _ in sender.sent] == ['tg:1']  # Боб не получил «17:00»
    app.clock.set(local(WED, 19, 5))
    assert await send(app, sender) == 1
    chat, message = sender.sent[-1]
    assert chat == 'tg:2' and 'Thu 22 Oct, 19:00' in message.text

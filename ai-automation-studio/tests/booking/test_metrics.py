import datetime as dt

from autostudio.booking.metrics import METRICS, highlights
from autostudio.events import Event
from autostudio.report import build_report, render_markdown
from tests.booking.helpers import START


def created(weekday: str, service: str, after_hours: str, name: str | None = None) -> Event:
    payload = {'weekday': weekday, 'service': service, 'after_hours': after_hours}
    if name:
        payload['service_name'] = name
    return Event(at=START, package='booking', kind='booking.created', payload=payload)


def other(kind: str) -> Event:
    return Event(at=START, package='booking', kind=kind, payload={})


EVENTS = [
    created('fri', 'haircut', 'yes', 'Haircut'),
    created('fri', 'haircut', 'no', 'Haircut'),
    created('wed', 'beard', 'yes', 'Beard trim'),
    other('booking.question_answered'),
    other('booking.question_answered'),
    other('booking.handoff'),
]


def test__metrics__specs__honest_minutes_per_action():
    minutes = {spec.kind: spec.minutes_saved for spec in METRICS}
    assert minutes == {
        'booking.created': 5,
        'booking.cancelled': 2,
        'booking.rescheduled': 4,
        'booking.reminded': 1,
        'booking.question_answered': 3,
        'booking.handoff': 0,
        'booking.handoff_throttled': 0,
        'booking.walk_in': 0,
        'booking.link_opened': 0,
        'booking.whatsapp_message': 0,
    }
    assert all(set(spec.label) == {'en', 'ru'} for spec in METRICS)


def test__highlights__english__busiest_top_after_hours_questions():
    assert highlights(EVENTS, 'en') == [
        'Busiest day: Friday — 2 of 3 bookings.',
        'Most booked: Haircut (2).',
        '67% of bookings came in while you were closed (2 of 3).',
        '2 customer questions answered automatically; 1 passed to you.',
    ]


def test__highlights__russian__plurals():
    lines = highlights(EVENTS, 'ru')
    assert lines[0] == 'Самый загруженный день — пятница: 2 из 3 записей.'
    assert lines[2] == '67% записей пришли, когда вы были закрыты (2 из 3).'
    assert lines[3] == '2 вопроса клиентов бот закрыл сам, 1 передано вам.'
    single = highlights([created('mon', 'haircut', 'no'), other('booking.question_answered')], 'ru')
    assert single[0] == 'Самый загруженный день — понедельник: 1 из 1 записи.'
    assert single[1] == 'Самая популярная услуга — haircut (1).'
    assert single[2] == '1 вопрос клиентов бот закрыл сам, 0 передано вам.'


def test__highlights__ties_and_unknown_language__deterministic_english():
    tie = [created('fri', 'a', 'no'), created('mon', 'b', 'no')]
    assert highlights(tie, 'ka')[:2] == ['Busiest day: Monday — 1 of 2 bookings.', 'Most booked: a (1).']
    assert highlights([created('mon', 'a', 'no')], 'en')[0] == 'Busiest day: Monday — 1 of 1 booking.'


def test__highlights__nothing__empty():
    assert highlights([], 'en') == []
    assert highlights([Event(at=START, package='booking', kind='booking.created', payload={})], 'en') == []


def test__report__booking_events__rendered():
    report = build_report(
        client='Demo Barbers',
        month=dt.date(2026, 10, 1),
        language='en',
        events=EVENTS,
        specs=METRICS,
        highlights=highlights(EVENTS, 'en'),
    )
    text = render_markdown(report)
    assert '| Bookings made | 3 | 0.2 h |' in text and '67% of bookings' in text


def event(event_kind: str, **payload: str) -> Event:
    return Event(package='booking', kind=event_kind, at=START, payload=payload)


def test__highlights__channels_sources_and_whatsapp_volume():
    events = [
        event('booking.created', channel='telegram', came_from='instagram'),
        event('booking.created', channel='telegram', came_from='instagram'),
        event('booking.created', channel='whatsapp', came_from='wa_ad'),
        event('booking.created', channel='telegram', came_from=''),
        event('booking.whatsapp_message', kind='service', purpose='reply'),
        event('booking.whatsapp_message', kind='template', purpose='reminder'),
    ]
    english = highlights(events, 'en')
    assert 'Bookings by channel: Telegram 3, WhatsApp 1.' in english
    assert 'Bookings from tracked links: instagram 2, wa_ad 1 (of 4).' in english
    assert 'WhatsApp: 2 messages sent, 1 of them template reminders outside the 24-hour window.' in english
    russian = highlights(events[:1] + events[4:5], 'ru')
    assert 'Записи по ссылкам с меткой: instagram 1 (из 1).' in russian
    assert 'WhatsApp: отправлено 1 сообщение, из них 0 — напоминания шаблоном вне окна 24 ч.' in russian
    # Один канал — строки «по каналам» нет: она ничего не говорит.
    assert not any(line.startswith('Записи по каналам') for line in russian)

import datetime as dt
import json

import httpx
import msgspec
import pytest
import respx

from autostudio.booking.gcal import (
    BOOKING_PROPERTY,
    CALENDAR_API,
    FREEBUSY_MAX_CALENDARS,
    SCOPES,
    CalendarError,
    EventDraft,
    EventGoneError,
    FakeCalendar,
    GoogleCalendar,
    event_body,
    service_account_email,
)

TOKEN = 'ya29.secret-access-token'
CAL = 'ru.ge#holiday@group.v.calendar.google.com'
CAL_PATH = 'ru.ge%23holiday%40group.v.calendar.google.com'
START = dt.datetime(2026, 10, 21, 6, 0, tzinfo=dt.UTC)
END = START + dt.timedelta(days=1)


class StaticTokens:
    def __init__(self) -> None:
        self.calls = 0

    async def token(self) -> str:
        self.calls += 1
        return TOKEN


DRAFT = EventDraft(
    summary='Haircut — Anna',
    description='Booking #K7Q2MX · Giorgi',
    start=dt.datetime(2026, 10, 21, 8, 0, tzinfo=dt.UTC),
    end=dt.datetime(2026, 10, 21, 8, 45, tzinfo=dt.UTC),
    timezone='Asia/Tbilisi',
    booking_key='test-barbers/K7Q2MX',
)


def calendar(http: httpx.AsyncClient) -> GoogleCalendar:
    return GoogleCalendar(http, StaticTokens())


@respx.mock
async def test__free_busy__busy_and_not_shared__split_by_calendar_utc():
    route = respx.post(f'{CALENDAR_API}/freeBusy').mock(
        return_value=httpx.Response(
            200,
            json={
                'kind': 'calendar#freeBusy',
                'calendars': {
                    'giorgi@x': {'busy': [{'start': '2026-10-21T15:00:00+04:00', 'end': '2026-10-21T16:00:00+04:00'}]},
                    'nika@x': {'errors': [{'domain': 'global', 'reason': 'notFound'}], 'busy': []},
                },
            },
        )
    )
    async with httpx.AsyncClient() as http:
        answer = await calendar(http).free_busy(['giorgi@x', 'nika@x', 'lost@x'], START, END)
    assert answer.busy == {
        'giorgi@x': [(dt.datetime(2026, 10, 21, 11, tzinfo=dt.UTC), dt.datetime(2026, 10, 21, 12, tzinfo=dt.UTC))]
    }
    assert answer.errors == {'nika@x': 'notFound', 'lost@x': 'missing'}
    body = json.loads(route.calls[0].request.content)
    assert body == {
        'timeMin': '2026-10-21T06:00:00Z',
        'timeMax': '2026-10-22T06:00:00Z',
        'items': [{'id': 'giorgi@x'}, {'id': 'nika@x'}, {'id': 'lost@x'}],
    }
    assert route.calls[0].request.headers['Authorization'] == f'Bearer {TOKEN}'


@respx.mock
async def test__free_busy__more_than_50_calendars__chunked_requests():
    route = respx.post(f'{CALENDAR_API}/freeBusy').mock(return_value=httpx.Response(200, json={'calendars': {}}))
    many = [f'c{i}@x' for i in range(FREEBUSY_MAX_CALENDARS + 3)]
    async with httpx.AsyncClient() as http:
        answer = await calendar(http).free_busy(many, START, END)
    assert route.call_count == 2 and len(answer.errors) == len(many)
    assert len(json.loads(route.calls[1].request.content)['items']) == 3


@respx.mock
@pytest.mark.parametrize(
    ('response', 'message'),
    [
        (
            httpx.Response(403, json={'error': {'code': 403, 'errors': [{'reason': 'rateLimitExceeded'}]}}),
            'rateLimitExceeded',
        ),
        (httpx.Response(401, json={'error': {'code': 401, 'status': 'UNAUTHENTICATED'}}), 'UNAUTHENTICATED'),
        (httpx.Response(500, text='oops'), 'HTTP 500'),
        (httpx.Response(502, json={'other': 1}), 'HTTP 502'),
        (httpx.Response(404, json={'error': {'code': 404}}), 'HTTP 404'),
        (httpx.Response(200, text='not json'), 'не разобран'),
    ],
)
async def test__free_busy__google_errors__calendar_error_without_token(response, message):
    respx.post(f'{CALENDAR_API}/freeBusy').mock(return_value=response)
    async with httpx.AsyncClient() as http:
        with pytest.raises(CalendarError, match=message) as caught:
            await calendar(http).free_busy(['a@x'], START, END)
    assert TOKEN not in str(caught.value)


@respx.mock
async def test__free_busy__network_error__type_only():
    respx.post(f'{CALENDAR_API}/freeBusy').mock(side_effect=httpx.ConnectTimeout(f'Bearer {TOKEN}'))
    async with httpx.AsyncClient() as http:
        with pytest.raises(CalendarError) as caught:
            await calendar(http).free_busy(['a@x'], START, END)
    assert str(caught.value) == 'Google Calendar недоступен: ConnectTimeout'


@respx.mock
async def test__events__find_insert_patch_delete__escaped_calendar_path_and_private_property():
    base = f'{CALENDAR_API}/calendars/{CAL_PATH}/events'
    find = respx.get(base).mock(return_value=httpx.Response(200, json={'items': [{'id': 'evt1'}]}))
    insert = respx.post(base).mock(return_value=httpx.Response(200, json={'id': 'evt2', 'status': 'confirmed'}))
    patch = respx.patch(f'{base}/evt2').mock(return_value=httpx.Response(200, json={'id': 'evt2'}))
    delete = respx.delete(f'{base}/evt2').mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        api = calendar(http)
        assert await api.find_event(CAL, DRAFT.booking_key) == 'evt1'
        assert await api.insert_event(CAL, DRAFT) == 'evt2'
        await api.patch_event(CAL, 'evt2', DRAFT)
        await api.delete_event(CAL, 'evt2')
    assert find.calls[0].request.url.params['privateExtendedProperty'] == f'{BOOKING_PROPERTY}=test-barbers/K7Q2MX'
    body = json.loads(insert.calls[0].request.content)
    assert body == event_body(DRAFT)
    assert 'attendees' not in body  # сервисный аккаунт без delegation гостей не приглашает
    # Патч несёт `confirmed`: событие, удалённое руками (у Google оно `cancelled`), восстанавливается.
    assert json.loads(patch.calls[0].request.content)['status'] == 'confirmed'
    assert body['start'] == {'dateTime': '2026-10-21T08:00:00+00:00', 'timeZone': 'Asia/Tbilisi'}
    assert body['extendedProperties'] == {'private': {BOOKING_PROPERTY: 'test-barbers/K7Q2MX'}}
    assert patch.called and delete.called


@respx.mock
async def test__events__missing_event_or_calendar__gone_or_calendar_error():
    base = f'{CALENDAR_API}/calendars/a%40x/events'
    respx.get(base).mock(return_value=httpx.Response(200, json={}))
    respx.patch(f'{base}/gone').mock(return_value=httpx.Response(404, json={'error': {'code': 404}}))
    respx.delete(f'{base}/gone').mock(return_value=httpx.Response(410, json={'error': {'code': 410}}))
    async with httpx.AsyncClient() as http:
        api = calendar(http)
        assert await api.find_event('a@x', 'k') is None
        with pytest.raises(EventGoneError):
            await api.patch_event('a@x', 'gone', DRAFT)
        await api.delete_event('a@x', 'gone')  # уже удалено — не ошибка
    respx.get(f'{CALENDAR_API}/calendars/b%40x/events').mock(return_value=httpx.Response(404))
    respx.post(f'{CALENDAR_API}/calendars/b%40x/events').mock(return_value=httpx.Response(404))
    respx.get(f'{CALENDAR_API}/calendars/c%40x/events').mock(return_value=httpx.Response(200, text='{'))
    respx.post(f'{CALENDAR_API}/calendars/c%40x/events').mock(return_value=httpx.Response(200, text='{'))
    async with httpx.AsyncClient() as http:
        api = calendar(http)
        with pytest.raises(CalendarError, match='календарь b@x'):
            await api.find_event('b@x', 'k')
        with pytest.raises(CalendarError, match='календарь b@x'):
            await api.insert_event('b@x', DRAFT)
        with pytest.raises(CalendarError, match='список событий'):
            await api.find_event('c@x', 'k')
        with pytest.raises(CalendarError, match='создание события'):
            await api.insert_event('c@x', DRAFT)


@respx.mock
async def test__patch_event__google_keeps_it_cancelled__event_gone():
    base = f'{CALENDAR_API}/calendars/a%40x/events'
    respx.patch(f'{base}/moved').mock(return_value=httpx.Response(200, json={'id': 'moved', 'status': 'cancelled'}))
    respx.patch(f'{base}/odd').mock(return_value=httpx.Response(200, text='not json'))
    async with httpx.AsyncClient() as http:
        api = calendar(http)
        with pytest.raises(EventGoneError, match='осталось удалённым'):
            await api.patch_event('a@x', 'moved', DRAFT)
        with pytest.raises(CalendarError, match='изменение события'):
            await api.patch_event('a@x', 'odd', DRAFT)


async def test__fake_calendar__own_events_busy_missing_calendars_and_failures():
    fake = FakeCalendar({'g@x': [(START, START + dt.timedelta(hours=1))]})
    fake.missing.add('n@x')
    event = await fake.insert_event('g@x', DRAFT)
    answer = await fake.free_busy(['g@x', 'n@x'], START, END)
    assert answer.busy['g@x'] == [(START, START + dt.timedelta(hours=1)), (DRAFT.start, DRAFT.end)]
    assert answer.errors == {'n@x': 'notFound'}
    assert await fake.find_event('g@x', DRAFT.booking_key) == event
    with pytest.raises(EventGoneError):
        await fake.patch_event('g@x', 'nope', DRAFT)
    await fake.delete_event('g@x', event)
    fake.fail = True
    with pytest.raises(CalendarError):
        await fake.free_busy(['g@x'], START, END)


async def test__fake_calendar__deleted_event__cancelled_like_google_patch_with_status_restores(monkeypatch):
    from autostudio.booking import gcal

    fake = FakeCalendar()
    event = await fake.insert_event('g@x', DRAFT)
    await fake.delete_event('g@x', event)
    # Как у Google: удалённое не занимает время и не находится поиском, но патч по нему — 200.
    assert (await fake.free_busy(['g@x'], START, END)).busy['g@x'] == []
    assert await fake.find_event('g@x', DRAFT.booking_key) is None
    later = msgspec.structs.replace(DRAFT, start=DRAFT.start + dt.timedelta(hours=1))
    without_status = {key: value for key, value in event_body(later).items() if key != 'status'}
    monkeypatch.setattr(gcal, 'event_body', lambda draft: without_status)
    await fake.patch_event('g@x', event, later)  # тело без `status` — событие остаётся удалённым
    assert fake.events['g@x'] == {} and fake.cancelled['g@x'][event] == later
    monkeypatch.undo()
    await fake.patch_event('g@x', event, later)
    assert fake.events['g@x'] == {event: later} and fake.cancelled['g@x'] == {}


def test__scopes__client_calendars_shared_with_service_account__events_freebusy_and_events():
    # `calendar.freebusy` — «ваши календари»; у сервисного аккаунта своих нет, календари клиента расшарены.
    assert SCOPES == (
        'https://www.googleapis.com/auth/calendar.events.freebusy',
        'https://www.googleapis.com/auth/calendar.events',
    )


def test__service_account_email__key_file__email_or_none(tmp_path):
    key = tmp_path / 'key.json'
    key.write_text('{"type": "service_account", "client_email": "bot@proj.iam.gserviceaccount.com"}')
    assert service_account_email(key) == 'bot@proj.iam.gserviceaccount.com'
    key.write_text('not json')
    assert service_account_email(key) is None
    assert service_account_email(tmp_path / 'missing.json') is None

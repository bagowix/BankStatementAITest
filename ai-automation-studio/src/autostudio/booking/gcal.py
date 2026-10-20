"""Google Calendar API v3 через REST: занятость (freeBusy) и события записей.

Почему так, а не через `google-api-python-client`: нужны пять вызовов, а не весь
discovery-клиент; httpx уже есть в зависимостях и мокается в тестах (respx). Токен
сервисного аккаунта выдаёт `gauth` (google-auth в extras `sheets`, импортируется лениво).

Права (минимальные, по справочнику API):

- `calendar.freebusy` — freeBusy;
- `calendar.events` — создать, найти, изменить и удалить события в расшаренном календаре
  (`calendar.events.owned` не подходит: календарь принадлежит клиенту, а не сервисному аккаунту).

События создаются без гостей (`attendees`): сервисный аккаунт без domain-wide delegation
не может приглашать гостей, а подтверждение покупатель и так получает в мессенджере.
"""

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import quote

import httpx
import msgspec

from autostudio.booking.domain import Span
from autostudio.errors import AutoStudioError
from autostudio.llm import JSONObject

logger = logging.getLogger(__name__)

CALENDAR_API: Final = 'https://www.googleapis.com/calendar/v3'
SCOPES: Final[tuple[str, ...]] = (
    'https://www.googleapis.com/auth/calendar.freebusy',
    'https://www.googleapis.com/auth/calendar.events',
)
# Частное свойство события: по нему событие записи находится повторно (идемпотентность).
# Ключ до 44 символов, значение до 1024 (лимиты extended properties).
BOOKING_PROPERTY: Final = 'autostudioBooking'
# freeBusy принимает не больше 50 календарей за запрос.
FREEBUSY_MAX_CALENDARS: Final = 50
REQUEST_TIMEOUT_S: Final = 5.0


class CalendarError(AutoStudioError):
    """Google Calendar недоступен или отказал (текст — без токенов)."""


class EventGoneError(CalendarError):
    """События нет (удалено руками в календаре): 404 или 410."""


class EventDraft(msgspec.Struct, frozen=True, kw_only=True):
    """Событие записи, каким оно должно быть в календаре.

    Attributes:
        summary: Заголовок: `Стрижка — Анна`.
        description: Описание: код записи, мастер, телефон.
        start: Начало, UTC.
        end: Конец, UTC.
        timezone: Часовой пояс клиента (IANA) — так событие показывается в его календаре.
        booking_key: Значение частного свойства `BOOKING_PROPERTY` (`<клиент>/<код записи>`).
    """

    summary: str
    description: str
    start: dt.datetime
    end: dt.datetime
    timezone: str
    booking_key: str


class FreeBusy(msgspec.Struct, frozen=True, kw_only=True):
    """Ответ freeBusy, разобранный по календарям.

    Attributes:
        busy: Занятые промежутки календаря, UTC.
        errors: Календари, по которым Google вернул ошибку (`notFound` — не расшарен).
    """

    busy: dict[str, list[Span]]
    errors: dict[str, str]


class CalendarApi(Protocol):
    """Что пакету нужно от календаря (Google или заглушка демо)."""

    async def free_busy(self, calendars: Sequence[str], start: dt.datetime, end: dt.datetime) -> FreeBusy:
        """Занятость календарей в [start, end).

        Raises:
            CalendarError: Запрос не удался целиком.
        """
        ...

    async def find_event(self, calendar_id: str, booking_key: str) -> str | None:
        """id события записи (по частному свойству) или None."""
        ...

    async def insert_event(self, calendar_id: str, draft: EventDraft) -> str:
        """Создаёт событие и возвращает его id."""
        ...

    async def patch_event(self, calendar_id: str, event_id: str, draft: EventDraft) -> None:
        """Меняет время, заголовок и описание события.

        Raises:
            EventGoneError: События уже нет.
        """
        ...

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Удаляет событие; уже удалённое — не ошибка."""
        ...


class TokenSource(Protocol):
    """Источник access token для заголовка `Authorization`."""

    async def token(self) -> str:
        """Действующий токен.

        Raises:
            CalendarError: Ключ не читается или Google не выдал токен.
        """
        ...


def service_account_email(path: Path) -> str | None:
    """E-mail сервисного аккаунта из JSON-ключа (кому клиент делится календарём) или None."""

    class _Key(msgspec.Struct):
        client_email: str = ''

    try:
        return msgspec.json.decode(path.read_bytes(), type=_Key).client_email or None
    except (OSError, msgspec.DecodeError):
        return None


class _Period(msgspec.Struct):
    start: dt.datetime
    end: dt.datetime


class _CalendarError(msgspec.Struct):
    reason: str = ''


class _CalendarBusy(msgspec.Struct):
    busy: list[_Period] = []
    errors: list[_CalendarError] = []


class _FreeBusyResponse(msgspec.Struct):
    calendars: dict[str, _CalendarBusy] = {}


class _Event(msgspec.Struct):
    id: str


class _Events(msgspec.Struct):
    items: list[_Event] = []


class _ErrorDetail(msgspec.Struct):
    reason: str = ''


class _ErrorBody(msgspec.Struct):
    code: int = 0
    message: str = ''
    status: str = ''
    errors: list[_ErrorDetail] = []


class _ErrorResponse(msgspec.Struct):
    error: _ErrorBody | None = None


def _rfc3339(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _reason(response: httpx.Response) -> str:
    try:
        body = msgspec.json.decode(response.content, type=_ErrorResponse).error
    except msgspec.DecodeError:
        return f'HTTP {response.status_code}'
    if body is None:
        return f'HTTP {response.status_code}'
    detail = body.errors[0].reason if body.errors else body.status
    return f'HTTP {response.status_code} {detail}'.strip()


def _path(calendar_id: str) -> str:
    # В id календаря бывают «@» и «#» (`ru.ge#holiday@group.v.calendar.google.com`): в пути — только экранированным.
    return f'/calendars/{quote(calendar_id, safe="")}/events'


def event_body(draft: EventDraft) -> JSONObject:
    """Тело события для insert/patch: время с часовым поясом клиента, частное свойство записи."""
    return {
        'summary': draft.summary,
        'description': draft.description,
        'start': {'dateTime': draft.start.isoformat(), 'timeZone': draft.timezone},
        'end': {'dateTime': draft.end.isoformat(), 'timeZone': draft.timezone},
        'extendedProperties': {'private': {BOOKING_PROPERTY: draft.booking_key}},
    }


class GoogleCalendar:
    """`CalendarApi` поверх REST Google Calendar v3.

    Args:
        http: Общий httpx-клиент.
        tokens: Источник токена.
        timeout: Таймаут одного запроса — покупатель ждёт ответа бота.
        base_url: Адрес API (в тестах — тот же, его ловит respx).
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: TokenSource,
        *,
        timeout: float = REQUEST_TIMEOUT_S,
        base_url: str = CALENDAR_API,
    ) -> None:
        self._http = http
        self._tokens = tokens
        self._timeout = timeout
        self._base = base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: JSONObject | None = None,
    ) -> httpx.Response:
        headers = {'Authorization': f'Bearer {await self._tokens.token()}'}
        try:
            response = await self._http.request(
                method,
                f'{self._base}{path}',
                params=params,
                json=body,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise CalendarError(f'Google Calendar недоступен: {type(exc).__name__}') from None
        if response.status_code in (httpx.codes.NOT_FOUND, httpx.codes.GONE):
            raise EventGoneError(_reason(response))
        if response.is_error:
            raise CalendarError(f'Google Calendar: {_reason(response)}')
        return response

    async def free_busy(self, calendars: Sequence[str], start: dt.datetime, end: dt.datetime) -> FreeBusy:
        busy: dict[str, list[Span]] = {}
        errors: dict[str, str] = {}
        for first in range(0, len(calendars), FREEBUSY_MAX_CALENDARS):
            chunk = calendars[first : first + FREEBUSY_MAX_CALENDARS]
            items: list[JSONObject] = [{'id': cal} for cal in chunk]
            body: JSONObject = {'timeMin': _rfc3339(start), 'timeMax': _rfc3339(end), 'items': list(items)}
            try:
                response = await self._request('POST', '/freeBusy', body=body)
            except EventGoneError as exc:
                raise CalendarError(f'Google Calendar: {exc}') from None
            try:
                parsed = msgspec.json.decode(response.content, type=_FreeBusyResponse)
            except msgspec.DecodeError:
                raise CalendarError('Google Calendar: ответ freeBusy не разобран') from None
            for calendar in chunk:
                found = parsed.calendars.get(calendar)
                if found is None:
                    errors[calendar] = 'missing'
                elif found.errors:
                    errors[calendar] = found.errors[0].reason or 'error'
                else:
                    busy[calendar] = [
                        (period.start.astimezone(dt.UTC), period.end.astimezone(dt.UTC)) for period in found.busy
                    ]
        return FreeBusy(busy=busy, errors=errors)

    async def find_event(self, calendar_id: str, booking_key: str) -> str | None:
        params = {'privateExtendedProperty': f'{BOOKING_PROPERTY}={booking_key}', 'maxResults': '5'}
        try:
            response = await self._request('GET', _path(calendar_id), params=params)
        except EventGoneError as exc:
            raise CalendarError(f'календарь {calendar_id}: {exc}') from None
        try:
            items = msgspec.json.decode(response.content, type=_Events).items
        except msgspec.DecodeError:
            raise CalendarError('Google Calendar: список событий не разобран') from None
        return items[0].id if items else None

    async def insert_event(self, calendar_id: str, draft: EventDraft) -> str:
        try:
            response = await self._request('POST', _path(calendar_id), body=event_body(draft))
        except EventGoneError as exc:
            raise CalendarError(f'календарь {calendar_id}: {exc}') from None
        try:
            return msgspec.json.decode(response.content, type=_Event).id
        except msgspec.DecodeError:
            raise CalendarError('Google Calendar: ответ на создание события не разобран') from None

    async def patch_event(self, calendar_id: str, event_id: str, draft: EventDraft) -> None:
        await self._request('PATCH', f'{_path(calendar_id)}/{event_id}', body=event_body(draft))

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        try:
            await self._request('DELETE', f'{_path(calendar_id)}/{event_id}')
        except EventGoneError:
            # 410 Gone — событие уже удалено: по справочнику ошибок дальше делать нечего.
            logger.info('событие %s уже удалено из календаря', event_id)


class FakeCalendar:
    """`CalendarApi` в памяти: для демо и тестов.

    Занятость, которую «видит» freeBusy, — это внешние события (`external`) плюс события,
    созданные ботом: как у настоящего Google, где событие записи тоже занимает время.

    Args:
        external: Занятость по календарям, которую бот не создавал (личные дела мастера).
    """

    def __init__(self, external: Mapping[str, Sequence[Span]] | None = None) -> None:
        self.external: dict[str, list[Span]] = {cal: list(spans) for cal, spans in (external or {}).items()}
        self.events: dict[str, dict[str, EventDraft]] = {}
        self.calls: list[str] = []
        self.fail: bool = False
        self.missing: set[str] = set()
        self._next = 0

    def _check(self, call: str) -> None:
        self.calls.append(call)
        if self.fail:
            raise CalendarError('Google Calendar недоступен: ConnectError')

    async def free_busy(self, calendars: Sequence[str], start: dt.datetime, end: dt.datetime) -> FreeBusy:
        self._check('freeBusy')
        busy: dict[str, list[Span]] = {}
        for calendar in calendars:
            if calendar in self.missing:
                continue
            own = [(draft.start, draft.end) for draft in self.events.get(calendar, {}).values()]
            spans = sorted([*self.external.get(calendar, []), *own])
            busy[calendar] = [(a, b) for a, b in spans if a < end and b > start]
        return FreeBusy(busy=busy, errors=dict.fromkeys(sorted(self.missing & set(calendars)), 'notFound'))

    async def find_event(self, calendar_id: str, booking_key: str) -> str | None:
        self._check('find')
        events = self.events.get(calendar_id, {})
        return next((event_id for event_id, draft in events.items() if draft.booking_key == booking_key), None)

    async def insert_event(self, calendar_id: str, draft: EventDraft) -> str:
        self._check('insert')
        self._next += 1
        event_id = f'evt{self._next:04d}'
        self.events.setdefault(calendar_id, {})[event_id] = draft
        return event_id

    async def patch_event(self, calendar_id: str, event_id: str, draft: EventDraft) -> None:
        self._check('patch')
        events = self.events.get(calendar_id, {})
        if event_id not in events:
            raise EventGoneError('HTTP 404 notFound')
        events[event_id] = draft

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        self._check('delete')
        self.events.get(calendar_id, {}).pop(event_id, None)

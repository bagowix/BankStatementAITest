import copy
import datetime as dt
from pathlib import Path

import msgspec

from autostudio.booking.admin import AdminDesk
from autostudio.booking.config import BookingConfig
from autostudio.booking.domain import Customer, Slot
from autostudio.booking.engine import Engine
from autostudio.booking.heuristics import booking_fake_llm
from autostudio.booking.messages import Button, Incoming, Outgoing
from autostudio.booking.service import BookingService
from autostudio.booking.store import BookingStore
from autostudio.booking.texts import Texts
from autostudio.booking.views import Views
from autostudio.clock import FrozenClock
from autostudio.events import EventLog
from autostudio.llm import FakeLLM
from autostudio.sinks import CsvSink

# Вторник, 20 октября 2026, 12:30 по Тбилиси (UTC+4, без перевода часов).
START = dt.datetime(2026, 10, 20, 8, 30, tzinfo=dt.UTC)
TBILISI = 'Asia/Tbilisi'

BASE: dict = {
    'client': {'id': 'test-barbers', 'name': 'Test Barbers', 'timezone': TBILISI, 'language': 'en'},
    'booking': {
        'admin_chat_ids': [999],
        'languages': ['en', 'ru'],
        'contact_phone': '+995 555 00 00 00',
        'services': [
            {'id': 'haircut', 'name': {'en': 'Haircut', 'ru': 'Стрижка'}, 'duration_min': 45, 'price': '40'},
            {
                'id': 'beard',
                'name': {'en': 'Beard trim', 'ru': 'Оформление бороды'},
                'duration_min': 30,
                'price': '25.50',
            },
            {
                'id': 'combo',
                'name': {'en': 'Haircut + beard', 'ru': 'Стрижка + борода'},
                'duration_min': 75,
                'price': '55',
                'staff_ids': ['giorgi'],
            },
        ],
        'staff': [
            {'id': 'giorgi', 'name': 'Giorgi'},
            {
                'id': 'nika',
                'name': 'Nika',
                'hours': {
                    'wed': ['12:00-20:00'],
                    'thu': ['12:00-20:00'],
                    'fri': ['12:00-20:00'],
                    'sat': ['10:00-18:00'],
                },
            },
        ],
        'hours': {
            'mon': ['10:00-14:00', '15:00-20:00'],
            'tue': ['10:00-14:00', '15:00-20:00'],
            'wed': ['10:00-14:00', '15:00-20:00'],
            'thu': ['10:00-14:00', '15:00-20:00'],
            'fri': ['10:00-14:00', '15:00-20:00'],
            'sat': ['10:00-18:00'],
        },
        'faq': [
            {
                'id': 'address',
                'question': {'en': 'Where are you located?', 'ru': 'Где вы находитесь?'},
                'answer': {'en': '12 Rustaveli Ave, Tbilisi.', 'ru': 'Тбилиси, проспект Руставели, 12.'},
            },
            {
                'id': 'payment',
                'question': {'en': 'Do you accept cards?', 'ru': 'Можно оплатить картой?'},
                'answer': {'en': 'Yes — Visa, Mastercard and cash.', 'ru': 'Да — Visa, Mastercard и наличные.'},
            },
        ],
    },
}


def make_config(client: dict | None = None, **booking: object) -> BookingConfig:
    data = copy.deepcopy(BASE)
    data['client'].update(client or {})
    data['booking'].update(booking)
    return msgspec.convert(data, type=BookingConfig)


def local(day: dt.date, hour: int, minute: int = 0, zone: str = TBILISI) -> dt.datetime:
    from zoneinfo import ZoneInfo

    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(zone)).astimezone(dt.UTC)


WED = dt.date(2026, 10, 21)
TUE = dt.date(2026, 10, 20)


class Recorder:
    """Канал-заглушка: копит отправленное; может падать по сценарию."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[tuple[str, Outgoing]] = []
        self.texts: list[str] = []
        self.fail = fail

    async def send(self, chat_or_text, message: Outgoing | None = None) -> None:
        if self.fail is not None:
            raise self.fail
        if message is None:
            self.texts.append(chat_or_text)
        else:
            self.sent.append((chat_or_text, message))


class App:
    """Всё приложение без канала: движок, база, журнал, таблица в CSV."""

    def __init__(self, tmp_path: Path, config: BookingConfig, llm: FakeLLM | None = None) -> None:
        self.config = config
        self.clock = FrozenClock(START)
        self.store = BookingStore(tmp_path / 'booking.sqlite')
        self.events = EventLog(tmp_path / 'events.sqlite')
        self.sink = CsvSink(tmp_path / 'out')
        self.texts = Texts(config.booking, config.client.zone)
        self.views = Views(config, self.texts)
        self.service = BookingService(
            config=config, store=self.store, events=self.events, sink=self.sink, texts=self.texts
        )
        self.admin = AdminDesk(config=config, texts=self.texts, service=self.service)
        self.llm = llm or booking_fake_llm()
        self.operator = Recorder()
        self.engine = Engine(
            config=config,
            views=self.views,
            service=self.service,
            admin=self.admin,
            llm=self.llm,
            clock=self.clock,
            operator=self.operator,
        )
        self.last: dict[str, list[Outgoing]] = {}

    async def send(self, user: str, **fields) -> list[Outgoing]:
        fields.setdefault('name', 'Anna')
        fields.setdefault('language', 'en')
        replies = await self.engine.handle(Incoming(user=user, **fields))
        own = [reply for reply in replies if reply.to is None]
        if own:
            self.last[user] = own
        return replies

    async def text(self, user: str, text: str, **fields) -> list[Outgoing]:
        return await self.send(user, text=text, **fields)

    async def press(self, user: str, label: str, **fields) -> list[Outgoing]:
        button = find_button(self.last.get(user, []), label)
        return await self.send(user, data=button.data, **fields)

    def events_of(self, kind: str) -> list:
        return [
            e
            for e in self.events.between(START - dt.timedelta(days=30), START + dt.timedelta(days=60))
            if e.kind == kind
        ]

    def book_directly(self, user: str, service: str, staff: str, start: dt.datetime, name: str = 'Zed') -> object:
        svc = self.config.booking.service(service)
        slot = Slot(service_id=service, staff_id=staff, start=start, end=start + dt.timedelta(minutes=svc.duration_min))
        return self.store.create(
            slot,
            customer=Customer(key=user, name=name, phone='+995555000111'),
            source='buttons',
            language='en',
            now=self.clock(),
            buffer=dt.timedelta(0),
        )


def all_buttons(messages: list[Outgoing]) -> list[Button]:
    return [button for message in messages for row in message.buttons for button in row]


def find_button(messages: list[Outgoing], label: str) -> Button:
    for button in all_buttons(messages):
        if button.label.startswith(label) or button.data == label:
            return button
    raise AssertionError(f'нет кнопки {label!r} среди {[b.label for b in all_buttons(messages)]}')


def texts_of(messages: list[Outgoing]) -> str:
    return '\n'.join(message.text for message in messages)

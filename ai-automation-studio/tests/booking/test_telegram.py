import datetime as dt

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteWebhook,
    EditMessageReplyMarkup,
    SendMessage,
    SetMyCommands,
)
from aiogram.types import (
    Chat,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)

from autostudio.booking import telegram as tg
from autostudio.booking.domain import Customer
from autostudio.booking.messages import Button, DeliveryError, Outgoing, RecipientGoneError
from autostudio.booking.telegram import BookingBot, TelegramSender, chat_id_of, chat_key, render_markup, set_commands
from autostudio.errors import NotifyError
from tests.booking.helpers import START, App, Recorder, make_config

TOKEN = '42:TEST'
ADMIN_ID = 999


class MockSession(BaseSession):
    """Сессия aiogram без сети: копит запросы и отдаёт заготовленные ответы или ошибки."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []
        self.errors: dict[type, list[Exception]] = {}
        self.chat_errors: dict[int, Exception] = {}

    async def close(self) -> None:
        return None

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 - сигнатура задана BaseSession
        self.requests.append(method)
        queue = self.errors.get(type(method))
        if queue:
            raise queue.pop(0)
        if isinstance(method, SendMessage) and int(method.chat_id) in self.chat_errors:
            raise self.chat_errors[int(method.chat_id)]
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.requests),
                date=dt.datetime(2026, 10, 20, tzinfo=dt.UTC),
                chat=Chat(id=int(method.chat_id), type='private'),
                text=method.text,
            )
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):  # noqa: ASYNC109
        yield b''

    def sent(self) -> list[SendMessage]:
        return [request for request in self.requests if isinstance(request, SendMessage)]


@pytest.fixture
def session() -> MockSession:
    return MockSession()


@pytest.fixture
def bot(session) -> Bot:
    return Bot(TOKEN, session=session)


@pytest.fixture
def booking_bot(app: App, bot: Bot) -> BookingBot:
    return BookingBot(
        config=app.config, engine=app.engine, sender=TelegramSender(bot), texts=app.texts, operator=app.operator
    )


def user(uid: int = 1, **kw) -> dict:
    return {'id': uid, 'is_bot': False, 'first_name': 'Anna', 'language_code': 'en', 'username': 'anna', **kw}


def message_update(text: str | None = '/start', chat_id: int = 1, chat_type: str = 'private', **extra) -> dict:
    message = {'message_id': 1, 'date': 1792000000, 'chat': {'id': chat_id, 'type': chat_type}, 'from': user()}
    if text is not None:
        message['text'] = text
    message.update(extra)
    return {'update_id': 1, 'message': message}


def callback_update(data: str, chat_id: int = 1, chat_type: str = 'private', with_message: bool = True, **who) -> dict:
    query = {'id': 'cq1', 'from': user(**who), 'chat_instance': 'ci', 'data': data}
    if with_message:
        query['message'] = {
            'message_id': 5,
            'date': 1792000000,
            'chat': {'id': chat_id, 'type': chat_type},
            'text': 'menu',
            'reply_markup': {'inline_keyboard': [[{'text': 'x', 'callback_data': data}]]},
        }
    return {'update_id': 2, 'callback_query': query}


async def feed(bot: Bot, booking_bot: BookingBot, raw: dict) -> None:
    dispatcher = Dispatcher()
    dispatcher.include_router(booking_bot.router())
    await dispatcher.feed_update(bot, Update.model_validate(raw, context={'bot': bot}))


def test__render_markup__each_kind__matching_markup():
    inline = render_markup(Outgoing(text='x', buttons=[[Button('A', 'a')]]))
    assert isinstance(inline, InlineKeyboardMarkup) and inline.inline_keyboard[0][0].callback_data == 'a'
    contact = render_markup(Outgoing(text='x', request_contact='📱 Share', keyboard=['✖️ Cancel']))
    assert isinstance(contact, ReplyKeyboardMarkup)
    assert contact.keyboard[0][0].request_contact and contact.keyboard[1][0].text == '✖️ Cancel'
    assert contact.one_time_keyboard and contact.resize_keyboard
    assert isinstance(render_markup(Outgoing(text='x', remove_keyboard=True)), ReplyKeyboardRemove)
    assert render_markup(Outgoing(text='x')) is None


def test__chat_keys__foreign_key__recipient_gone():
    assert chat_key(-100123) == 'tg:-100123' and chat_id_of('tg:-100123') == -100123
    for bad in ('console:operator', 'tg:abc', '123'):
        with pytest.raises(RecipientGoneError):
            chat_id_of(bad)


async def test__sender__success__send_message_with_markup_and_clipped_text(bot, session):
    await TelegramSender(bot).send('tg:7', Outgoing(text='x' * 5000, buttons=[[Button('A', 'a')]]))
    [request] = session.sent()
    assert request.chat_id == 7 and len(request.text) == 4000
    assert isinstance(request.reply_markup, InlineKeyboardMarkup)


async def test__sender__blocked_or_bad_request__recipient_gone(bot, session):
    session.errors[SendMessage] = [
        TelegramForbiddenError(
            method=SendMessage(chat_id=7, text='x'), message='Forbidden: bot was blocked by the user'
        ),
        TelegramBadRequest(method=SendMessage(chat_id=7, text='x'), message='Bad Request: chat not found'),
    ]
    for _ in range(2):
        with pytest.raises(RecipientGoneError):
            await TelegramSender(bot).send('tg:7', Outgoing(text='hi'))


async def test__sender__retry_after__waits_and_retries_once(bot, session, monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(tg.asyncio, 'sleep', fake_sleep)
    flood = TelegramRetryAfter(method=SendMessage(chat_id=7, text='x'), message='Too Many Requests', retry_after=3)
    session.errors[SendMessage] = [flood]
    await TelegramSender(bot).send('tg:7', Outgoing(text='hi'))
    assert waits == [3] and len(session.sent()) == 2
    session.errors[SendMessage] = [flood, flood]
    with pytest.raises(DeliveryError):
        await TelegramSender(bot).send('tg:7', Outgoing(text='hi'))
    long = TelegramRetryAfter(method=SendMessage(chat_id=7, text='x'), message='Too Many Requests', retry_after=600)
    session.errors[SendMessage] = [long]
    with pytest.raises(DeliveryError):
        await TelegramSender(bot).send('tg:7', Outgoing(text='hi'))


async def test__sender__network_error__type_only_no_token(bot, session):
    session.errors[SendMessage] = [
        TelegramNetworkError(method=SendMessage(chat_id=7, text='x'), message=f'https://api.telegram.org/bot{TOKEN}/x')
    ]
    with pytest.raises(DeliveryError) as caught:
        await TelegramSender(bot).send('tg:7', Outgoing(text='hi'))
    assert str(caught.value) == 'TelegramNetworkError' and TOKEN not in str(caught.value)


async def test__router__private_start__greeting_and_menu(bot, session, booking_bot):
    await feed(bot, booking_bot, message_update('/start'))
    greeting, menu = session.sent()
    assert greeting.chat_id == 1 and greeting.text.startswith('Hi, Anna!')
    assert isinstance(menu.reply_markup, InlineKeyboardMarkup)


async def test__router__callback__answered_buttons_removed_and_processed(bot, session, booking_bot):
    await feed(bot, booking_bot, callback_update('b'))
    kinds = [type(request) for request in session.requests]
    assert kinds[:2] == [AnswerCallbackQuery, EditMessageReplyMarkup]
    assert session.sent()[0].text == 'Which service would you like?'


async def test__router__callback_without_message__uses_user_chat(bot, session, booking_bot):
    await feed(bot, booking_bot, callback_update('pr', with_message=False))
    assert type(session.requests[0]) is AnswerCallbackQuery
    assert session.sent()[0].chat_id == 1 and 'Our services' in session.sent()[0].text


async def test__router__callback_api_errors__still_processed(bot, session, booking_bot):
    session.errors[AnswerCallbackQuery] = [
        TelegramBadRequest(method=AnswerCallbackQuery(callback_query_id='cq1'), message='query is too old')
    ]
    session.errors[EditMessageReplyMarkup] = [
        TelegramBadRequest(method=EditMessageReplyMarkup(), message='message is not modified')
    ]
    await feed(bot, booking_bot, callback_update('m'))
    assert session.sent()[0].text == 'What would you like to do?'


async def test__router__contact_shared__phone_passed_to_engine(bot, session, booking_bot):
    contact = {'phone_number': '995555123456', 'first_name': 'Anna', 'user_id': 1}
    await feed(bot, booking_bot, message_update(None, contact=contact))
    assert session.sent()[0].text == 'Got it ✅'
    assert isinstance(session.sent()[0].reply_markup, ReplyKeyboardRemove)


async def test__router__group_not_in_admin_chats__admin_command_ignored_even_from_owner(bot, session, booking_bot):
    await feed(bot, booking_bot, message_update('/today', chat_id=-100, chat_type='group'))
    await feed(bot, booking_bot, message_update('hello', chat_id=-100, chat_type='supergroup'))
    # Владелец набрал /week в семейном или публичном чате: расписание с телефонами туда не уходит.
    raw = message_update('/week@TestBot', chat_id=-100, chat_type='group')
    raw['message']['from']['id'] = ADMIN_ID
    await feed(bot, booking_bot, raw)
    assert session.sent() == []
    callback = callback_update('ac:ABCDEF', chat_id=-100, chat_type='group', id=ADMIN_ID)
    await feed(bot, booking_bot, callback)
    assert session.sent() == []


async def test__router__staff_group_in_admin_chats__admin_command_served(bot, session, app):
    config = make_config(admin_chat_ids=[ADMIN_ID, -100])
    staff_bot = BookingBot(config=config, engine=app.engine, sender=TelegramSender(bot), texts=app.texts)
    raw = message_update('/today@TestBot', chat_id=-100, chat_type='group')
    await feed(bot, staff_bot, raw)
    [agenda] = session.sent()
    assert agenda.chat_id == -100 and agenda.text.startswith('📋')
    await feed(bot, staff_bot, message_update('just chatting', chat_id=-100, chat_type='group'))
    assert len(session.sent()) == 1


async def test__router__group_callback_from_non_admin__ignored(bot, session, booking_bot):
    await feed(bot, booking_bot, callback_update('ac:ABCDEF', chat_id=-100, chat_type='group'))
    assert session.sent() == []
    assert [type(r) for r in session.requests] == [AnswerCallbackQuery]


async def test__router__admin_chat_group_member__admin(bot, session, app):
    config = make_config(admin_chat_ids=[-100])
    booking_bot = BookingBot(config=config, engine=app.engine, sender=TelegramSender(bot), texts=app.texts)
    assert booking_bot.is_admin(5, -100) and not booking_bot.is_admin(5, 5)
    assert booking_bot.admin_keys == ['tg:-100']


async def test__router__message_from_bot__ignored(bot, session, booking_bot):
    raw = message_update('/start')
    raw['message']['from']['is_bot'] = True
    await feed(bot, booking_bot, raw)
    assert session.requests == []


async def test__router__admin_alert__delivered_to_admin_chat(bot, session, app, booking_bot):
    await feed(bot, booking_bot, callback_update('h'))
    await feed(bot, booking_bot, message_update('Can Giorgi do a fade?'))
    targets = [request.chat_id for request in session.sent()]
    assert ADMIN_ID in targets and 1 in targets


async def test__process__engine_bug__apology_and_operator_alert(bot, session, app, booking_bot, monkeypatch):
    async def broken(message):
        raise RuntimeError('bug')

    monkeypatch.setattr(app.engine, 'handle', broken)
    await feed(bot, booking_bot, message_update('/start'))
    assert session.sent()[0].text.startswith('Sorry, something went wrong on our side')
    assert app.operator.texts and 'RuntimeError' in app.operator.texts[0]
    booking_bot._operator = Recorder(fail=NotifyError('down'))
    await feed(bot, booking_bot, message_update('/start'))
    assert len(session.sent()) == 2


async def test__process__no_operator__apology_only(bot, session, app, monkeypatch):
    async def broken(message):
        raise RuntimeError('bug')

    monkeypatch.setattr(app.engine, 'handle', broken)
    quiet = BookingBot(config=app.config, engine=app.engine, sender=TelegramSender(bot), texts=app.texts)
    await feed(bot, quiet, message_update('/start'))
    assert len(session.sent()) == 1


async def test__set_commands__languages__default_and_per_language(bot, session, booking_bot):
    await set_commands(bot, booking_bot, ['en', 'ru'])
    calls = [r for r in session.requests if isinstance(r, SetMyCommands)]
    assert [c.language_code for c in calls] == [None, 'en', 'ru']
    assert calls[2].commands[1].description == 'Записаться'
    session.errors[SetMyCommands] = [TelegramBadRequest(method=calls[0], message='bad')]
    await set_commands(bot, booking_bot, ['en'])


async def test__run_polling__stopped__webhook_dropped_and_loop_stopped(bot, session, booking_bot, app, monkeypatch):
    from autostudio.booking.reminders import ReminderLoop
    from autostudio.runlog import Heartbeat

    started: list[bool] = []

    async def fake_polling(self, *bots, **kwargs):
        started.append(kwargs['handle_signals'])

    monkeypatch.setattr(Dispatcher, 'start_polling', fake_polling)
    loop = ReminderLoop(
        config=app.config,
        store=app.store,
        sender=TelegramSender(bot),
        sink=app.sink,
        events=app.events,
        texts=app.texts,
        heartbeat=Heartbeat(None, None),
        operator=None,
        clock=app.clock,
    )
    await tg.run_polling(bot, booking_bot, loop, ['en', 'ru'])
    assert started == [True]
    assert any(isinstance(r, DeleteWebhook) for r in session.requests)


def forbidden(chat_id: int) -> TelegramForbiddenError:
    return TelegramForbiddenError(
        method=SendMessage(chat_id=chat_id, text='x'), message='Forbidden: bot was blocked by the user'
    )


async def test__router__contact_of_someone_else__not_verified(bot, session, booking_bot, app):
    seen = []

    async def spy(incoming):
        seen.append(incoming)
        return []

    booking_bot._engine.handle = spy
    own = {'phone_number': '995555123456', 'first_name': 'Anna', 'user_id': 1}
    other = {'phone_number': '+1 000 000 0000', 'first_name': 'Bob', 'user_id': 777}
    no_owner = {'phone_number': '995555123456', 'first_name': 'Card'}
    for contact in (own, other, no_owner):
        await feed(bot, booking_bot, message_update(None, contact=contact))
    assert [(item.phone, item.phone_verified) for item in seen] == [
        ('995555123456', True),
        ('+1 000 000 0000', False),
        ('995555123456', False),
    ]


async def test__process__engine_bug_repeated__operator_alerted_once_per_hour(
    bot, session, app, booking_bot, monkeypatch
):
    async def broken(message):
        raise ValueError('forged callback')

    monkeypatch.setattr(app.engine, 'handle', broken)
    for _ in range(5):
        await feed(bot, booking_bot, callback_update('d:haircut:_:20261021:x'))
    assert len(app.operator.texts) == 1
    booking_bot._clock = lambda: dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)
    await feed(bot, booking_bot, callback_update('d:haircut:_:20261021:x'))
    assert len(app.operator.texts) == 2


async def test__process__admin_blocked_bot__operator_alerted(bot, session, app, booking_bot):
    session.chat_errors[ADMIN_ID] = forbidden(ADMIN_ID)
    await feed(bot, booking_bot, callback_update('h'))
    await feed(bot, booking_bot, message_update('Can Giorgi do a fade?'))
    # Покупатель ответ получил; попытка в чат администратора (последняя) упала.
    assert [request.chat_id for request in session.sent()] == [1, 1, ADMIN_ID]
    assert app.operator.texts and 'tg:999 не получает сообщения бота' in app.operator.texts[0]


async def test__process__reply_to_customer_who_blocked_bot__admin_told_not_delivered(bot, session, app, booking_bot):
    app.store.save_customer(Customer(key='tg:4242', language='en'), START)
    session.chat_errors[4242] = TelegramBadRequest(
        method=SendMessage(chat_id=4242, text='x'), message='Bad Request: chat not found'
    )
    raw = message_update('/reply tg:4242 Yes, we do kids coloring')
    raw['message']['chat']['id'] = ADMIN_ID
    raw['message']['from']['id'] = ADMIN_ID
    await feed(bot, booking_bot, raw)
    attempt, note = session.sent()  # попытка в tg:4242 упала — админу честное «не доставлено»
    assert attempt.chat_id == 4242
    assert note.chat_id == ADMIN_ID and note.text.startswith('Not delivered to tg:4242')
    session.chat_errors.clear()
    await feed(bot, booking_bot, raw)
    relay, receipt = session.sent()[2:]
    assert relay.chat_id == 4242 and receipt.text == 'Delivered to tg:4242.'

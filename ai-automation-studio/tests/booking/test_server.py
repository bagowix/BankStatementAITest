import asyncio
import json
import socket

import aiohttp
import msgspec
import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.methods import DeleteWebhook, GetMe, SendMessage, SetWebhook
from aiogram.types import Message
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.server import (
    HEALTH_PATH,
    TELEGRAM_AUTH_HEADER,
    TELEGRAM_PATH,
    WHATSAPP_PATH,
    TelegramWebhook,
    serve,
    web_app,
)
from autostudio.booking.telegram import ALLOWED_UPDATES, BookingBot, TelegramSender
from autostudio.runlog import Heartbeat
from tests.booking.helpers import App, make_config
from tests.booking.test_telegram import TOKEN, MockSession, message_update

SECRET = 'tg-webhook-secret_123'


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def parts(app: App, session: MockSession) -> tuple[Bot, BookingBot, ReminderLoop]:
    bot = Bot(TOKEN, session=session)
    sender = TelegramSender(bot)
    booking_bot = BookingBot(config=app.config, engine=app.engine, sender=sender, texts=app.texts)
    loop = ReminderLoop(
        config=app.config,
        store=app.store,
        sender=sender,
        sink=app.sink,
        events=app.events,
        texts=app.texts,
        heartbeat=Heartbeat(None, None),
        operator=None,
        clock=app.clock,
    )
    return bot, booking_bot, loop


async def test__serve__polling_mode__webhook_removed_polling_started_loop_stopped(tmp_path, monkeypatch):
    app = App(tmp_path, make_config())
    session = MockSession()
    bot, booking_bot, loop = parts(app, session)
    started: list[dict] = []

    async def fake_polling(self, *bots, **kwargs):
        started.append(kwargs)

    monkeypatch.setattr(Dispatcher, 'start_polling', fake_polling)
    await serve(config=app.config, bot=bot, booking_bot=booking_bot, loop=loop)
    assert started[0]['handle_signals'] is False and started[0]['close_bot_session'] is False
    assert started[0]['allowed_updates'] == ALLOWED_UPDATES and started[0]['tasks_concurrency_limit'] == 64
    assert any(isinstance(request, DeleteWebhook) for request in session.requests)
    assert not any(isinstance(request, SetWebhook) for request in session.requests)


async def test__serve__polling_crashes__error_raised_after_cleanup(tmp_path, monkeypatch):
    app = App(tmp_path, make_config())
    bot, booking_bot, loop = parts(app, MockSession())

    async def broken_polling(self, *bots, **kwargs):
        raise TelegramUnauthorizedError(method=GetMe(), message='Unauthorized')

    monkeypatch.setattr(Dispatcher, 'start_polling', broken_polling)
    with pytest.raises(TelegramUnauthorizedError):
        await serve(config=app.config, bot=bot, booking_bot=booking_bot, loop=loop, install_signals=False)


async def test__serve__stop_event_during_polling__polling_stopped(tmp_path, monkeypatch):
    app = App(tmp_path, make_config())
    bot, booking_bot, loop = parts(app, MockSession())
    released = asyncio.Event()

    async def endless_polling(self, *bots, **kwargs):
        await released.wait()

    async def fake_stop(self):
        released.set()

    monkeypatch.setattr(Dispatcher, 'start_polling', endless_polling)
    monkeypatch.setattr(Dispatcher, 'stop_polling', fake_stop)
    stop = asyncio.Event()
    task = asyncio.create_task(
        serve(config=app.config, bot=bot, booking_bot=booking_bot, loop=loop, stop=stop, install_signals=False)
    )
    await asyncio.sleep(0.05)
    assert not task.done()
    stop.set()  # как SIGTERM
    await asyncio.wait_for(task, timeout=5)
    assert released.is_set()


async def wait_up(url: str) -> None:
    async with aiohttp.ClientSession() as client:
        for _ in range(100):
            try:
                async with client.get(url) as response:
                    if response.status == 200:
                        return
            except aiohttp.ClientError:
                await asyncio.sleep(0.02)
    raise AssertionError('сервер не поднялся')


async def test__serve__telegram_webhook__secret_header_required_update_processed(tmp_path):
    import httpx

    from tests.booking.test_whatsapp import VERIFY, Harness

    config = make_config(
        telegram={'mode': 'webhook'},
        server={'public_url': 'https://bot.example.test/', 'host': '127.0.0.1'},
        whatsapp={'phone_number_id': '106540352242922'},
    )
    app = App(tmp_path, config)
    session = MockSession()
    bot, booking_bot, loop = parts(app, session)
    port = free_port()
    stop = asyncio.Event()
    http = httpx.AsyncClient()
    task = asyncio.create_task(
        serve(
            config=config,
            bot=bot,
            booking_bot=booking_bot,
            loop=loop,
            whatsapp=Harness(app, http).bot,
            telegram_secret=SECRET,
            port=port,
            stop=stop,
            install_signals=False,
        )
    )
    base = f'http://127.0.0.1:{port}'
    try:
        await wait_up(f'{base}{HEALTH_PATH}')
        async with aiohttp.ClientSession() as client:
            query = {'hub.mode': 'subscribe', 'hub.verify_token': VERIFY, 'hub.challenge': 'c1'}
            async with client.get(f'{base}{WHATSAPP_PATH}', params=query) as response:
                assert response.status == 200 and await response.text() == 'c1'
        update = message_update('/start site')
        async with aiohttp.ClientSession() as client:
            async with client.post(f'{base}{TELEGRAM_PATH}', json=update) as response:
                assert response.status == 401  # без заголовка секрета
            headers = {'X-Telegram-Bot-Api-Secret-Token': 'wrong'}
            async with client.post(f'{base}{TELEGRAM_PATH}', json=update, headers=headers) as response:
                assert response.status == 401
            headers = {'X-Telegram-Bot-Api-Secret-Token': SECRET}
            async with client.post(f'{base}{TELEGRAM_PATH}', json=update, headers=headers) as response:
                assert response.status == 200
        for _ in range(100):
            if any(isinstance(request, SendMessage) for request in session.requests):
                break
            await asyncio.sleep(0.02)
    finally:
        stop.set()
        await task
        await http.aclose()
    [webhook] = [request for request in session.requests if isinstance(request, SetWebhook)]
    assert webhook.url == 'https://bot.example.test/telegram/webhook' and webhook.secret_token == SECRET
    assert webhook.allowed_updates == ALLOWED_UPDATES and webhook.drop_pending_updates is False
    assert not any(isinstance(request, DeleteWebhook) for request in session.requests)
    # Метка deep link из вебхука дошла до журнала.
    assert [event.payload['source'] for event in app.events_of('booking.link_opened')] == ['site']


async def test__web_app__routes_health_whatsapp_and_secretless_telegram_refused(tmp_path):
    import httpx

    from tests.booking.test_whatsapp import Harness

    app = App(tmp_path, make_config(whatsapp={'phone_number_id': '106540352242922'}))
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        async with TestClient(TestServer(web_app(whatsapp=harness.bot))) as client:
            assert (await client.get(HEALTH_PATH)).status == 200
            assert (await client.get(WHATSAPP_PATH)).status == 403
            assert (await client.post(TELEGRAM_PATH)).status in (404, 405)
    for secret in (None, ''):
        with pytest.raises(ValueError, match='secret_token'):
            TelegramWebhook(Dispatcher(), Bot(TOKEN, session=MockSession()), secret)


async def test__web_app__whatsapp_payload_2mb_signed__accepted_not_413(tmp_path):
    # Meta шлёт вебхуки до 3 МБ; у aiohttp по умолчанию предел 1 МиБ — такой вебхук получил бы 413.
    import httpx

    from tests.booking.test_whatsapp import Harness, sign

    app = App(tmp_path, make_config(whatsapp={'phone_number_id': '106540352242922'}))
    value = {'messaging_product': 'whatsapp', 'statuses': [], 'padding': 'x' * (2 * 1024**2)}
    raw = {'object': 'whatsapp_business_account', 'entry': [{'changes': [{'field': 'messages', 'value': value}]}]}
    body = msgspec.json.encode(raw)
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        async with TestClient(TestServer(web_app(whatsapp=harness.bot))) as client:
            response = await client.post(WHATSAPP_PATH, data=body, headers={'X-Hub-Signature-256': sign(body)})
            assert response.status == 200
            await harness.bot.drain()


class SlowBot:
    """Диспетчер с хендлером, который ждёт сигнала: апдейт «в работе», пока тест не отпустит."""

    def __init__(self, *, fail: bool = False) -> None:
        self.gate = asyncio.Event()
        self.started: list[str] = []
        self.done: list[str] = []
        self.dispatcher = Dispatcher()
        router = Router()

        @router.message()
        async def slow(message: Message) -> None:
            self.started.append(message.text or '')
            await self.gate.wait()
            if fail:
                raise RuntimeError('bug')
            self.done.append(message.text or '')

        self.dispatcher.include_router(router)

    async def wait_started(self, count: int) -> None:
        for _ in range(200):
            if len(self.started) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f'начато {len(self.started)} апдейтов из {count}')


def update_json(text: str, update_id: int) -> str:
    return json.dumps({**message_update(text), 'update_id': update_id})


async def test__telegram_webhook__limit_reached__next_update_waits_for_slot_then_drained(tmp_path):
    slow = SlowBot()
    webhook = TelegramWebhook(slow.dispatcher, Bot(TOKEN, session=MockSession()), SECRET, limit=1)
    application = web.Application()
    webhook.routes(application, '/tg')
    headers = {TELEGRAM_AUTH_HEADER: SECRET, 'Content-Type': 'application/json'}
    async with TestClient(TestServer(application)) as client:
        first = await client.post('/tg', data=update_json('one', 1), headers=headers)
        assert first.status == 200 and webhook.in_flight == 1  # 200 сразу, обработка в фоне
        await slow.wait_started(1)
        second = asyncio.create_task(client.post('/tg', data=update_json('two', 2), headers=headers))
        await asyncio.sleep(0.05)
        # Слот занят: второй апдейт не стал ещё одной задачей, Telegram ждёт ответа.
        assert not second.done() and slow.started == ['one']
        slow.gate.set()
        assert (await second).status == 200
        await webhook.drain()
    assert slow.done == ['one', 'two'] and webhook.in_flight == 0


async def test__telegram_webhook__wrong_secret_bad_json_handler_crash__401_400_logged(tmp_path, caplog):
    slow = SlowBot(fail=True)
    slow.gate.set()
    webhook = TelegramWebhook(slow.dispatcher, Bot(TOKEN, session=MockSession()), SECRET)
    application = web.Application()
    webhook.routes(application, '/tg')
    async with TestClient(TestServer(application)) as client:
        wrong = await client.post('/tg', data=update_json('x', 1), headers={TELEGRAM_AUTH_HEADER: 'nope'})
        assert wrong.status == 401 and slow.started == []
        bad = await client.post('/tg', data='{"update_id": "x"', headers={TELEGRAM_AUTH_HEADER: SECRET})
        assert bad.status == 400
        crash = await client.post('/tg', data=update_json('boom', 3), headers={TELEGRAM_AUTH_HEADER: SECRET})
        assert crash.status == 200
        await webhook.drain()
    assert slow.started == ['boom'] and 'апдейт Telegram 3 не обработан' in caplog.text


async def test__serve__telegram_webhook_update_in_flight_on_stop__finished_before_return(tmp_path):
    # Telegram уже получил 200 и апдейт не повторит: остановка должна дождаться его, а не оборвать.
    config = make_config(
        telegram={'mode': 'webhook'}, server={'public_url': 'https://bot.example.test/', 'host': '127.0.0.1'}
    )
    app = App(tmp_path, config)
    bot, booking_bot, loop = parts(app, MockSession())
    gate = asyncio.Event()
    handled: list[str] = []

    async def slow_engine(incoming):
        await gate.wait()
        handled.append(incoming.text)
        return []

    app.engine.handle = slow_engine
    port = free_port()
    stop = asyncio.Event()
    task = asyncio.create_task(
        serve(
            config=config,
            bot=bot,
            booking_bot=booking_bot,
            loop=loop,
            telegram_secret=SECRET,
            port=port,
            stop=stop,
            install_signals=False,
        )
    )
    base = f'http://127.0.0.1:{port}'
    await wait_up(f'{base}{HEALTH_PATH}')
    async with aiohttp.ClientSession() as client:
        headers = {TELEGRAM_AUTH_HEADER: SECRET}
        async with client.post(f'{base}{TELEGRAM_PATH}', json=message_update('hello'), headers=headers) as response:
            assert response.status == 200
    await asyncio.sleep(0.05)
    stop.set()  # как SIGTERM, пока апдейт ещё в работе
    await asyncio.sleep(0.1)
    assert not task.done() and handled == []
    gate.set()
    await asyncio.wait_for(task, timeout=5)
    assert handled == ['hello']

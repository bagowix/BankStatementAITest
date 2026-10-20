import datetime as dt
import hashlib
import hmac

import httpx
import msgspec
import pytest
import respx
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from autostudio.booking import callbacks as cb
from autostudio.booking.alarm import OperatorAlarm
from autostudio.booking.messages import (
    Button,
    DeliveryError,
    Incoming,
    Outgoing,
    RecipientGoneError,
    Reminder,
    RoutingSender,
)
from autostudio.booking.relay import Relay
from autostudio.booking.wa_format import KEYBOARD_PREFIX, MORE_PREFIX, OWN_PHONE_ID, WebhookPayload
from autostudio.booking.whatsapp import MESSAGE_EVENT, WhatsAppBot, WhatsAppSender
from tests.booking.helpers import START, App, Recorder, make_config

URL = 'https://graph.facebook.com/v25.0/106540352242922/messages'
WA = {
    'phone_number_id': '106540352242922',
    'reminder_template': 'booking_reminder',
    'template_languages': {'en': 'en_US'},
}
SECRET = 'app-secret'
VERIFY = 'verify-me'
TOKEN = 'EAAG-system-user-token'
OK = httpx.Response(200, json={'messaging_product': 'whatsapp', 'messages': [{'id': 'wamid.out'}]})


@pytest.fixture
def app(tmp_path) -> App:
    return App(tmp_path, make_config(whatsapp=WA))


def alarm_for(app: App) -> OperatorAlarm:
    return OperatorAlarm(app.operator, 'Test Barbers / booking', clock=app.clock)


def make_sender(app: App, http: httpx.AsyncClient, *, config=None) -> WhatsAppSender:
    return WhatsAppSender(
        http=http,
        config=config or app.config,
        token=TOKEN,
        store=app.store,
        texts=app.texts,
        events=app.events,
        alarm=alarm_for(app),
        clock=app.clock,
        retry_delay=0,
    )


def contact(app: App, *, user_id: str | None = 'GE.1', phone: str | None = '995599000111', key: str = 'wa:c1'):
    return app.store.touch_contact('whatsapp', user_id=user_id, phone=phone, at=app.clock(), new_key=key)


def sent_json(route) -> list[dict]:
    return [msgspec.json.decode(call.request.content) for call in route.calls]


@respx.mock
async def test__send__in_window__bsuid_recipient_service_message_counted(app):
    route = respx.post(URL).mock(return_value=OK)
    contact(app)
    async with httpx.AsyncClient() as http:
        await make_sender(app, http).send('wa:c1', Outgoing(text='Hello'))
    [body] = sent_json(route)
    assert body['recipient'] == 'GE.1' and 'to' not in body and body['text'] == {'body': 'Hello'}
    assert route.calls[0].request.headers['Authorization'] == f'Bearer {TOKEN}'
    [event] = app.events_of(MESSAGE_EVENT)
    assert event.payload == {'kind': 'service', 'purpose': 'reply'}


@respx.mock
async def test__send__phone_only_contact__to_field(app):
    route = respx.post(URL).mock(return_value=OK)
    contact(app, user_id=None)
    async with httpx.AsyncClient() as http:
        await make_sender(app, http).send('wa:c1', Outgoing(text='Hi'))
    assert sent_json(route)[0]['to'] == '995599000111'


@respx.mock
async def test__send__outside_window_free_form__not_sent_recipient_gone(app):
    route = respx.post(URL).mock(return_value=OK)
    contact(app)
    app.clock.advance(dt.timedelta(hours=25))
    async with httpx.AsyncClient() as http:
        with pytest.raises(RecipientGoneError, match='24'):
            await make_sender(app, http).send('wa:c1', Outgoing(text='/reply from owner'))
        with pytest.raises(RecipientGoneError, match='неизвестный'):
            await make_sender(app, http).send('wa:nobody', Outgoing(text='x'))
    assert not route.called


def reminder() -> Outgoing:
    return Outgoing(
        text='⏰ Reminder: Haircut Wed 21 Oct, 12:00',
        buttons=[[Button('Change or cancel', 'bk:K7Q2MX')]],
        reminder=Reminder(service='Haircut', when='Wed 21 Oct, 12:00', language='en'),
    )


@respx.mock
async def test__reminder__outside_window_subscribed__approved_template(app):
    route = respx.post(URL).mock(return_value=OK)
    contact(app)
    app.store.set_consent('wa:c1', subscribed=True, now=app.clock())
    app.clock.advance(dt.timedelta(hours=30))
    async with httpx.AsyncClient() as http:
        await make_sender(app, http).send('wa:c1', reminder())
    [body] = sent_json(route)
    assert body['type'] == 'template'
    assert body['template']['name'] == 'booking_reminder' and body['template']['language'] == {'code': 'en_US'}
    assert [p['text'] for p in body['template']['components'][0]['parameters']] == ['Haircut', 'Wed 21 Oct, 12:00']
    assert app.events_of(MESSAGE_EVENT)[0].payload == {'kind': 'template', 'purpose': 'reminder'}


@respx.mock
async def test__reminder__inside_window__free_form_with_buttons(app):
    route = respx.post(URL).mock(return_value=OK)
    contact(app)
    app.store.set_consent('wa:c1', subscribed=True, now=app.clock())
    async with httpx.AsyncClient() as http:
        await make_sender(app, http).send('wa:c1', reminder())
    [body] = sent_json(route)
    assert body['type'] == 'interactive' and body['interactive']['type'] == 'button'
    assert app.events_of(MESSAGE_EVENT)[0].payload == {'kind': 'service', 'purpose': 'reminder'}


@respx.mock
async def test__reminder__no_consent_opted_out_or_no_template__recipient_gone(app):
    route = respx.post(URL).mock(return_value=OK)
    contact(app)
    async with httpx.AsyncClient() as http:
        sender = make_sender(app, http)
        with pytest.raises(RecipientGoneError, match='согласия'):
            await sender.send('wa:c1', reminder())
        app.store.set_consent('wa:c1', subscribed=True, now=app.clock())
        app.clock.advance(dt.timedelta(minutes=1))
        app.store.set_consent('wa:c1', subscribed=False, now=app.clock())
        with pytest.raises(RecipientGoneError, match='отписался'):
            await sender.send('wa:c1', reminder())
        app.clock.advance(dt.timedelta(minutes=1))
        app.store.set_consent('wa:c1', subscribed=True, now=app.clock())
        app.clock.advance(dt.timedelta(hours=30))
        no_template = make_config(whatsapp={'phone_number_id': '106540352242922'})
        with pytest.raises(RecipientGoneError, match='шаблон'):
            await make_sender(app, http, config=no_template).send('wa:c1', reminder())
    assert not route.called


@respx.mock
async def test__post__throttled_then_ok__retried_once(app):
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(400, json={'error': {'code': 131056, 'message': 'pair rate limit'}}), OK]
    )
    contact(app)
    async with httpx.AsyncClient() as http:
        await make_sender(app, http).send('wa:c1', Outgoing(text='x'))
    assert route.call_count == 2


@respx.mock
async def test__post__server_down_twice__delivery_error_retryable(app):
    respx.post(URL).mock(return_value=httpx.Response(503, text='down'))
    contact(app)
    async with httpx.AsyncClient() as http:
        with pytest.raises(DeliveryError) as caught:
            await make_sender(app, http).send('wa:c1', Outgoing(text='x'))
    assert not isinstance(caught.value, RecipientGoneError) and '503' in str(caught.value)


@respx.mock
async def test__post__network_error__type_only_no_token(app):
    respx.post(URL).mock(side_effect=httpx.ConnectError(f'cannot connect, token {TOKEN}'))
    contact(app)
    async with httpx.AsyncClient() as http:
        with pytest.raises(DeliveryError) as caught:
            await make_sender(app, http).send('wa:c1', Outgoing(text='x'))
    assert str(caught.value) == 'WhatsApp недоступен: ConnectError' and TOKEN not in str(caught.value)


REVOKED = httpx.Response(401, json={'error': {'code': 190, 'message': 'expired'}})


@respx.mock
async def test__post__token_revoked__channel_failure_alerted_once_recovery_then_alerted_again(app):
    route = respx.post(URL).mock(side_effect=[REVOKED, REVOKED, REVOKED, OK, REVOKED])
    contact(app)
    async with httpx.AsyncClient() as http:
        sender = make_sender(app, http)
        for _ in range(3):
            # Сбой всего канала, а не получателя: напоминание должно вернуться в очередь.
            with pytest.raises(DeliveryError, match='190') as caught:
                await sender.send('wa:c1', Outgoing(text='x'))
            assert not isinstance(caught.value, RecipientGoneError)
        assert route.call_count == 3  # повтором внутри отправки токен не чинится
        await sender.send('wa:c1', Outgoing(text='fixed'))
        with pytest.raises(DeliveryError):
            await sender.send('wa:c1', Outgoing(text='revoked again'))
    assert [text[:1] for text in app.operator.texts] == ['⚠', '✅', '⚠']
    assert 'отклонил токен' in app.operator.texts[0] and 'снова принимает токен' in app.operator.texts[1]
    assert len(app.events_of(MESSAGE_EVENT)) == 1


@respx.mock
async def test__template_rejected__channel_failure_alerted_recovered_on_next_template(app):
    rejected = httpx.Response(400, json={'error': {'code': 132001, 'message': 'no template'}})
    respx.post(URL).mock(side_effect=[rejected, OK])
    contact(app)
    app.store.set_consent('wa:c1', subscribed=True, now=app.clock())
    app.clock.advance(dt.timedelta(hours=30))
    async with httpx.AsyncClient() as http:
        sender = make_sender(app, http)
        with pytest.raises(DeliveryError) as caught:
            await sender.send('wa:c1', reminder())
        assert not isinstance(caught.value, RecipientGoneError)
        await sender.send('wa:c1', reminder())
    assert 'шаблон booking_reminder не отправлен' in app.operator.texts[0]
    assert app.operator.texts[1] == '✅ Test Barbers / booking: шаблон booking_reminder снова отправляется'


@respx.mock
async def test__post__error_body_not_json__no_code_recipient_gone_no_token_alarm(app):
    respx.post(URL).mock(return_value=httpx.Response(400, text='<html>'))
    contact(app)
    async with httpx.AsyncClient() as http:
        with pytest.raises(RecipientGoneError, match=r'\(—\): HTTP 400'):
            await make_sender(app, http).send('wa:c1', Outgoing(text='x'))
    assert app.operator.texts == []


@respx.mock
@pytest.mark.parametrize(
    'response',
    [httpx.Response(503, text='<html>Service Unavailable</html>'), httpx.Response(429), httpx.Response(502, json={})],
)
async def test__post__outage_without_graph_error__retryable_no_token_alarm(app, response):
    # Код 0 у Graph — это AuthException; тело не от Graph (HTML прокси, пустой 429) кода не имеет.
    route = respx.post(URL).mock(return_value=response)
    contact(app)
    async with httpx.AsyncClient() as http:
        with pytest.raises(DeliveryError, match='—') as caught:
            await make_sender(app, http).send('wa:c1', Outgoing(text='x'))
    assert not isinstance(caught.value, RecipientGoneError) and route.call_count == 2
    assert app.operator.texts == []


@respx.mock
async def test__reminder__token_revoked_then_fixed__released_and_delivered_next_pass(app):
    from autostudio.booking.reminders import send_due_reminders
    from tests.booking.helpers import WED, local

    route = respx.post(URL).mock(side_effect=[REVOKED, OK])
    contact(app)
    app.store.set_consent('wa:c1', subscribed=True, now=app.clock())
    app.book_directly('wa:c1', 'haircut', 'giorgi', local(WED, 17))
    app.clock.set(local(WED, 15, 1))  # вне 24-часового окна: напоминание уходит шаблоном

    async def run_pass() -> int:
        async with httpx.AsyncClient() as http:
            sender = make_sender(app, http)
            return await send_due_reminders(
                app.store, sender, app.clock(), config=app.config, texts=app.texts, events=app.events
            )

    assert await run_pass() == 0
    app.clock.advance(dt.timedelta(minutes=5))  # оператор заменил токен
    assert await run_pass() == 1
    assert route.call_count == 2 and sent_json(route)[1]['type'] == 'template'
    assert len(app.events_of('booking.reminded')) == 1


def long_list() -> Outgoing:
    rows = [[Button(f'{h:02d}:00', f't:b:haircut:_:2026102{h % 10}0000')] for h in range(8, 20)]
    return Outgoing(text='Pick a time', buttons=rows)


@respx.mock
async def test__more__stored_page__next_rows_sent_unknown_or_expired_refused(app):
    route = respx.post(URL).mock(return_value=OK)
    contact(app)
    async with httpx.AsyncClient() as http:
        sender = make_sender(app, http)
        assert not await sender.more('wa:c1', 9)  # страниц ещё нет
        await sender.send('wa:c1', long_list())
        first = sent_json(route)[0]['interactive']['action']['sections'][0]['rows']
        assert len(first) == 10 and first[-1]['id'] == f'{MORE_PREFIX}9'
        assert await sender.more('wa:c1', 9)
        second = sent_json(route)[1]['interactive']
        assert second['body']['text'] == 'Please choose:' and len(second['action']['sections'][0]['rows']) == 3
        assert not await sender.more('wa:c1', 99)
        app.clock.advance(dt.timedelta(hours=1))
        assert not await sender.more('wa:c1', 9)


def sign(body: bytes) -> str:
    return 'sha256=' + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class Harness:
    def __init__(self, app: App, http: httpx.AsyncClient) -> None:
        self.app = app
        self.owner = Recorder()
        self.wa = make_sender(app, http)
        self.alarm = alarm_for(app)
        relay = Relay(
            engine=app.engine,
            sender=RoutingSender({'wa': self.wa, 'tg': self.owner}),
            texts=app.texts,
            admins=['tg:999'],
            notifier=None,
            alarm=self.alarm,
        )
        self.bot = WhatsAppBot(
            config=app.config,
            relay=relay,
            wa_sender=self.wa,
            store=app.store,
            texts=app.texts,
            app_secret=SECRET,
            verify_token=VERIFY,
            alarm=self.alarm,
            clock=app.clock,
        )
        self.count = 0

    def body(self, *messages: dict, statuses: list[dict] | None = None) -> bytes:
        enriched = []
        for message in messages:
            self.count += 1
            enriched.append(
                {
                    'id': f'wamid.in{self.count}',
                    'from': '995599000111',
                    'from_user_id': 'GE.1',
                    'timestamp': str(int(self.app.clock().timestamp())),
                    **message,
                }
            )
        value = {
            'messaging_product': 'whatsapp',
            'contacts': [{'wa_id': '995599000111', 'user_id': 'GE.1', 'profile': {'name': 'Nino'}}],
            'messages': enriched,
            'statuses': statuses or [],
        }
        raw = {'object': 'whatsapp_business_account', 'entry': [{'changes': [{'field': 'messages', 'value': value}]}]}
        return msgspec.json.encode(raw)

    async def feed(self, *messages: dict, statuses: list[dict] | None = None) -> None:
        await self.bot.handle_payload(msgspec.json.decode(self.body(*messages, statuses=statuses), type=WebhookPayload))

    async def post(self, client: TestClient, body: bytes, signature: str | None = None) -> int:
        headers = {'X-Hub-Signature-256': signature if signature is not None else sign(body)}
        response = await client.post('/wa', data=body, headers=headers)
        await self.bot.drain()
        return response.status


def web_app(harness: Harness) -> web.Application:
    application = web.Application()
    harness.bot.routes(application, '/wa')
    return application


@respx.mock
async def test__webhook_get__right_token_challenge_wrong_403(app):
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        async with TestClient(TestServer(web_app(harness))) as client:
            ok = await client.get(
                '/wa', params={'hub.mode': 'subscribe', 'hub.verify_token': VERIFY, 'hub.challenge': '42'}
            )
            assert ok.status == 200 and await ok.text() == '42'
            wrong = await client.get('/wa', params={'hub.mode': 'subscribe', 'hub.verify_token': 'nope'})
            assert wrong.status == 403
            mode = await client.get('/wa', params={'hub.mode': 'unsubscribe', 'hub.verify_token': VERIFY})
            assert mode.status == 403


@respx.mock
async def test__webhook_post__signature_checked_before_parsing__duplicates_once(app):
    route = respx.post(URL).mock(return_value=OK)
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        async with TestClient(TestServer(web_app(harness))) as client:
            body = harness.body({'type': 'text', 'text': {'body': '/start'}})
            assert await harness.post(client, body, signature='sha256=' + '0' * 64) == 403
            assert await harness.post(client, b'not json', signature=sign(b'not json')) == 400
            assert not route.called
            assert await harness.post(client, body) == 200
            assert await harness.post(client, body) == 200  # повторная доставка того же wamid
    # Приветствие и меню склеены в одно сообщение: в WhatsApp каждое сообщение платное.
    [reply] = sent_json(route)
    assert reply['recipient'] == 'GE.1' and reply['interactive']['type'] == 'list'
    assert reply['interactive']['body']['text'].startswith("Hi, Nino! I'm the booking assistant of Test Barbers.")
    key = app.store.contact(app.store.touch_contact('whatsapp', user_id='GE.1', phone=None, at=START, new_key='x').key)
    assert key is not None and key.key.startswith('wa:') and key.phone == '995599000111'


@respx.mock
async def test__webhook_post__wrong_app_secret__operator_alerted_once_then_recovery(app):
    respx.post(URL).mock(return_value=OK)
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        async with TestClient(TestServer(web_app(harness))) as client:
            body = harness.body({'type': 'text', 'text': {'body': 'hi'}})
            foreign = 'sha256=' + hmac.new(b'other-app-secret', body, hashlib.sha256).hexdigest()
            assert await harness.post(client, body, signature=foreign) == 403
            assert await harness.post(client, body, signature=foreign) == 403
            assert len(app.operator.texts) == 1 and 'App Secret' in app.operator.texts[0]
            assert await harness.post(client, body) == 200
    assert app.operator.texts[1] == (
        '✅ Test Barbers / booking: подпись вебхука WhatsApp снова сходится — сообщения доходят'
    )


@respx.mock
async def test__webhook_post__scanner_junk__403_without_alarm(app):
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        async with TestClient(TestServer(web_app(harness))) as client:
            body = harness.body({'type': 'text', 'text': {'body': 'hi'}})
            assert (await client.post('/wa', data=body)).status == 403  # без подписи
            assert await harness.post(client, body, signature='sha256=nothex') == 403
            assert await harness.post(client, b'{"x": 1}', signature='sha256=' + 'a' * 64) == 403
            assert await harness.post(client, b'<html>', signature='sha256=' + 'a' * 64) == 403
    assert app.operator.texts == []


async def spy_on(app: App) -> list[Incoming]:
    seen: list[Incoming] = []

    async def spy(incoming: Incoming) -> list[Outgoing]:
        seen.append(incoming)
        return []

    app.engine.handle = spy
    return seen


@respx.mock
async def test__webhook__replies_mapped_to_engine_input(app):
    respx.post(URL).mock(return_value=OK)
    seen = await spy_on(app)
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        messages = [
            {
                'type': 'interactive',
                'interactive': {'type': 'button_reply', 'button_reply': {'id': OWN_PHONE_ID, 'title': 'Use'}},
            },
            {
                'type': 'interactive',
                'interactive': {
                    'type': 'button_reply',
                    'button_reply': {'id': f'{KEYBOARD_PREFIX}✖️ Cancel', 'title': 'x'},
                },
            },
            {
                'type': 'interactive',
                'interactive': {'type': 'list_reply', 'list_reply': {'id': 'sv:haircut', 'title': 'H'}},
            },
            {'type': 'contacts', 'contacts': [{'phones': [{'phone': '+995 599 00 01 11', 'wa_id': '995599000111'}]}]},
            {'type': 'text', 'text': {'body': 'from an ad'}, 'referral': {'source_type': 'ad'}},
            {
                'type': 'interactive',
                'interactive': {'type': 'list_reply', 'list_reply': {'id': f'{MORE_PREFIX}9', 'title': 'More'}},
            },
            {'type': 'reaction', 'reaction': {'emoji': '👍'}},
        ]
        await harness.feed(*messages)
    phone, keyboard, data, shared, ad, more = seen
    assert (phone.phone, phone.phone_verified) == ('+995599000111', True)
    assert keyboard.text == '✖️ Cancel' and data.data == 'sv:haircut'
    assert (shared.phone, shared.phone_verified) == ('+995 599 00 01 11', True)
    assert ad.text == 'from an ad' and ad.source == 'wa_ad'
    # «Ещё…» без сохранённой страницы (перезапуск) уходит движку — там это «кнопка устарела».
    assert more.data == f'{MORE_PREFIX}9'
    assert all(item.user == phone.user and item.name == 'Nino' for item in seen)


@respx.mock
async def test__webhook__stop_and_start__consent_toggled_engine_not_called(app):
    route = respx.post(URL).mock(return_value=OK)
    seen = await spy_on(app)
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        await harness.feed({'type': 'text', 'text': {'body': 'STOP'}})
        [key] = _keys(app)
        stored = app.store.contact(key)
        assert not seen and stored.opted_out_at is not None and not stored.subscribed
        app.clock.advance(dt.timedelta(minutes=1))
        await harness.feed({'type': 'text', 'text': {'body': 'Start'}})
        assert app.store.contact(key).subscribed
        # START без прежней отписки — обычное сообщение для движка.
        await harness.feed({'type': 'text', 'text': {'body': 'start'}})
        assert [item.text for item in seen] == ['start']
    texts = [body['text']['body'] for body in sent_json(route)]
    assert texts[0].startswith("Done — Test Barbers won't send you reminders") and 'turn them back on' in texts[0]
    assert texts[1] == 'Reminders from Test Barbers are on again.'


@respx.mock
async def test__webhook__stop_reply_undeliverable__logged_not_raised(app, caplog):
    respx.post(URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        await Harness(app, http).feed({'type': 'text', 'text': {'body': 'stop'}})
    assert 'ответ на STOP/START не доставлен' in caplog.text


def _keys(app: App) -> list[str]:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(app.store.path)) as conn:
        return [row[0] for row in conn.execute('SELECT key FROM channel_contacts')]


@respx.mock
async def test__webhook__confirm_pressed__opt_in_recorded(app):
    respx.post(URL).mock(return_value=OK)
    await spy_on(app)
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        confirm = f'{cb.CONFIRM}:b:haircut:_:202610211200'
        reply = {
            'type': 'interactive',
            'interactive': {'type': 'button_reply', 'button_reply': {'id': confirm, 'title': '✅'}},
        }
        await harness.feed(reply)
    [key] = _keys(app)
    assert app.store.contact(key).subscribed


@respx.mock
async def test__webhook__engine_crash_and_bad_message__apology_alarm_next_message_processed(app, caplog):
    route = respx.post(URL).mock(return_value=OK)

    async def broken(incoming):
        raise RuntimeError('bug')

    app.engine.handle = broken
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        anonymous = {'type': 'text', 'text': {'body': 'no sender'}, 'from': None, 'from_user_id': None}
        await harness.feed(anonymous, {'type': 'text', 'text': {'body': 'hi'}})
    [apology] = sent_json(route)
    assert apology['text']['body'].startswith('Sorry, something went wrong')
    assert app.operator.texts == ['⚠️ Test Barbers / booking: сбой обработки сообщения: RuntimeError']
    assert 'сообщение WhatsApp не обработано' in caplog.text


@respx.mock
async def test__webhook__failed_template_status__operator_alerted_at_most_hourly(app):
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        statuses = [{'id': 'out', 'status': 'failed', 'errors': [{'code': 132015, 'title': 'Template is paused'}]}]
        await harness.feed(statuses=statuses)
        await harness.feed(statuses=statuses)
        assert len(app.operator.texts) == 1 and 'отклонил шаблон' in app.operator.texts[0]
        # Восстановление по статусам не видно — поэтому сигнал не «один на жизнь процесса», а раз в час.
        app.clock.advance(dt.timedelta(minutes=61))
        await harness.feed(statuses=statuses)
    assert len(app.operator.texts) == 2


def test__sender__without_whatsapp_section__refused(tmp_path):
    plain = App(tmp_path, make_config())
    with pytest.raises(ValueError, match=r'booking\.whatsapp'):
        make_sender(plain, httpx.AsyncClient())


@respx.mock
async def test__webhook__more_row__next_page_from_sender_engine_called_once(app):
    route = respx.post(URL).mock(return_value=OK)
    calls: list[Incoming] = []

    async def long_menu(incoming: Incoming) -> list[Outgoing]:
        calls.append(incoming)
        return [long_list()]

    app.engine.handle = long_menu
    async with httpx.AsyncClient() as http:
        harness = Harness(app, http)
        await harness.feed({'type': 'text', 'text': {'body': 'times?'}})
        more = {
            'type': 'interactive',
            'interactive': {'type': 'list_reply', 'list_reply': {'id': f'{MORE_PREFIX}9', 'title': 'More…'}},
        }
        await harness.feed(more)
    first, second = sent_json(route)
    assert first['interactive']['action']['sections'][0]['rows'][-1]['id'] == f'{MORE_PREFIX}9'
    rows = second['interactive']['action']['sections'][0]['rows']
    assert rows[0]['id'] == long_list().buttons[9][0].data
    assert len(calls) == 1

"""Адаптер WhatsApp Cloud API: вебхук (aiohttp) → движок → Graph API.

Тот же движок, что у Telegram; здесь — только то, что специфично для WhatsApp:

- **Вебхук.** GET — проверка подписки (`hub.mode`, `hub.verify_token`, `hub.challenge`);
  POST — сначала подпись `X-Hub-Signature-256` по сырому телу (HMAC-SHA256 ключом App
  Secret, сравнение за постоянное время), и только потом разбор JSON. Ответ 200 уходит
  сразу, обработка — в фоне; повторная доставка того же сообщения (Meta повторяет при
  любом сбое) отсеивается по `wamid`.
- **Кто пишет.** Ключ покупателя в движке — наш случайный `wa:<hex>`; адрес отправки
  (BSUID `from_user_id` или телефон `wa_id`) хранится отдельно (`store.touch_contact`). Так в
  логах нет номеров, а переход Meta на BSUID и usernames не меняет ключ, к которому
  привязаны записи.
- **Окно 24 часа.** Внутри окна (покупатель писал меньше 24 часов назад) — обычные
  сообщения: с 01.10.2026 они платные по тарифу utility. Вне окна бизнес может написать
  только одобренным шаблоном — так уходят напоминания (`reminder_template`); остальное вне
  окна не отправляется, а администратор видит «не доставлено».
- **Согласие.** Напоминание получают только те, кто написал первым и подтвердил запись под
  текстом согласия; «STOP» отписывает, «START» — возвращает.
- **Счёт сообщений.** Каждое отправленное сообщение — событие `booking.whatsapp_message`
  (обычное или шаблон): месячный отчёт показывает объём, за который клиент платит Meta.
- Бот — помощник записи и FAQ конкретного бизнеса (с 15.01.2026 Meta запрещает в WhatsApp
  ассистентов общего назначения): движок отвечает только по услугам, графику и FAQ.

HTTPS даёт обратный прокси (см. `server.py`): Meta шлёт вебхуки только на HTTPS.
"""

import asyncio
import datetime as dt
import hmac
import logging
import secrets
from collections import OrderedDict
from typing import Final

import httpx
import msgspec
from aiohttp import web

from autostudio.booking import callbacks as cb
from autostudio.booking.alarm import OperatorAlarm
from autostudio.booking.config import BookingConfig, WhatsAppSection
from autostudio.booking.messages import DeliveryError, Incoming, Outgoing, RecipientGoneError, coalesce
from autostudio.booking.relay import Relay
from autostudio.booking.service import PACKAGE
from autostudio.booking.store import BookingStore, ChannelContact
from autostudio.booking.texts import Texts
from autostudio.booking.wa_format import (
    INTERACTIVE_BODY_MAX,
    KEYBOARD_PREFIX,
    MORE_PREFIX,
    OWN_PHONE_ID,
    SIGNATURE_HEADER,
    InboundMessage,
    Labels,
    Option,
    WaMessage,
    WebhookPayload,
    options_of,
    page_of,
    parse_webhook,
    render,
    template_message,
    verify_signature,
)
from autostudio.clock import Clock
from autostudio.events import EventLog

logger = logging.getLogger(__name__)

GRAPH_API: Final = 'https://graph.facebook.com'
KEY_PREFIX: Final = 'wa:'
CHANNEL: Final = 'whatsapp'
MESSAGE_EVENT: Final = 'booking.whatsapp_message'
# Окно обслуживания — 24 часа от последнего сообщения покупателя; запас на расхождение часов
# и задержку вебхука, чтобы не отправить «обычное» сообщение в первую секунду после окна.
SERVICE_WINDOW: Final = dt.timedelta(hours=24) - dt.timedelta(minutes=10)
REQUEST_TIMEOUT_S: Final = 15.0
_RETRY_DELAY_S: Final = 1.0
_SEEN_MAX: Final = 2000
_PAGES_MAX: Final = 1000
_PAGE_TTL: Final = dt.timedelta(minutes=30)
# Коды Meta: перегрузка и лимиты (повторить позже) — пропускная способность номера,
# пара «бизнес — пользователь» (одно сообщение в 6 секунд), временные сбои платформы.
RETRYABLE_CODES: Final = frozenset({1, 2, 4, 80007, 130429, 131000, 131016, 131056})
# Токен отозван или нет прав — чинит оператор, а не следующая попытка.
AUTH_CODES: Final = frozenset({0, 10, 190, 200})
STOP_WORDS: Final = frozenset({'stop', 'стоп', 'unsubscribe', 'отписаться'})
START_WORDS: Final = frozenset({'start', 'старт', 'subscribe', 'подписаться'})


def labels_for(texts: Texts, language: str, business: str) -> Labels:
    """Подписи адаптера на языке покупателя."""
    return Labels(
        list_button=texts.get(language, 'wa_list_button'),
        section=texts.get(language, 'wa_section'),
        more=texts.get(language, 'wa_more'),
        choose=texts.get(language, 'wa_choose'),
        use_my_number=texts.get(language, 'wa_use_my_number'),
        opt_in=texts.get(language, 'wa_opt_in', business=business),
    )


class _GraphError(msgspec.Struct):
    code: int = 0
    message: str = ''


class _GraphResponse(msgspec.Struct):
    error: _GraphError | None = None


class _Page(msgspec.Struct, frozen=True, kw_only=True):
    message: Outgoing
    language: str
    phone_button: bool
    expires_at: dt.datetime


class WhatsAppSender:
    """`Sender` для ключей `wa:`: окно 24 часа, шаблоны напоминаний, страницы списков, счёт сообщений.

    Args:
        http: Общий httpx-клиент.
        config: Конфиг клиента (секция `[booking.whatsapp]` обязательна).
        token: Токен system user клиента.
        store: База (адреса покупателей, окно, согласие).
        texts: Тексты.
        events: Журнал событий (счёт сообщений для отчёта).
        alarm: Сигналы оператору (токен отозван, шаблон отклонён).
        clock: Часы.
        retry_delay: Пауза перед единственным повтором при перегрузке, секунды.
    """

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        config: BookingConfig,
        token: str,
        store: BookingStore,
        texts: Texts,
        events: EventLog,
        alarm: OperatorAlarm,
        clock: Clock,
        retry_delay: float = _RETRY_DELAY_S,
    ) -> None:
        section = config.booking.whatsapp
        if section is None:
            raise ValueError('WhatsAppSender без [booking.whatsapp]')
        self._section: WhatsAppSection = section
        self._config = config
        self._http = http
        self._token = token
        self._store = store
        self._texts = texts
        self._events = events
        self._alarm = alarm
        self._clock = clock
        self._retry_delay = retry_delay
        self._pages: OrderedDict[str, _Page] = OrderedDict()
        self._url = f'{GRAPH_API}/{section.graph_version}/{section.phone_number_id}/messages'

    def language_of(self, chat: str) -> str:
        """Язык покупателя: из профиля, иначе язык по умолчанию (WhatsApp язык клиента не сообщает)."""
        profile = self._store.customer(chat)
        language = profile.language if profile is not None else None
        return language if language in self._config.booking.languages else self._config.booking.default_language

    def _labels(self, language: str) -> Labels:
        return labels_for(self._texts, language, self._config.client.name)

    def in_window(self, contact: ChannelContact, now: dt.datetime) -> bool:
        """Открыто ли 24-часовое окно обслуживания (покупатель писал недавно)."""
        return now - contact.last_inbound_at < SERVICE_WINDOW

    async def send(self, chat: str, message: Outgoing) -> None:
        contact = self._store.contact(chat)
        if contact is None:
            raise RecipientGoneError(f'неизвестный покупатель WhatsApp {chat}')
        now = self._clock()
        if message.reminder is not None:
            if not contact.subscribed:
                raise RecipientGoneError(f'{chat}: нет согласия на напоминания или отписался')
            if not self.in_window(contact, now):
                await self._send_template(contact, message)
                return
        elif not self.in_window(contact, now):
            # Вне окна обычное сообщение Meta не доставит (131047): честное «не доставлено»
            # администратору лучше платной попытки, которая молча пропадёт.
            raise RecipientGoneError(f'{chat}: прошло больше 24 часов с последнего сообщения покупателя')
        language = self.language_of(chat)
        phone_button = contact.phone is not None
        payloads = render(message, labels=self._labels(language), phone_button=phone_button)
        self._remember_pages(chat, message, language, phone_button, now)
        for payload in payloads:
            await self._post(contact, payload, kind='service', purpose='reminder' if message.reminder else 'reply')

    async def more(self, chat: str, offset: int) -> bool:
        """Следующая страница длинного списка («Ещё…»).

        Args:
            chat: Ключ покупателя.
            offset: Первый вариант страницы.

        Returns:
            False, если страницы нет (истекла, перезапуск) — тогда нажатие обработает движок.
        """
        now = self._clock()
        page = self._pages.get(chat)
        contact = self._store.contact(chat)
        if page is None or page.expires_at <= now or contact is None:
            return False
        labels = self._labels(page.language)
        options = options_of(page.message, phone_button=page.phone_button, labels=labels)
        if not 0 < offset < len(options):
            return False
        [payload] = render(page.message, labels=labels, phone_button=page.phone_button, offset=offset)
        await self._post(contact, payload, kind='service', purpose='reply')
        return True

    def _remember_pages(self, chat: str, message: Outgoing, language: str, phone_button: bool, now: dt.datetime) -> None:
        options: list[Option] = options_of(message, phone_button=phone_button, labels=self._labels(language))
        if not page_of(options, 0, self._labels(language))[1]:
            return
        self._pages[chat] = _Page(
            message=message, language=language, phone_button=phone_button, expires_at=now + _PAGE_TTL
        )
        self._pages.move_to_end(chat)
        while len(self._pages) > _PAGES_MAX:
            self._pages.popitem(last=False)

    async def _send_template(self, contact: ChannelContact, message: Outgoing) -> None:
        reminder = message.reminder
        name = self._section.reminder_template
        if reminder is None or name is None:
            raise RecipientGoneError(f'{contact.key}: вне 24-часового окна, а шаблон напоминания не настроен')
        language = self._section.template_language(reminder.language)
        payload = template_message(name, language, [reminder.service, reminder.when])
        await self._post(contact, payload, kind='template', purpose='reminder')

    async def _post(self, contact: ChannelContact, payload: WaMessage, *, kind: str, purpose: str) -> None:
        # По BSUID, если он известен: у пользователя с username телефона может не быть.
        if contact.user_id:
            addressed = msgspec.structs.replace(payload, recipient=contact.user_id)
        else:
            addressed = msgspec.structs.replace(payload, to=contact.phone)
        body = msgspec.json.encode(addressed)
        headers = {'Authorization': f'Bearer {self._token}', 'Content-Type': 'application/json'}
        for attempt in range(2):
            try:
                response = await self._http.post(self._url, content=body, headers=headers, timeout=REQUEST_TIMEOUT_S)
            except httpx.HTTPError as exc:
                if attempt:
                    raise DeliveryError(f'WhatsApp недоступен: {type(exc).__name__}') from None
                await asyncio.sleep(self._retry_delay)
                continue
            if response.is_success:
                self._events.record(PACKAGE, MESSAGE_EVENT, self._clock(), {'kind': kind, 'purpose': purpose})
                return
            code, text = _graph_error(response)
            retryable = response.status_code >= 500 or response.status_code == 429 or code in RETRYABLE_CODES
            if retryable and not attempt:
                await asyncio.sleep(self._retry_delay)
                continue
            await self._failed(code, text, response.status_code, kind)
            if retryable:
                raise DeliveryError(f'WhatsApp {response.status_code} ({code}): {text}')
            # Остальное (неверный параметр, получатель недоступен, шаблон отклонён) повтором
            # не лечится: повтор напоминания каждую минуту был бы спамом и расходом.
            raise RecipientGoneError(f'WhatsApp {response.status_code} ({code}): {text}')

    async def _failed(self, code: int, text: str, status: int, kind: str) -> None:
        if status in (401, 403) or code in AUTH_CODES:
            await self._alarm.raise_(
                'whatsapp_auth', f'WhatsApp отклонил токен ({code}: {text}) — проверьте токен system user'
            )
        elif kind == 'template' and 132000 <= code < 133000:
            await self._alarm.raise_(
                'whatsapp_template',
                f'шаблон {self._section.reminder_template} не отправлен ({code}: {text}) — проверьте имя, язык, '
                'статус одобрения и число параметров',
            )


def _graph_error(response: httpx.Response) -> tuple[int, str]:
    try:
        error = msgspec.json.decode(response.content, type=_GraphResponse).error
    except msgspec.DecodeError:
        return 0, f'HTTP {response.status_code}'
    if error is None:
        return 0, f'HTTP {response.status_code}'
    return error.code, error.message[:200]


class WhatsAppBot:
    """Вебхук WhatsApp: проверка, разбор, отсев повторов, движок и доставка.

    Args:
        config: Конфиг клиента.
        relay: Общий путь «движок → доставка».
        wa_sender: Отправка в WhatsApp (страницы списков, ответы на STOP/START).
        store: База (контакты, согласие).
        texts: Тексты.
        app_secret: App Secret приложения Meta (подпись POST).
        verify_token: Строка проверки подписки (GET).
        alarm: Сигналы оператору.
        clock: Часы.
    """

    def __init__(
        self,
        *,
        config: BookingConfig,
        relay: Relay,
        wa_sender: WhatsAppSender,
        store: BookingStore,
        texts: Texts,
        app_secret: str,
        verify_token: str,
        alarm: OperatorAlarm,
        clock: Clock,
    ) -> None:
        self._config = config
        self._relay = relay
        self._wa = wa_sender
        self._store = store
        self._texts = texts
        self._app_secret = app_secret
        self._verify_token = verify_token
        self._alarm = alarm
        self._clock = clock
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._tasks: set[asyncio.Task[None]] = set()

    def routes(self, app: web.Application, path: str) -> None:
        """Регистрирует GET (проверка подписки) и POST (сообщения) на `path`."""
        app.router.add_get(path, self.verify)
        app.router.add_post(path, self.receive)

    async def verify(self, request: web.Request) -> web.Response:
        """GET: Meta проверяет, что вебхук наш, и ждёт `hub.challenge` в ответ."""
        query = request.query
        given = query.get('hub.verify_token', '').encode()
        if query.get('hub.mode') == 'subscribe' and hmac.compare_digest(given, self._verify_token.encode()):
            return web.Response(text=query.get('hub.challenge', ''))
        logger.warning('проверка вебхука WhatsApp не прошла: неверный verify_token или mode')
        return web.Response(status=403)

    async def receive(self, request: web.Request) -> web.Response:
        """POST: подпись по сырому телу, затем разбор; обработка — в фоне, ответ 200 — сразу."""
        body = await request.read()
        if not verify_signature(self._app_secret, body, request.headers.get(SIGNATURE_HEADER)):
            logger.warning('вебхук WhatsApp с неверной подписью отброшен')
            return web.Response(status=403)
        try:
            payload = msgspec.json.decode(body, type=WebhookPayload)
        except msgspec.DecodeError:
            return web.Response(status=400)
        task = asyncio.get_running_loop().create_task(self.handle_payload(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return web.Response(status=200)

    async def drain(self) -> None:
        """Дожидается фоновой обработки (остановка, демо, тесты)."""
        while self._tasks:
            await asyncio.wait(set(self._tasks))

    async def handle_payload(self, payload: WebhookPayload) -> None:
        """Обрабатывает разобранный вебхук: сообщения по порядку, сбои доставки — в лог."""
        messages, failures = parse_webhook(payload)
        for failure in failures:
            logger.warning('WhatsApp не доставил сообщение (%s: %s)', failure.code, failure.title)
            if 132000 <= failure.code < 133000:
                await self._alarm.raise_(
                    'whatsapp_template', f'WhatsApp отклонил шаблон напоминания ({failure.code}: {failure.title})'
                )
        for message in messages:
            if self._duplicate(message.message_id) or message.ignored:
                continue
            try:
                await self._handle(message)
            except Exception:
                # Сбой на одном сообщении не должен терять остальные из того же вебхука.
                logger.exception('сообщение WhatsApp не обработано')

    def _duplicate(self, message_id: str) -> bool:
        if message_id in self._seen:
            return True
        self._seen[message_id] = None
        while len(self._seen) > _SEEN_MAX:
            self._seen.popitem(last=False)
        return False

    async def _handle(self, message: InboundMessage) -> None:
        now = self._clock()
        sent_at = min(message.sent_at or now, now)
        contact = self._store.touch_contact(
            CHANNEL, user_id=message.user_id, phone=message.phone, at=sent_at, new_key=f'{KEY_PREFIX}{secrets.token_hex(8)}'
        )
        key = contact.key
        word = message.text.strip().lower() if message.reply_id is None else ''
        if word in STOP_WORDS:
            self._store.set_consent(key, subscribed=False, now=now)
            await self._notice(key, 'wa_opted_out')
            return
        if word in START_WORDS and contact.opted_out_at is not None and not contact.subscribed:
            self._store.set_consent(key, subscribed=True, now=now)
            await self._notice(key, 'wa_opted_in')
            return
        reply = message.reply_id
        if reply is not None and reply.startswith(MORE_PREFIX):
            raw = reply.removeprefix(MORE_PREFIX)
            if raw.isascii() and raw.isdigit() and await self._wa.more(key, int(raw)):
                return
        incoming = self._incoming(key, contact, message)
        if incoming.data is not None and incoming.data.startswith(f'{cb.CONFIRM}:'):
            # Подтверждение записи нажато под текстом согласия (wa_format.render): это opt-in.
            self._store.set_consent(key, subscribed=True, now=now)
        await self._relay.process(incoming, shape=lambda replies: coalesce(replies, INTERACTIVE_BODY_MAX))

    def _incoming(self, key: str, contact: ChannelContact, message: InboundMessage) -> Incoming:
        base = Incoming(user=key, name=message.name, source=f'wa_{message.referral}' if message.referral else None)
        reply = message.reply_id
        if reply == OWN_PHONE_ID and contact.phone:
            # Номер отправителя подтверждён самим WhatsApp — как «Отправить мой номер» в Telegram.
            return msgspec.structs.replace(base, phone=f'+{contact.phone.lstrip("+")}', phone_verified=True)
        if reply is not None and reply.startswith(KEYBOARD_PREFIX):
            return msgspec.structs.replace(base, text=reply.removeprefix(KEYBOARD_PREFIX))
        if reply is not None:
            return msgspec.structs.replace(base, data=reply)
        if message.shared_phone is not None:
            own = message.shared_own
            return msgspec.structs.replace(base, phone=message.shared_phone, phone_verified=own)
        return msgspec.structs.replace(base, text=message.text)

    async def _notice(self, key: str, text_key: str) -> None:
        text = self._texts.get(self._wa.language_of(key), text_key, business=self._config.client.name)
        try:
            await self._wa.send(key, Outgoing(text=text))
        except DeliveryError as exc:
            logger.warning('ответ на STOP/START не доставлен: %s', exc)

"""Адаптер Telegram (aiogram 3): апдейты → `Incoming`, `Outgoing` → разметка Telegram.

Тонкий слой: вся логика — в движке. Здесь только то, что специфично для Telegram:
ключ чата `tg:<id>`, кто администратор (по `admin_chat_ids`), инлайн-кнопки,
клавиатура «Отправить мой номер», ответ на нажатие кнопки, ошибки Bot API.

В группах бот слушает только служебные команды и только в группах из
`admin_chat_ids` (общий чат сотрудников): расписание с именами и телефонами не должно
попасть в семейный или публичный чат, куда кто-то добавил бота, даже если команду
набрал владелец.

Deep link `t.me/<bot>?start=instagram` приходит как `/start instagram`: метка уходит в
`Incoming.source`, а движок запоминает её — владелец видит в отчёте, какой канал приводит
записи. Запуск (long polling или вебхук) — в `server.py`.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Final

from aiogram import Bot, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    User,
)

from autostudio.booking.admin import ADMIN_COMMANDS, AdminDesk
from autostudio.booking.alarm import OperatorAlarm
from autostudio.booking.callbacks import ADMIN_CANCEL, split
from autostudio.booking.config import BookingConfig
from autostudio.booking.engine import Engine
from autostudio.booking.messages import DeliveryError, Incoming, Outgoing, RecipientGoneError, Sender
from autostudio.booking.relay import Relay
from autostudio.booking.texts import Texts
from autostudio.clock import Clock, system_now
from autostudio.notify import Notifier, clip

logger = logging.getLogger(__name__)

KEY_PREFIX: Final = 'tg:'
_RETRY_AFTER_MAX_S: Final = 30
_COMMANDS: Final = (('start', 'cmd_start'), ('book', 'cmd_book'), ('my', 'cmd_my'), ('cancel', 'cmd_cancel'))
# Апдейты, которые бот обрабатывает; остальные Telegram даже не присылает.
ALLOWED_UPDATES: Final = ['message', 'callback_query']

type Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove


def chat_key(chat_id: int) -> str:
    """Ключ чата для движка: `tg:123456`."""
    return f'{KEY_PREFIX}{chat_id}'


def chat_id_of(key: str) -> int:
    """Обратное к `chat_key`.

    Raises:
        RecipientGoneError: Ключ не из Telegram (например, консольный) — доставить некуда.
    """
    raw = key.removeprefix(KEY_PREFIX)
    if not key.startswith(KEY_PREFIX) or not raw.lstrip('-').isdigit():
        raise RecipientGoneError(f'не чат Telegram: {key!r}')
    return int(raw)


def render_markup(message: Outgoing) -> Markup | None:
    """Разметка Telegram для ответа движка (у сообщения может быть только одна)."""
    if message.buttons:
        rows = [[InlineKeyboardButton(text=b.label, callback_data=b.data) for b in row] for row in message.buttons]
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if message.request_contact is not None:
        keyboard = [[KeyboardButton(text=message.request_contact, request_contact=True)]]
        keyboard += [[KeyboardButton(text=label)] for label in message.keyboard]
        return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)
    if message.remove_keyboard:
        return ReplyKeyboardRemove()
    return None


def _describe(exc: TelegramAPIError) -> str:
    # В сетевых ошибках бывает URL запроса, а в нём — токен бота: показываем только тип.
    if isinstance(exc, TelegramNetworkError):
        return type(exc).__name__
    return f'{type(exc).__name__}: {exc.message}'


class TelegramSender:
    """`Sender` поверх `aiogram.Bot`.

    Args:
        bot: Бот aiogram.
    """

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat: str, message: Outgoing) -> None:
        target = chat_id_of(chat)
        markup = render_markup(message)
        for attempt in range(2):
            try:
                await self._bot.send_message(chat_id=target, text=clip(message.text), reply_markup=markup)
            except TelegramRetryAfter as exc:
                # Лимит Telegram: ждём, сколько сказано (но не вечно), и пробуем ещё раз.
                if attempt or exc.retry_after > _RETRY_AFTER_MAX_S:
                    raise DeliveryError(_describe(exc)) from None
                await asyncio.sleep(exc.retry_after)
                continue
            except (TelegramForbiddenError, TelegramNotFound, TelegramBadRequest) as exc:
                raise RecipientGoneError(_describe(exc)) from None
            except TelegramAPIError as exc:
                raise DeliveryError(_describe(exc)) from None
            return


class BookingBot:
    """Склейка Telegram и движка: апдейт → движок → доставка ответов.

    Args:
        config: Конфиг клиента.
        engine: Движок разговора.
        sender: Канал (обычно `TelegramSender`).
        texts: Тексты (извинение при сбое, описания команд).
        notifier: Дополнительный канал владельца из `[notify]` или None (консоль сюда не
            передаётся: в боевом режиме уведомления с именами и телефонами не пишутся в логи).
        operator: Канал оператора для сигнала о сбое или None.
        clock: Часы (ограничение частоты сигналов оператору).
        alarm: Сигналы оператору (по умолчанию — свои, поверх `operator`).
    """

    def __init__(
        self,
        *,
        config: BookingConfig,
        engine: Engine,
        sender: Sender,
        texts: Texts,
        notifier: Notifier | None = None,
        operator: Notifier | None = None,
        clock: Clock = system_now,
        alarm: OperatorAlarm | None = None,
    ) -> None:
        self._config = config
        self._texts = texts
        self._admin_ids = frozenset(config.booking.admin_chat_ids)
        self._alarm = alarm or OperatorAlarm(operator, f'{config.client.name} / booking', clock=clock)
        self._relay = Relay(
            engine=engine, sender=sender, texts=texts, admins=self.admin_keys, notifier=notifier, alarm=self._alarm
        )

    @property
    def notifier(self) -> Notifier | None:
        """Дополнительный канал владельца `[notify]` (в боевом режиме — никогда не консоль)."""
        return self._relay.notifier

    @property
    def admin_keys(self) -> list[str]:
        return [chat_key(chat_id) for chat_id in self._config.booking.admin_chat_ids]

    def is_admin(self, user_id: int, chat_id: int) -> bool:
        """Администратор — сам пользователь из списка или любой участник чата из списка."""
        return user_id in self._admin_ids or chat_id in self._admin_ids

    @staticmethod
    def _who(user: User) -> tuple[str, str | None]:
        return user.first_name or '', f'@{user.username}' if user.username else None

    def incoming_from_message(self, message: Message) -> Incoming | None:
        """Сообщение Telegram → `Incoming`; None — игнорировать (бот, чужая группа)."""
        user = message.from_user
        if user is None or user.is_bot:
            return None
        admin = self.is_admin(user.id, message.chat.id)
        text = message.text
        if message.chat.type != 'private':
            command = AdminDesk.parse_command(text)[0] if text and text.startswith('/') else ''
            if message.chat.id not in self._admin_ids or command not in ADMIN_COMMANDS:
                return None
        name, handle = self._who(user)
        contact = message.contact
        return Incoming(
            user=chat_key(message.chat.id),
            name=name,
            handle=handle,
            language=user.language_code,
            is_admin=admin,
            text=text if text is not None else '',
            phone=contact.phone_number if contact is not None else None,
            # Карточку контакта можно переслать чужую: «свой номер» — только когда Telegram
            # говорит, что карточка принадлежит отправителю.
            phone_verified=contact is not None and contact.user_id == user.id,
            source=deep_link_payload(text) if message.chat.type == 'private' else None,
        )

    def incoming_from_callback(self, query: CallbackQuery) -> Incoming | None:
        """Нажатие кнопки → `Incoming`; None — игнорировать."""
        user = query.from_user
        chat_id = query.message.chat.id if query.message is not None else user.id
        private = query.message is None or query.message.chat.type == 'private'
        admin = self.is_admin(user.id, chat_id)
        data = query.data or ''
        if not private and not (chat_id in self._admin_ids and split(data)[0] == ADMIN_CANCEL):
            return None
        name, handle = self._who(user)
        return Incoming(
            user=chat_key(chat_id), name=name, handle=handle, language=user.language_code, is_admin=admin, data=data
        )

    async def process(self, incoming: Incoming) -> None:
        """Прогоняет событие через движок и доставляет ответы; сбой не оставляет покупателя без ответа."""
        await self._relay.process(incoming)

    async def on_message(self, message: Message) -> None:
        """Обработчик сообщений aiogram."""
        incoming = self.incoming_from_message(message)
        if incoming is not None:
            await self.process(incoming)

    async def on_callback(self, query: CallbackQuery) -> None:
        """Обработчик нажатий aiogram: снять «часики», убрать кнопки у старого сообщения, обработать."""
        incoming = self.incoming_from_callback(query)
        try:
            await query.answer()
        except TelegramAPIError as exc:
            logger.debug('answerCallbackQuery: %s', _describe(exc))
        if incoming is None:
            return
        if isinstance(query.message, Message) and query.message.reply_markup is not None:
            # Кнопки нажатого сообщения убираем: так меньше «устаревших» нажатий.
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError as exc:
                logger.debug('editMessageReplyMarkup: %s', _describe(exc))
        await self.process(incoming)

    def router(self) -> Router:
        """Router aiogram с обработчиками сообщений и нажатий."""
        router = Router(name='booking')
        router.message.register(self.on_message)
        router.callback_query.register(self.on_callback)
        return router

    def commands(self, language: str) -> list[BotCommand]:
        """Меню команд бота на языке покупателя."""
        return [BotCommand(command=name, description=self._texts.get(language, key)) for name, key in _COMMANDS]


def deep_link_payload(text: str | None) -> str | None:
    """Метка из `/start <payload>` (deep link `t.me/<bot>?start=<payload>`) как есть, или None.

    Проверку формата делает движок (`domain.clean_source`): адаптер только достаёт аргумент.
    """
    if not text or not text.startswith('/'):
        return None
    command, rest = AdminDesk.parse_command(text)
    if command != 'start' or not rest:
        return None
    return rest.split()[0]


async def set_commands(bot: Bot, booking_bot: BookingBot, languages: Sequence[str]) -> None:
    """Публикует меню команд: по языку клиента Telegram и по умолчанию."""
    try:
        await bot.set_my_commands(booking_bot.commands(languages[0]))
        for language in languages:
            await bot.set_my_commands(booking_bot.commands(language), language_code=language)
    except TelegramAPIError as exc:
        logger.warning('меню команд не опубликовано: %s', _describe(exc))

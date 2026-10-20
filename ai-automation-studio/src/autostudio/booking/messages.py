"""Сообщения движка: что пришло от покупателя и что ему ответить — без привязки к каналу.

Адаптер канала (Telegram, консоль, в будущем WhatsApp) переводит свои апдейты во
`Incoming`, а `Outgoing` — в свою разметку. Отправка спрятана за протоколом `Sender`:
тестам и напоминаниям не нужен настоящий бот.
"""

import logging
from collections.abc import Sequence
from typing import Final, Protocol

import msgspec

from autostudio.errors import AutoStudioError, NotifyError
from autostudio.notify import Notifier

logger = logging.getLogger(__name__)

# Telegram отвергает callback_data длиннее 64 байт — кнопка просто не отправится.
CALLBACK_DATA_LIMIT: Final = 64
# Адресат «все администраторы»: адаптер сам знает их чаты.
ADMINS: Final = '@admins'


class Button(msgspec.Struct, frozen=True):
    """Кнопка под сообщением.

    Attributes:
        label: Подпись.
        data: Что вернётся движку при нажатии; не длиннее 64 байт в UTF-8.
    """

    label: str
    data: str

    def __post_init__(self) -> None:
        size = len(self.data.encode('utf-8'))
        if not 0 < size <= CALLBACK_DATA_LIMIT:
            raise ValueError(f'данные кнопки {self.data!r}: {size} байт, допустимо 1–{CALLBACK_DATA_LIMIT}')


class Incoming(msgspec.Struct, frozen=True, kw_only=True):
    """Входящее событие от покупателя или администратора.

    Attributes:
        user: Ключ чата в канале (`tg:123456`): туда же уходит ответ.
        name: Отображаемое имя из канала (подсказка для «записать как …»).
        handle: Публичное имя (`@anna`) или None — для уведомления владельцу.
        language: Код языка клиента канала (`ru`, `en-US`) или None.
        is_admin: Отправитель — владелец или сотрудник (решает адаптер по `admin_chat_ids`).
        text: Текст сообщения.
        data: Данные нажатой кнопки.
        phone: Телефон из «Поделиться контактом».
    """

    user: str
    name: str = ''
    handle: str | None = None
    language: str | None = None
    is_admin: bool = False
    text: str | None = None
    data: str | None = None
    phone: str | None = None


class Outgoing(msgspec.Struct, frozen=True, kw_only=True):
    """Ответ движка.

    Attributes:
        text: Текст без разметки.
        buttons: Ряды кнопок под сообщением.
        request_contact: Подпись кнопки «Отправить мой номер» (клавиатура под полем ввода) или None.
        keyboard: Обычные кнопки той же клавиатуры (их подпись приходит текстом, например «Отмена»).
        remove_keyboard: Убрать такую клавиатуру.
        to: Кому: None — отправителю, `ADMINS` — администраторам, иначе ключ чата.
    """

    text: str
    buttons: list[list[Button]] = []
    request_contact: str | None = None
    keyboard: list[str] = []
    remove_keyboard: bool = False
    to: str | None = None


class Sender(Protocol):
    """Отправка сообщения в канал."""

    async def send(self, chat: str, message: Outgoing) -> None:
        """Отправляет сообщение.

        Args:
            chat: Ключ чата (`tg:123456`).
            message: Что отправить.

        Raises:
            DeliveryError: Сообщение не доставлено.
        """
        ...


class DeliveryError(AutoStudioError):
    """Сообщение не доставлено; можно повторить позже."""


class RecipientGoneError(DeliveryError):
    """Получатель недоступен навсегда (заблокировал бота, удалил чат) — повторять незачем."""


async def deliver(
    sender: Sender,
    origin: str,
    messages: Sequence[Outgoing],
    *,
    admins: Sequence[str],
    notifier: Notifier | None = None,
) -> int:
    """Рассылает ответы движка адресатам.

    Сбой доставки одному адресату не мешает остальным: покупатель получает ответ,
    даже если у владельца сейчас недоступен вебхук.

    Args:
        sender: Канал.
        origin: Чат, откуда пришло сообщение (адресат по умолчанию).
        messages: Ответы движка в порядке отправки.
        admins: Чаты администраторов для адресата `ADMINS`.
        notifier: Дополнительный канал владельца (`[notify]`) или None.

    Returns:
        Сколько отправок не удалось.
    """
    failures = 0
    for message in messages:
        if message.to == ADMINS:
            targets = list(admins)
            if notifier is not None:
                try:
                    await notifier.send(message.text)
                except NotifyError as exc:
                    logger.warning('уведомление владельцу не доставлено: %s', exc)
                    failures += 1
        else:
            targets = [message.to or origin]
        for chat in targets:
            try:
                await sender.send(chat, message)
            except DeliveryError as exc:
                logger.warning('сообщение в %s не доставлено: %s', chat, exc)
                failures += 1
    return failures

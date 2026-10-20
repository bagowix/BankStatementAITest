"""Сообщения движка: что пришло от покупателя и что ему ответить — без привязки к каналу.

Адаптер канала (Telegram, WhatsApp, консоль) переводит свои апдейты во `Incoming`, а
`Outgoing` — в свою разметку. Отправка спрятана за протоколом `Sender`: тестам и
напоминаниям не нужен настоящий бот. Ключ чата начинается с префикса канала (`tg:`,
`wa:`), и `RoutingSender` по нему выбирает канал: ответ администратора из Telegram
`/reply wa:…` уходит покупателю в WhatsApp, а уведомление владельцу о записи из
WhatsApp — в Telegram.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Final, Protocol

import msgspec

from autostudio.errors import AutoStudioError, NotifyError
from autostudio.notify import Notifier

logger = logging.getLogger(__name__)

# Telegram отвергает callback_data длиннее 64 байт — кнопка просто не отправится.
CALLBACK_DATA_LIMIT: Final = 64
# Самый строгий лимит длины текста среди каналов (Telegram — 4096, с запасом). Длинные
# тексты (расписание на неделю) движок режет сам, по границам строк, а не канал — посреди
# телефона и без продолжения.
TEXT_LIMIT: Final = 4000
# Адресат «все администраторы»: адаптер сам знает их чаты.
ADMINS: Final = '@admins'
# Так `deliver` называет в списке сбоев дополнительный канал владельца `[notify]`.
NOTIFY_CHANNEL: Final = '@notify'
# Префикс ключа чата → канал (для журнала и отчёта: откуда пришла запись).
CHANNELS: Final[Mapping[str, str]] = {'tg': 'telegram', 'wa': 'whatsapp', 'console': 'console'}


def channel_of(key: str) -> str:
    """Канал по ключу чата: `tg:123` → `telegram`, `wa:…` → `whatsapp`, иначе `other`."""
    prefix, sep, _ = key.partition(':')
    return CHANNELS.get(prefix, 'other') if sep else 'other'


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


def split_text(blocks: Sequence[str], limit: int = TEXT_LIMIT, separator: str = '\n\n') -> list[str]:
    """Склеивает блоки в сообщения не длиннее `limit`; блок длиннее лимита режется по строкам.

    Args:
        blocks: Части текста по порядку (например, дни расписания).
        limit: Лимит длины одного сообщения.
        separator: Чем склеиваются блоки внутри сообщения.

    Returns:
        Сообщения по порядку; ни одно не длиннее `limit`.
    """
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= limit:
            pieces.append(block)
            continue
        pieces.extend(_split_lines(block, limit))
    out: list[str] = []
    for piece in pieces:
        if out and len(out[-1]) + len(separator) + len(piece) <= limit:
            out[-1] = f'{out[-1]}{separator}{piece}'
        else:
            out.append(piece)
    return out


def _split_lines(block: str, limit: int) -> list[str]:
    out: list[str] = []
    for line in block.split('\n'):
        # Одна строка длиннее лимита — редкость (огромный вопрос покупателя); её честно обрезаем.
        line = line if len(line) <= limit else line[: limit - 1] + '…'
        if out and len(out[-1]) + 1 + len(line) <= limit:
            out[-1] = f'{out[-1]}\n{line}'
        else:
            out.append(line)
    return out


class Receipt(msgspec.Struct, frozen=True, kw_only=True):
    """Квитанция отправителю о доставке сообщения третьему лицу.

    Администратор, ответивший покупателю через `/reply`, должен знать, дошёл ли ответ:
    покупатель мог заблокировать бота, а ключ чата — быть с опечаткой.

    Attributes:
        delivered: Текст отправителю, если сообщение доставлено.
        failed: Текст отправителю, если нет.
    """

    delivered: str
    failed: str


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
        phone: Телефон из карточки контакта.
        phone_verified: Это собственный номер отправителя («Отправить мой номер»: адаптер
            сверил владельца карточки с отправителем), а не чужой контакт.
        source: Откуда пришёл покупатель: метка deep link (`/start instagram` в Telegram),
            реклама Click-to-WhatsApp; адаптер передаёт как есть, движок проверяет.
    """

    user: str
    name: str = ''
    handle: str | None = None
    language: str | None = None
    is_admin: bool = False
    text: str | None = None
    data: str | None = None
    phone: str | None = None
    phone_verified: bool = False
    source: str | None = None


class Reminder(msgspec.Struct, frozen=True, kw_only=True):
    """Сообщение — напоминание, которое бизнес шлёт первым, и его параметры.

    Telegram отправляет обычный текст. WhatsApp вне 24-часового окна обязан отправить
    одобренный шаблон, и параметры шаблона берёт отсюда, а не разбирает текст.

    Attributes:
        service: Название услуги на языке покупателя (`{{1}}` шаблона).
        when: Дата и время визита на языке покупателя (`{{2}}` шаблона).
        language: Язык покупателя.
    """

    service: str
    when: str
    language: str


class Outgoing(msgspec.Struct, frozen=True, kw_only=True):
    """Ответ движка.

    Attributes:
        text: Текст без разметки.
        buttons: Ряды кнопок под сообщением.
        request_contact: Подпись кнопки «Отправить мой номер» (клавиатура под полем ввода) или None.
        keyboard: Обычные кнопки той же клавиатуры (их подпись приходит текстом, например «Отмена»).
        remove_keyboard: Убрать такую клавиатуру.
        to: Кому: None — отправителю, `ADMINS` — администраторам, иначе ключ чата.
        receipt: Сообщить отправителю, доставлено ли это сообщение (для `to` — другой чат).
        reminder: Это напоминание (бизнес пишет первым) и его параметры для шаблона канала.
    """

    text: str
    buttons: list[list[Button]] = []
    request_contact: str | None = None
    keyboard: list[str] = []
    remove_keyboard: bool = False
    to: str | None = None
    receipt: Receipt | None = None
    reminder: Reminder | None = None


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


class RoutingSender:
    """`Sender`, который выбирает канал по префиксу ключа чата (`tg:`, `wa:`).

    Args:
        routes: Префикс без двоеточия → отправка этого канала.
    """

    def __init__(self, routes: Mapping[str, Sender]) -> None:
        self._routes = dict(routes)

    async def send(self, chat: str, message: Outgoing) -> None:
        prefix, sep, _ = chat.partition(':')
        sender = self._routes.get(prefix) if sep else None
        if sender is None:
            raise RecipientGoneError(f'нет канала для чата {chat!r}')
        await sender.send(chat, message)


def coalesce(messages: Sequence[Outgoing], limit: int) -> list[Outgoing]:
    """Склеивает подряд идущие сообщения одному адресату, если первое — просто текст.

    Движок часто отвечает парой «уведомление + меню». В Telegram это бесплатно, а в
    WhatsApp каждое сообщение платное и считается в лимит «одно сообщение в 6 секунд
    одному пользователю»: склейка делает из пары одно сообщение, если текст помещается.

    Args:
        messages: Ответы движка по порядку.
        limit: Предел длины склеенного текста (у интерактивного сообщения WhatsApp — 1024).

    Returns:
        Те же ответы, где возможно — склеенные.
    """
    out: list[Outgoing] = []
    for message in messages:
        previous = out[-1] if out else None
        mergeable = (
            previous is not None
            and previous.to == message.to
            and not previous.buttons
            and previous.request_contact is None
            and previous.receipt is None
            and previous.reminder is None
            and message.receipt is None
            and message.reminder is None
        )
        merged_text = f'{previous.text}\n\n{message.text}' if previous is not None else message.text
        if previous is not None and mergeable and len(merged_text) <= limit:
            out[-1] = msgspec.structs.replace(message, text=merged_text)
        else:
            out.append(message)
    return out


async def deliver(
    sender: Sender,
    origin: str,
    messages: Sequence[Outgoing],
    *,
    admins: Sequence[str],
    notifier: Notifier | None = None,
) -> list[str]:
    """Рассылает ответы движка адресатам.

    Сбой доставки одному адресату не мешает остальным: покупатель получает ответ,
    даже если у владельца сейчас недоступен вебхук. У сообщения с квитанцией
    (`receipt`) отправитель после попытки получает «доставлено» или «не доставлено».

    Args:
        sender: Канал.
        origin: Чат, откуда пришло сообщение (адресат по умолчанию).
        messages: Ответы движка в порядке отправки.
        admins: Чаты администраторов для адресата `ADMINS`.
        notifier: Дополнительный канал владельца (`[notify]`) или None.

    Returns:
        Адресаты, которым доставить не удалось (`NOTIFY_CHANNEL` — канал `[notify]`).
    """
    failed: list[str] = []
    for message in messages:
        if message.to == ADMINS:
            targets = list(admins)
            if notifier is not None:
                try:
                    await notifier.send(message.text)
                except NotifyError as exc:
                    logger.warning('уведомление владельцу не доставлено: %s', exc)
                    failed.append(NOTIFY_CHANNEL)
        else:
            targets = [message.to or origin]
        missed = [chat for chat in targets if not await _send(sender, chat, message)]
        failed += missed
        if message.receipt is not None:
            note = message.receipt.failed if missed else message.receipt.delivered
            if not await _send(sender, origin, Outgoing(text=note)):
                failed.append(origin)
    return failed


async def _send(sender: Sender, chat: str, message: Outgoing) -> bool:
    try:
        await sender.send(chat, message)
    except DeliveryError as exc:
        logger.warning('сообщение в %s не доставлено: %s', chat, exc)
        return False
    return True

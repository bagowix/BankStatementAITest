"""WhatsApp Cloud API: разбор вебхука, подпись и перевод ответов движка в сообщения — чистые функции.

Здесь нет сети и базы — только форматы Meta и правила перевода, поэтому всё
проверяется тестами на записанных формах payload.

**Перевод `Outgoing` → сообщение WhatsApp** (детерминированный):

- без кнопок — текстовое сообщение (тело до 4096 знаков; длиннее — несколько);
- 1–3 варианта — интерактивные кнопки (`button`): заголовок кнопки до 20 знаков, id до 256,
  текст до 1024 (длиннее — сначала текстом, затем кнопки с коротким «Выберите»);
- больше трёх — список (`list`): один раздел, до 10 строк; заголовок строки до 24 знаков,
  длинная подпись целиком уходит в описание строки (до 72); id строки до 200. Если
  вариантов больше 10, на странице 9 и строка «Ещё…», которая открывает следующую страницу
  (`MORE_PREFIX` + номер первого варианта — состояние страниц держит отправитель);
- «Отправить мой номер» Telegram становится кнопкой «Этот номер» (WhatsApp знает номер
  отправителя — он подтверждён самим WhatsApp), обычные кнопки клавиатуры («Отмена») —
  кнопками, нажатие которых движок получит текстом;
- подтверждение записи (кнопка `ok:` движка) дополняется текстом согласия на напоминание
  (opt-in с названием бизнеса и целью — требование Meta перед шаблонными сообщениями).

Обрезка: длина считается в кодовых единицах UTF-16 (эмодзи — две), с запасом против
того, как считает клиент WhatsApp; обрезанная подпись заканчивается «…», а совпавшие
после обрезки подписи кнопок различаются номером.
"""

import datetime as dt
import hashlib
import hmac
from collections.abc import Sequence
from typing import Final

import msgspec

from autostudio.booking import callbacks as cb
from autostudio.booking.messages import Outgoing, split_text

TEXT_BODY_MAX: Final = 4096
INTERACTIVE_BODY_MAX: Final = 1024
LIST_BODY_MAX: Final = 4096
BUTTONS_MAX: Final = 3
BUTTON_TITLE_MAX: Final = 20
BUTTON_ID_MAX: Final = 256
ROWS_MAX: Final = 10
ROW_TITLE_MAX: Final = 24
ROW_ID_MAX: Final = 200
ROW_DESCRIPTION_MAX: Final = 72
SECTION_TITLE_MAX: Final = 24
LIST_BUTTON_MAX: Final = 20
# Данные кнопок, которые обрабатывает сам адаптер (движок такие коды не выдаёт).
OWN_PHONE_ID: Final = 'wa:phone'
KEYBOARD_PREFIX: Final = 'wa:kb:'
MORE_PREFIX: Final = 'wa:more:'
SIGNATURE_HEADER: Final = 'X-Hub-Signature-256'
_SIGNATURE_PREFIX: Final = 'sha256='
# Сообщения без смысла для разговора: реакции, системные (смена номера) — без ответа.
IGNORED_TYPES: Final = frozenset({'reaction', 'system', 'unsupported', 'request_welcome'})


class Labels(msgspec.Struct, frozen=True, kw_only=True):
    """Подписи адаптера на языке покупателя (из `texts`).

    Attributes:
        list_button: Кнопка, открывающая список (до 20 знаков).
        section: Заголовок раздела списка (до 24 знаков).
        more: Строка «Ещё…».
        choose: Короткий текст над кнопками, когда основной текст ушёл отдельным сообщением.
        use_my_number: Кнопка «Этот номер».
        opt_in: Текст согласия на напоминание (на экране подтверждения записи).
    """

    list_button: str
    section: str
    more: str
    choose: str
    use_my_number: str
    opt_in: str


class Option(msgspec.Struct, frozen=True):
    """Вариант ответа: id (вернётся в вебхуке) и подпись."""

    id: str
    title: str


# Исходящие сообщения: только поля, которые отправляем (omit_defaults — без пустых ключей).


class WaText(msgspec.Struct, frozen=True, omit_defaults=True):
    body: str
    preview_url: bool = False


class WaReply(msgspec.Struct, frozen=True):
    id: str
    title: str


class WaButton(msgspec.Struct, frozen=True):
    reply: WaReply
    type: str = 'reply'


class WaRow(msgspec.Struct, frozen=True, omit_defaults=True):
    id: str
    title: str
    description: str | None = None


class WaSection(msgspec.Struct, frozen=True):
    title: str
    rows: list[WaRow]


class WaAction(msgspec.Struct, frozen=True, omit_defaults=True):
    buttons: list[WaButton] | None = None
    button: str | None = None
    sections: list[WaSection] | None = None


class WaBody(msgspec.Struct, frozen=True):
    text: str


class WaInteractive(msgspec.Struct, frozen=True):
    type: str
    body: WaBody
    action: WaAction


class WaParameter(msgspec.Struct, frozen=True):
    text: str
    type: str = 'text'


class WaComponent(msgspec.Struct, frozen=True):
    type: str
    parameters: list[WaParameter]


class WaLanguage(msgspec.Struct, frozen=True):
    code: str


class WaTemplate(msgspec.Struct, frozen=True):
    name: str
    language: WaLanguage
    components: list[WaComponent]


class WaMessage(msgspec.Struct, frozen=True, kw_only=True, omit_defaults=True):
    """Тело POST `/<phone_number_id>/messages`.

    Адрес — `recipient` (BSUID) или `to` (телефон); если известны оба, отправляем по BSUID:
    у пользователя с username телефона может не быть, а BSUID есть всегда.
    """

    messaging_product: str
    recipient_type: str
    type: str
    to: str | None = None
    recipient: str | None = None
    text: WaText | None = None
    interactive: WaInteractive | None = None
    template: WaTemplate | None = None


def _units(text: str) -> int:
    # Кодовые единицы UTF-16: эмодзи вне BMP — две; так считать безопаснее, чем по символам.
    return len(text.encode('utf-16-le')) // 2


def fit(text: str, limit: int) -> str:
    """Текст не длиннее `limit` кодовых единиц UTF-16; обрезанный заканчивается «…».

    Args:
        text: Исходный текст.
        limit: Предел (лимит поля Meta).

    Returns:
        Текст как есть или обрезанный.
    """
    text = ' '.join(text.split()) if '\n' not in text else text
    if _units(text) <= limit:
        return text
    out = ''
    for char in text:
        if _units(out + char) > limit - 1:
            break
        out += char
    return out.rstrip() + '…'


def _unique(titles: Sequence[str], limit: int) -> list[str]:
    # После обрезки «Wed 21 Oct, 12:00 · Hai…» у двух записей может совпасть, а WhatsApp
    # требует разные подписи кнопок: совпавшие различаются номером в конце.
    seen: set[str] = set()
    out: list[str] = []
    for title in titles:
        candidate, number = title, 1
        while candidate in seen:
            number += 1
            suffix = f' {number}'
            candidate = fit(title, limit - _units(suffix)) + suffix
        seen.add(candidate)
        out.append(candidate)
    return out


def options_of(message: Outgoing, *, phone_button: bool, labels: Labels) -> list[Option]:
    """Варианты ответа: кнопки движка, «Этот номер» и кнопки клавиатуры.

    Args:
        message: Ответ движка.
        phone_button: Можно ли предложить «Этот номер» (у отправителя известен телефон).
        labels: Подписи.

    Returns:
        Варианты по порядку (id не длиннее лимита кнопки).
    """
    options: list[Option] = []
    if message.request_contact is not None:
        if phone_button:
            options.append(Option(OWN_PHONE_ID, labels.use_my_number))
        options += [
            Option(f'{KEYBOARD_PREFIX}{label}', label)
            for label in message.keyboard
            if _units(f'{KEYBOARD_PREFIX}{label}') <= ROW_ID_MAX
        ]
    options += [Option(button.data, button.label) for row in message.buttons for button in row]
    return options


def _text_messages(body: str) -> list[WaMessage]:
    return [
        WaMessage(messaging_product='whatsapp', recipient_type='individual', type='text', text=WaText(body=chunk))
        for chunk in split_text([body], TEXT_BODY_MAX)
    ]


def _buttons_message(body: str, options: Sequence[Option]) -> WaMessage:
    titles = _unique([fit(option.title, BUTTON_TITLE_MAX) for option in options], BUTTON_TITLE_MAX)
    buttons = [WaButton(reply=WaReply(id=option.id, title=title)) for option, title in zip(options, titles, strict=True)]
    interactive = WaInteractive(type='button', body=WaBody(text=body), action=WaAction(buttons=buttons))
    return WaMessage(messaging_product='whatsapp', recipient_type='individual', type='interactive', interactive=interactive)


def _list_message(body: str, options: Sequence[Option], labels: Labels) -> WaMessage:
    titles = _unique([fit(option.title, ROW_TITLE_MAX) for option in options], ROW_TITLE_MAX)
    rows = [
        WaRow(
            id=option.id,
            title=title,
            # Полная подпись — в описании, если заголовок пришлось обрезать.
            description=fit(option.title, ROW_DESCRIPTION_MAX) if title != option.title else None,
        )
        for option, title in zip(options, titles, strict=True)
    ]
    action = WaAction(
        button=fit(labels.list_button, LIST_BUTTON_MAX),
        sections=[WaSection(title=fit(labels.section, SECTION_TITLE_MAX), rows=rows)],
    )
    interactive = WaInteractive(type='list', body=WaBody(text=fit(body, LIST_BODY_MAX)), action=action)
    return WaMessage(messaging_product='whatsapp', recipient_type='individual', type='interactive', interactive=interactive)


def page_of(options: Sequence[Option], offset: int, labels: Labels) -> tuple[list[Option], bool]:
    """Страница списка с `offset`: до 10 вариантов или 9 и «Ещё…».

    Args:
        options: Все варианты.
        offset: Первый вариант страницы.
        labels: Подписи.

    Returns:
        Варианты страницы и есть ли следующая.
    """
    rest = list(options[offset:])
    if len(rest) <= ROWS_MAX:
        return rest, False
    shown = rest[: ROWS_MAX - 1]
    return [*shown, Option(f'{MORE_PREFIX}{offset + len(shown)}', labels.more)], True


def render(message: Outgoing, *, labels: Labels, phone_button: bool, offset: int = 0) -> list[WaMessage]:
    """Сообщения WhatsApp для ответа движка (без адресата — его ставит отправитель).

    Args:
        message: Ответ движка.
        labels: Подписи на языке покупателя.
        phone_button: Предлагать ли «Этот номер» вместо «Отправить мой номер».
        offset: Страница списка (для «Ещё…»); тело следующих страниц — короткое «Выберите».

    Returns:
        Одно или несколько сообщений по порядку.
    """
    options = options_of(message, phone_button=phone_button, labels=labels)
    body = message.text if offset == 0 else labels.choose
    if any(option.id.startswith(f'{cb.CONFIRM}:') for option in options):
        body = f'{body}\n\n{labels.opt_in}'
    if not options:
        return _text_messages(body)
    if len(options) <= BUTTONS_MAX:
        lead: list[WaMessage] = []
        if _units(body) > INTERACTIVE_BODY_MAX:
            lead, body = _text_messages(body), labels.choose
        return [*lead, _buttons_message(body, options)]
    page, _ = page_of(options, offset, labels)
    return [_list_message(body, page, labels)]


def template_message(name: str, language: str, parameters: Sequence[str]) -> WaMessage:
    """Шаблонное сообщение с позиционными параметрами тела (`{{1}}`, `{{2}}`).

    Args:
        name: Имя одобренного шаблона.
        language: Код языка шаблона (`en_US`, `ru`).
        parameters: Значения параметров по порядку.

    Returns:
        Сообщение без адресата.
    """
    body = WaComponent(type='body', parameters=[WaParameter(text=fit(value, INTERACTIVE_BODY_MAX)) for value in parameters])
    template = WaTemplate(name=name, language=WaLanguage(code=language), components=[body])
    return WaMessage(messaging_product='whatsapp', recipient_type='individual', type='template', template=template)


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Проверяет `X-Hub-Signature-256`: HMAC-SHA256 сырого тела ключом App Secret.

    Сравнение — за постоянное время (`hmac.compare_digest`): по времени ответа нельзя
    подобрать подпись побайтно.

    Args:
        app_secret: App Secret приложения Meta.
        body: Тело запроса как пришло — до разбора JSON.
        header: Значение заголовка или None.

    Returns:
        True, если подпись верна.
    """
    if not header or not header.startswith(_SIGNATURE_PREFIX) or not app_secret:
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.removeprefix(_SIGNATURE_PREFIX).strip().lower().encode())


# Входящие: формы payload Meta (лишние поля игнорируются — Meta добавляет новые без предупреждения).


class _Text(msgspec.Struct):
    body: str = ''


class _Reply(msgspec.Struct):
    id: str = ''
    title: str = ''


class _Interactive(msgspec.Struct):
    type: str = ''
    button_reply: _Reply | None = None
    list_reply: _Reply | None = None


class _TemplateButton(msgspec.Struct):
    payload: str = ''
    text: str = ''


class _SharedPhone(msgspec.Struct):
    phone: str = ''
    wa_id: str | None = None


class _SharedContact(msgspec.Struct):
    phones: list[_SharedPhone] = []


class _Referral(msgspec.Struct):
    source_type: str = ''


class _Message(msgspec.Struct):
    id: str = ''
    sender: str | None = msgspec.field(name='from', default=None)
    from_user_id: str | None = None
    timestamp: str = ''
    type: str = ''
    text: _Text | None = None
    interactive: _Interactive | None = None
    button: _TemplateButton | None = None
    contacts: list[_SharedContact] = []
    referral: _Referral | None = None


class _Profile(msgspec.Struct):
    name: str = ''


class _Contact(msgspec.Struct):
    wa_id: str | None = None
    user_id: str | None = None
    profile: _Profile | None = None


class _Error(msgspec.Struct):
    code: int = 0
    title: str = ''


class _Status(msgspec.Struct):
    id: str = ''
    status: str = ''
    errors: list[_Error] = []


class _Value(msgspec.Struct):
    contacts: list[_Contact] = []
    messages: list[_Message] = []
    statuses: list[_Status] = []


class _Change(msgspec.Struct):
    field: str = ''
    value: _Value | None = None


class _Entry(msgspec.Struct):
    changes: list[_Change] = []


class WebhookPayload(msgspec.Struct):
    """Тело вебхука `whatsapp_business_account` (только нужные пакету поля)."""

    object: str = ''
    entry: list[_Entry] = []


class InboundMessage(msgspec.Struct, frozen=True, kw_only=True):
    """Сообщение покупателя, разобранное из вебхука.

    Attributes:
        message_id: `wamid…` — для отсева повторной доставки вебхука.
        user_id: BSUID (`from_user_id`) или None.
        phone: Телефон отправителя (`from`, wa_id) или None — у пользователя с username его нет.
        name: Имя из профиля WhatsApp.
        sent_at: Когда покупатель отправил сообщение (начало 24-часового окна).
        text: Текст сообщения (для кнопок — их подпись).
        reply_id: id нажатой кнопки или строки списка.
        shared_phone: Телефон из присланной карточки контакта.
        shared_own: Карточка — сам отправитель (её wa_id совпадает с отправителем).
        referral: Тип источника Click-to-WhatsApp (`ad`, `post`) или None.
        ignored: Сообщение без смысла для разговора (реакция, системное) — не отвечать.
    """

    message_id: str
    user_id: str | None
    phone: str | None
    name: str
    sent_at: dt.datetime | None
    text: str = ''
    reply_id: str | None = None
    shared_phone: str | None = None
    shared_own: bool = False
    referral: str | None = None
    ignored: bool = False


class DeliveryFailure(msgspec.Struct, frozen=True, kw_only=True):
    """Статус `failed` из вебхука: сообщение принято API, но не доставлено.

    Attributes:
        message_id: id исходящего сообщения.
        code: Код ошибки Meta.
        title: Короткое описание.
    """

    message_id: str
    code: int
    title: str


def _sent_at(raw: str) -> dt.datetime | None:
    if not raw.isascii() or not raw.isdigit():
        return None
    return dt.datetime.fromtimestamp(int(raw), dt.UTC)


def _name(contacts: Sequence[_Contact], message: _Message) -> str:
    for contact in contacts:
        same_user = message.from_user_id is not None and contact.user_id == message.from_user_id
        same_phone = message.sender is not None and contact.wa_id == message.sender
        if (same_user or same_phone) and contact.profile is not None:
            return contact.profile.name
    first = contacts[0] if contacts else None
    return first.profile.name if first is not None and first.profile is not None else ''


def _inbound(message: _Message, contacts: Sequence[_Contact]) -> InboundMessage:
    base = InboundMessage(
        message_id=message.id,
        user_id=message.from_user_id or None,
        phone=message.sender or None,
        name=_name(contacts, message),
        sent_at=_sent_at(message.timestamp),
        referral=(message.referral.source_type or 'ad') if message.referral is not None else None,
        ignored=message.type in IGNORED_TYPES,
    )
    if message.type == 'text' and message.text is not None:
        return msgspec.structs.replace(base, text=message.text.body)
    if message.type == 'interactive' and message.interactive is not None:
        reply = message.interactive.button_reply or message.interactive.list_reply
        if reply is not None:
            return msgspec.structs.replace(base, reply_id=reply.id, text=reply.title)
    if message.type == 'button' and message.button is not None:
        payload = message.button.payload
        return msgspec.structs.replace(base, reply_id=payload or None, text=message.button.text)
    if message.type == 'contacts':
        phones = [phone for contact in message.contacts for phone in contact.phones]
        if phones:
            first = phones[0]
            shared = first.phone or (f'+{first.wa_id}' if first.wa_id else '')
            own = first.wa_id is not None and first.wa_id == message.sender
            return msgspec.structs.replace(base, shared_phone=shared or None, shared_own=own)
    return base


def parse_webhook(payload: WebhookPayload) -> tuple[list[InboundMessage], list[DeliveryFailure]]:
    """Сообщения покупателей и сбои доставки из одного вебхука.

    Args:
        payload: Разобранное тело.

    Returns:
        Входящие по порядку и статусы `failed`.
    """
    messages: list[InboundMessage] = []
    failures: list[DeliveryFailure] = []
    for entry in payload.entry:
        for change in entry.changes:
            value = change.value
            if change.field != 'messages' or value is None:
                continue
            messages += [_inbound(message, value.contacts) for message in value.messages if message.id]
            for status in value.statuses:
                if status.status == 'failed':
                    error = status.errors[0] if status.errors else _Error()
                    failures.append(DeliveryFailure(message_id=status.id, code=error.code, title=error.title))
    return messages, failures

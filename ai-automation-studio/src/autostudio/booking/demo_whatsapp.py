"""Офлайн-часть демо для WhatsApp: подписанные вебхуки на входе, Graph API — на `httpx.MockTransport`.

Покупатель WhatsApp в демо «пишет» так же, как пишет настоящий: тело вебхука в формате
Meta, подпись `X-Hub-Signature-256` ключом App Secret, проверка подписи до разбора. Ответы
бота уходят через настоящий `WhatsAppSender`, но транспорт httpx подменён: запрос к Graph
API не покидает процесс, а его JSON попадает в стенограмму — видно, во что превратились
кнопки движка (кнопки, список, шаблон).
"""

import hashlib
import hmac
from collections.abc import Callable
from typing import Final

import httpx
import msgspec

from autostudio.booking.wa_format import WebhookPayload, verify_signature
from autostudio.booking.whatsapp import WhatsAppBot
from autostudio.clock import Clock
from autostudio.errors import ConfigError

DEMO_APP_SECRET: Final = 'demo-app-secret'  # noqa: S105 - секрет демо, не боевой
DEMO_PHONE_NUMBER_ID: Final = '123456789012345'


class _Reply(msgspec.Struct):
    id: str = ''
    title: str = ''


class _Row(msgspec.Struct):
    id: str = ''
    title: str = ''


class _Section(msgspec.Struct):
    rows: list[_Row] = []


class _ButtonOut(msgspec.Struct):
    reply: _Reply


class _Action(msgspec.Struct):
    buttons: list[_ButtonOut] = []
    button: str = ''
    sections: list[_Section] = []


class _Body(msgspec.Struct):
    text: str = ''


class _Interactive(msgspec.Struct):
    type: str = ''
    body: _Body = msgspec.field(default_factory=_Body)
    action: _Action = msgspec.field(default_factory=_Action)


class _Text(msgspec.Struct):
    body: str = ''


class _Param(msgspec.Struct):
    text: str = ''


class _Component(msgspec.Struct):
    parameters: list[_Param] = []


class _Language(msgspec.Struct):
    code: str = ''


class _Template(msgspec.Struct):
    name: str = ''
    language: _Language = msgspec.field(default_factory=_Language)
    components: list[_Component] = []


class _Sent(msgspec.Struct):
    type: str = ''
    to: str | None = None
    recipient: str | None = None
    text: _Text | None = None
    interactive: _Interactive | None = None
    template: _Template | None = None


class GraphRecorder:
    """Транспорт Graph API демо: копит отправленное и отвечает как Meta (`wamid…`).

    Args:
        listener: Что сделать с каждым сообщением (строка стенограммы) или None.
    """

    def __init__(self, listener: Callable[[str], None] | None = None) -> None:
        self.sent: list[_Sent] = []
        self.options: list[tuple[str, str]] = []
        self.listener = listener

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        message = msgspec.json.decode(request.content, type=_Sent)
        self.sent.append(message)
        if message.interactive is not None:
            action = message.interactive.action
            self.options = [(button.reply.id, button.reply.title) for button in action.buttons] + [
                (row.id, row.title) for section in action.sections for row in section.rows
            ]
        if self.listener is not None:
            self.listener(describe(message))
        wamid = f'wamid.demo{len(self.sent):04d}'
        return httpx.Response(200, json={'messaging_product': 'whatsapp', 'messages': [{'id': wamid}]})


def describe(message: _Sent) -> str:
    """Строка стенограммы: что ушло в WhatsApp и в каком виде."""
    if message.template is not None:
        template = message.template
        values = ' · '.join(param.text for component in template.components for param in component.parameters)
        return f'**Bot → WhatsApp (template `{template.name}`, {template.language.code}):** {values}  '
    if message.interactive is not None:
        interactive = message.interactive
        body = interactive.body.text.replace('\n', '  \n')
        if interactive.type == 'list':
            rows = ' '.join(f'`[{row.title}]`' for section in interactive.action.sections for row in section.rows)
            return f'**Bot → WhatsApp (list «{interactive.action.button}»):** {body}  \n  {rows}  '
        buttons = ' '.join(f'`[{button.reply.title}]`' for button in interactive.action.buttons)
        return f'**Bot → WhatsApp (buttons):** {body}  \n  {buttons}  '
    text = message.text.body if message.text is not None else ''
    return f'**Bot → WhatsApp:** {text}  '.replace('\n', '  \n')


class WhatsAppCustomer:
    """Покупатель WhatsApp в демо: шлёт подписанные вебхуки и нажимает кнопки последнего ответа.

    Args:
        bot: Вебхук WhatsApp.
        graph: Транспорт Graph API демо (из него берутся кнопки последнего ответа).
        clock: Часы (время сообщения в вебхуке).
        name: Имя профиля WhatsApp.
        phone: wa_id (телефон без «+»).
        bsuid: BSUID (`from_user_id`).
        log: Куда писать реплики покупателя (стенограмма) или None.
    """

    def __init__(
        self,
        bot: WhatsAppBot,
        graph: GraphRecorder,
        clock: Clock,
        *,
        name: str,
        phone: str,
        bsuid: str,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._bot = bot
        self._graph = graph
        self._clock = clock
        self._log = log
        self.name = name
        self._phone = phone
        self._bsuid = bsuid
        self._count = 0
        self.rejected = 0

    def _body(self, message: dict[str, str | dict[str, str | dict[str, str]]]) -> bytes:
        self._count += 1
        payload = {
            'object': 'whatsapp_business_account',
            'entry': [
                {
                    'id': 'WABA',
                    'changes': [
                        {
                            'field': 'messages',
                            'value': {
                                'messaging_product': 'whatsapp',
                                'metadata': {'phone_number_id': DEMO_PHONE_NUMBER_ID},
                                'contacts': [
                                    {'wa_id': self._phone, 'user_id': self._bsuid, 'profile': {'name': self.name}}
                                ],
                                'messages': [
                                    {
                                        'id': f'wamid.in{self._count:04d}',
                                        'from': self._phone,
                                        'from_user_id': self._bsuid,
                                        'timestamp': str(int(self._clock().timestamp())),
                                        **message,
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        return msgspec.json.encode(payload)

    async def _deliver(self, body: bytes) -> None:
        signature = 'sha256=' + hmac.new(DEMO_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not verify_signature(DEMO_APP_SECRET, body, signature):  # pragma: no cover - подпись своя
            raise RuntimeError('подпись демо не прошла')
        # Подделка: то же тело с чужой подписью сервер отбрасывает до разбора JSON.
        if verify_signature(DEMO_APP_SECRET, body, 'sha256=' + '0' * 64):  # pragma: no cover
            raise RuntimeError('поддельная подпись прошла')
        self.rejected += 1
        await self._bot.handle_payload(msgspec.json.decode(body, type=WebhookPayload))

    def _said(self, line: str) -> None:
        if self._log is not None:
            self._log(f'**{self.name} (WhatsApp):** {line}')

    async def say(self, text: str) -> None:
        self._said(text)
        await self._deliver(self._body({'type': 'text', 'text': {'body': text}}))

    async def tap(self, title: str) -> str:
        """Нажимает кнопку или строку списка последнего ответа по началу подписи; возвращает подпись."""
        for option_id, option_title in self._graph.options:
            if option_title.startswith(title):
                kind = 'list_reply' if any(option_id == row for row in self._rows()) else 'button_reply'
                reply = {'type': kind, kind: {'id': option_id, 'title': option_title}}
                self._said(f'_taps_ `[{option_title}]`')
                await self._deliver(self._body({'type': 'interactive', 'interactive': reply}))
                return option_title
        raise ConfigError(f'демо: нет кнопки {title!r} в последнем ответе WhatsApp')

    def _rows(self) -> list[str]:
        last = next((sent for sent in reversed(self._graph.sent) if sent.interactive is not None), None)
        if last is None or last.interactive is None:
            return []
        return [row.id for section in last.interactive.action.sections for row in section.rows]

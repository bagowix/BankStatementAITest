"""Движок разговора: `handle(Incoming) -> list[Outgoing]`, без знания о канале.

Что здесь решено и почему:

- **Запись создаёт только кнопка «Подтвердить».** Свободный текст разбирает модель,
  но её ответ лишь выбирает, какие свободные слоты показать; слоты считает код, а
  запись появляется после явного нажатия. Текст «отмени все записи» в худшем случае
  покажет покупателю его собственные записи с кнопками.
- **Кнопки самодостаточны** (см. `callbacks`): после перезапуска бота или нажатия в
  старом сообщении шаг продолжается, если слот ещё свободен, а если нет — покупатель
  видит ближайшие альтернативы.
- **Состояние разговора — в памяти, с TTL** (`state_ttl_min`). Оно держит только
  незаконченный шаг (ввод имени, телефона, вопроса). После перезапуска процесса эти
  шаги забываются: покупатель увидит меню, а все кнопки продолжат работать. Профиль
  (имя, телефон, язык) и записи — в SQLite и переживают перезапуск.
- Сообщения одного покупателя обрабатываются по очереди (замок на пользователя):
  двойное нажатие «Подтвердить» не создаст две записи.
- **Бот публичный, поэтому всё ограничено:** у покупателя не больше
  `max_active_per_customer` предстоящих записей, свободный текст (вызов модели) — не
  чаще `MODEL_CALLS` за `MODEL_WINDOW`, уведомление администраторам «позовите человека» —
  не больше `HANDOFFS` разных текстов за `HANDOFF_WINDOW` и `HANDOFFS_PER_DAY` за сутки от
  одного покупателя; повтор уже переданного текста владельца не будит. Сверх лимита текст не
  теряется молча: покупатель слышит, что сообщение не передано, и получает телефон, а в
  журнал пишется `booking.handoff_throttled`. Ответ администратора покупателю обнуляет его
  счёт. Счётчики — в памяти.
- **Администратор записывает гостя «с улицы»** тем же сценарием: бот спрашивает имя и
  телефон гостя каждый раз и не сохраняет их в профиль администратора.
"""

import asyncio
import datetime as dt
import logging
import weakref
from collections.abc import Sequence
from enum import StrEnum
from typing import Final

import msgspec

from autostudio.booking import callbacks as cb
from autostudio.booking.admin import AdminDesk
from autostudio.booking.callbacks import SlotRef
from autostudio.booking.config import BookingConfig, Service
from autostudio.booking.domain import (
    Booking,
    BookingError,
    CancellationTooLateError,
    Customer,
    Slot,
    SlotTakenError,
    Source,
    TimeWindow,
    TooManyBookingsError,
    clean_name,
    clean_phone,
    local_today,
    wall_to_utc,
    will_remind,
)
from autostudio.booking.limits import Handoff, HandoffGate, RateLimiter
from autostudio.booking.messages import ADMINS, Button, Incoming, Outgoing
from autostudio.booking.prompts import (
    BookingIntent,
    BookingRequest,
    FaqAnswer,
    faq_system_prompt,
    intent_system_prompt,
    validate_answer,
    validate_intent,
    wrap_customer_message,
)
from autostudio.booking.service import BookingService
from autostudio.booking.views import DAYS_PER_PAGE, SUGGESTIONS, TIMES_PER_PAGE, Views
from autostudio.clock import Clock
from autostudio.errors import LLMError, NotifyError
from autostudio.llm import LLMClient
from autostudio.notify import Notifier

logger = logging.getLogger(__name__)

_ALERT_EVERY: Final = dt.timedelta(hours=1)
_ALTERNATIVES: Final = 6
# Живой человек не пишет боту записи больше пары сообщений в минуту; поток — это спам
# или зацикленный бот, и каждый его вызов модели оплачивает оператор.
MODEL_CALLS: Final = 8
MODEL_WINDOW: Final = dt.timedelta(minutes=10)
# Несколько разных вопросов подряд — обычный покупатель (адрес, оплата, метро); десяток за
# минуты — поток, который не должен становиться потоком уведомлений владельцу. Суточный
# предел держит поток «по чуть-чуть» весь день; ответ администратора обнуляет счёт.
HANDOFFS: Final = 4
HANDOFF_WINDOW: Final = dt.timedelta(minutes=10)
HANDOFFS_PER_DAY: Final = 12
# Тот же текст в течение часа — это «вы получили?», а не новый вопрос.
HANDOFF_REPEAT_WINDOW: Final = dt.timedelta(hours=1)


class Step(StrEnum):
    """Незаконченный шаг, на котором бот ждёт текст."""

    IDLE = 'idle'
    NAME = 'name'
    PHONE = 'phone'
    QUESTION = 'question'
    HUMAN = 'human'


class Conversation(msgspec.Struct, frozen=True, kw_only=True):
    """Состояние разговора одного покупателя.

    Attributes:
        language: Язык разговора.
        step: Чего бот ждёт.
        ref: Выбранный слот (на шагах имени и телефона).
        name: Введённое имя (на шаге телефона).
        pref_day: День из свободного текста, пока покупатель выбирает услугу.
        pref_window: Окно времени из свободного текста.
        walk_in: Администратор записывает гостя (имя и телефон — гостя, не профиль).
        guest: Имя и телефон гостя на шаге подтверждения записи администратором.
        expires_at: Когда состояние забывается.
    """

    language: str
    step: Step = Step.IDLE
    ref: SlotRef | None = None
    name: str | None = None
    pref_day: dt.date | None = None
    pref_window: TimeWindow | None = None
    walk_in: bool = False
    guest: Customer | None = None
    expires_at: dt.datetime

    def reset(self) -> 'Conversation':
        """Чистое состояние с тем же языком."""
        return Conversation(language=self.language, expires_at=self.expires_at)


class Conversations:
    """Состояния разговоров в памяти с временем жизни.

    Args:
        ttl: Сколько состояние живёт после последнего сообщения.
    """

    def __init__(self, ttl: dt.timedelta) -> None:
        self._ttl = ttl
        self._items: dict[str, Conversation] = {}

    def get(self, user: str, now: dt.datetime) -> Conversation | None:
        """Состояние или None, если его нет или оно истекло."""
        found = self._items.get(user)
        if found is not None and found.expires_at <= now:
            del self._items[user]
            return None
        return found

    def put(self, user: str, conversation: Conversation, now: dt.datetime) -> None:
        """Сохраняет состояние и продлевает его жизнь; заодно выбрасывает истёкшие."""
        self._items[user] = msgspec.structs.replace(conversation, expires_at=now + self._ttl)
        if len(self._items) > 1000:
            for key in [key for key, item in self._items.items() if item.expires_at <= now]:
                del self._items[key]

    def __len__(self) -> int:
        return len(self._items)


type Reply = tuple[list[Outgoing], Conversation]


class Engine:
    """Разговор с покупателем и служебные команды администраторов.

    Args:
        config: Конфиг клиента.
        views: Экраны.
        service: Действия с записями.
        admin: Служебные команды.
        llm: Модель (или `FakeLLM`).
        clock: Часы.
        operator: Канал оператора для сигнала «модель недоступна» или None.
    """

    def __init__(
        self,
        *,
        config: BookingConfig,
        views: Views,
        service: BookingService,
        admin: AdminDesk,
        llm: LLMClient,
        clock: Clock,
        operator: Notifier | None = None,
    ) -> None:
        self._config = config
        self._views = views
        self._texts = views.texts
        self._service = service
        self._store = service.store
        self._admin = admin
        self._llm = llm
        self._clock = clock
        self._operator = operator
        self._conversations = Conversations(dt.timedelta(minutes=config.booking.state_ttl_min))
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._last_alert: dt.datetime | None = None
        self._model_budget = RateLimiter(MODEL_CALLS, MODEL_WINDOW)
        self._handoffs = HandoffGate(
            burst=HANDOFFS, burst_window=HANDOFF_WINDOW, daily=HANDOFFS_PER_DAY, repeat_window=HANDOFF_REPEAT_WINDOW
        )

    @property
    def conversations(self) -> Conversations:
        return self._conversations

    async def handle(self, message: Incoming) -> list[Outgoing]:
        """Обрабатывает одно входящее событие.

        Args:
            message: Текст, нажатие кнопки или контакт.

        Returns:
            Ответы по порядку: покупателю, администраторам, другим чатам.
        """
        lock = self._locks.get(message.user)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[message.user] = lock
        async with lock:
            return await self._handle(message)

    async def _handle(self, message: Incoming) -> list[Outgoing]:
        now = self._clock()
        conversation = self._conversations.get(message.user, now) or Conversation(
            language=self._language_for(message), expires_at=now
        )
        if message.is_admin:
            reply = await self._admin.handle(message, now)
            if reply is not None:
                self._release_answered(reply)
                return reply
        before = conversation.step
        outgoing, after = await self._route(message, conversation, now)
        if before == Step.PHONE and after.step != Step.PHONE:
            outgoing = self._close_contact_keyboard(outgoing, after.language)
        self._conversations.put(message.user, after, now)
        return outgoing

    def _release_answered(self, outgoing: Sequence[Outgoing]) -> None:
        # Администратор написал покупателю (`/reply`, извинение за отмену): разговор живой, и
        # ответы покупателя должны доходить до владельца, а не упираться в лимит передачи.
        for item in outgoing:
            if item.to is not None and item.to != ADMINS:
                self._handoffs.release(item.to)

    def _language_for(self, message: Incoming) -> str:
        profile = self._store.customer(message.user)
        if profile is not None and profile.language in self._config.booking.languages:
            return profile.language
        return self._texts.resolve_language(message.language)

    def _close_contact_keyboard(self, outgoing: list[Outgoing], language: str) -> list[Outgoing]:
        # Клавиатура «Отправить номер» живёт, пока её не уберут явно, а сообщение с
        # инлайн-кнопками убрать её не может: нужен ответ без кнопок.
        first = outgoing[0] if outgoing else None
        if first is not None and first.to is None and not first.buttons and first.request_contact is None:
            return [msgspec.structs.replace(first, remove_keyboard=True), *outgoing[1:]]
        return [Outgoing(text=self._texts.get(language, 'ok_short'), remove_keyboard=True), *outgoing]

    async def _route(self, message: Incoming, conversation: Conversation, now: dt.datetime) -> Reply:
        if message.data is not None:
            return await self._on_button(message, conversation, now)
        if message.phone is not None:
            # «Свой» номер — только если адаптер сверил владельца карточки с отправителем;
            # чужой контакт проверяется так же строго, как набранный руками.
            own = message.phone_verified and not conversation.walk_in
            return self._on_phone(message.phone, message, conversation, now, international=own)
        text = (message.text or '').strip()
        lang = conversation.language
        if text.startswith('/'):
            return self._on_command(text, message, conversation, now)
        if conversation.step in (Step.NAME, Step.PHONE) and text in self._abort_labels():
            return self._views.notice_and_menu(lang, 'aborted'), conversation.reset()
        if conversation.step == Step.NAME:
            return self._on_name(text, message, conversation)
        if conversation.step == Step.PHONE:
            return self._on_phone(text, message, conversation, now, international=False)
        if not text:
            return self._views.notice_and_menu(lang, 'not_understood'), conversation.reset()
        if conversation.step == Step.HUMAN:
            return self._forward_to_human(text, message, conversation.reset(), now)
        if not message.is_admin and not self._model_budget.allow(message.user, now):
            return self._views.notice_and_menu(lang, 'slow_down'), conversation.reset()
        if conversation.step == Step.QUESTION:
            return await self._answer_question(text, message, conversation.reset(), now)
        return await self._on_free_text(text, message, conversation, now)

    def _abort_labels(self) -> set[str]:
        return {self._texts.get(lang, 'btn_abort') for lang in self._config.booking.languages}

    # Команды и кнопки

    def _on_command(self, text: str, message: Incoming, conversation: Conversation, now: dt.datetime) -> Reply:
        command = AdminDesk.parse_command(text)[0]
        lang = conversation.language
        fresh = conversation.reset()
        match command:
            case 'book':
                return [self._views.services(lang)], fresh
            case 'my':
                return self._views.my_bookings(self._store.for_customer(message.user, now), lang), fresh
            case 'cancel':
                return self._views.notice_and_menu(lang, 'aborted'), fresh
            case _:
                outgoing = self._views.greeting(lang, clean_name(message.name) or '')
                if message.is_admin:
                    outgoing.append(Outgoing(text=self._texts.get(self._config.owner_language, 'admin_help')))
                return outgoing, fresh

    async def _on_button(self, message: Incoming, conversation: Conversation, now: dt.datetime) -> Reply:
        action, args = cb.split(message.data or '')
        lang = conversation.language
        fresh = conversation.reset()
        views = self._views
        match action:
            case cb.MENU:
                return [views.menu(lang)], fresh
            case cb.BOOK:
                return self._start_booking(lang, fresh, now)
            case cb.MY:
                return views.my_bookings(self._store.for_customer(message.user, now), lang), fresh
            case cb.PRICES:
                return [views.prices(lang)], fresh
            case cb.ASK:
                return [Outgoing(text=self._texts.get(lang, 'ask_question'))], msgspec.structs.replace(
                    fresh, step=Step.QUESTION
                )
            case cb.HUMAN:
                return [Outgoing(text=self._texts.get(lang, 'human_prompt'))], msgspec.structs.replace(
                    fresh, step=Step.HUMAN
                )
            case cb.LANGUAGE if args and args[0] in self._config.booking.languages:
                self._store.save_customer(Customer(key=message.user, language=args[0]), now)
                chosen = msgspec.structs.replace(fresh, language=args[0])
                return [views.menu(args[0])], chosen
            case cb.ABORT:
                return views.notice_and_menu(lang, 'aborted'), fresh
            case cb.USE_NAME if conversation.step == Step.NAME and conversation.ref is not None:
                return self._on_name(message.name, message, conversation)
            case cb.SERVICE if len(args) == 1:
                return self._after_service(args[0], conversation, now)
            case cb.STAFF if len(args) == 2:
                return self._day_picker(args[0], cb.staff_from_code(args[1]), None, lang, now), fresh
            case cb.DAYS_FROM if len(args) == 3:
                return self._day_picker(args[0], cb.staff_from_code(args[1]), cb.decode_day(args[2]), lang, now), fresh
            case cb.DAY if len(args) == 3:
                return self._time_picker(args[0], cb.staff_from_code(args[1]), args[2], 0, lang, now), fresh
            case cb.DAY if len(args) == 4 and args[3].isascii() and args[3].isdecimal():
                # `isdecimal` без `isascii` пропустил бы «٣» и подобное: кнопку можно подделать.
                page = int(args[3])
                return self._time_picker(args[0], cb.staff_from_code(args[1]), args[2], page, lang, now), fresh
            case cb.TIME:
                return self._on_slot(cb.parse_slot(args), message, conversation, now)
            case cb.CONFIRM:
                return await self._on_confirm(cb.parse_slot(args), message, conversation, now)
            case cb.EDIT:
                return self._on_edit(cb.parse_slot(args), message, conversation, now)
            case cb.BOOKING if len(args) == 1:
                return self._booking_card(args[0], message, lang, now), fresh
            case cb.CANCEL_ASK if len(args) == 1:
                return self._cancel_ask(args[0], message, lang, now), fresh
            case cb.CANCEL_YES if len(args) == 1:
                return await self._cancel_yes(args[0], message, lang, now), fresh
            case cb.MOVE if len(args) == 1:
                return self._move_days(args[0], message, lang, now), fresh
            case cb.MOVE_DAY if len(args) == 2:
                return self._move_times(args[0], args[1], message, lang, now), fresh
            case cb.MOVE_TIME if len(args) == 2:
                return self._move_confirm(args[0], args[1], message, lang, now), fresh
            case cb.MOVE_YES if len(args) == 2:
                return await self._move_yes(args[0], args[1], message, lang, now), fresh
            case _:
                return self._stale(lang), fresh

    def _stale(self, language: str) -> list[Outgoing]:
        return self._views.notice_and_menu(language, 'stale')

    # Запись по кнопкам

    def _start_booking(self, language: str, conversation: Conversation, now: dt.datetime) -> Reply:
        services = self._config.booking.services
        if len(services) == 1:
            return self._after_service(services[0].id, conversation, now)
        return [self._views.services(language)], conversation

    def _after_service(self, service_id: str, conversation: Conversation, now: dt.datetime) -> Reply:
        lang = conversation.language
        service = self._config.booking.service(service_id)
        fresh = conversation.reset()
        if service is None:
            return self._stale(lang), fresh
        if conversation.pref_day is not None or conversation.pref_window is not None:
            return self._propose(service, conversation.pref_day, conversation.pref_window, [], lang, now), fresh
        if len(self._config.booking.eligible_staff(service)) > 1:
            return [self._views.staff_picker(service, lang)], fresh
        return self._day_picker(service.id, None, None, lang, now), fresh

    def _day_picker(
        self, service_id: str, staff_id: str | None, start_day: dt.date | None, language: str, now: dt.datetime
    ) -> list[Outgoing]:
        service = self._config.booking.service(service_id)
        if service is None or (staff_id is not None and staff_id not in self._staff_ids(service)):
            return self._stale(language)
        days = self._service.bookable_days(
            service.id, now, staff_id=staff_id, start_day=start_day, limit=DAYS_PER_PAGE + 1
        )
        if not days:
            return self._views.no_days(service.id, language)
        page, extra = days[:DAYS_PER_PAGE], days[DAYS_PER_PAGE:]
        more = cb.join(cb.DAYS_FROM, service.id, cb.staff_code(staff_id), cb.encode_day(extra[0])) if extra else None
        text = self._texts.get(language, 'choose_day', service=self._texts.pick(service.name, language))
        return [
            self._views.day_picker(
                text=text,
                days=page,
                today=local_today(self._config, now),
                language=language,
                day_data=lambda day: cb.day_data(service.id, staff_id, day),
                more=more,
                back=self._views.menu_button(language),
            )
        ]

    def _time_picker(
        self, service_id: str, staff_id: str | None, raw_day: str, page: int, language: str, now: dt.datetime
    ) -> list[Outgoing]:
        service = self._config.booking.service(service_id)
        day = cb.decode_day(raw_day)
        if service is None or day is None or (staff_id is not None and staff_id not in self._staff_ids(service)):
            return self._stale(language)
        slots = self._service.free_slots(service.id, day, now, staff_id=staff_id)
        if not slots:
            # День успел заполниться, пока покупатель выбирал: показываем другие дни.
            return self._day_picker(service.id, staff_id, None, language, now)
        shown = slots[page * TIMES_PER_PAGE : (page + 1) * TIMES_PER_PAGE]
        if not shown:
            return self._stale(language)
        more = cb.day_data(service.id, staff_id, day, page + 1) if len(slots) > (page + 1) * TIMES_PER_PAGE else None
        text = self._texts.get(
            language,
            'choose_time',
            service=self._texts.pick(service.name, language),
            day=self._texts.day_label(day, language, local_today(self._config, now)),
        )
        other_day = Button(
            self._texts.get(language, 'btn_other_day'), cb.join(cb.STAFF, service.id, cb.staff_code(staff_id))
        )
        return [
            self._views.time_picker(
                text=text,
                slots=shown,
                language=language,
                slot_data=self._views.slot_ref_data(cb.TIME, 'buttons', staff_id),
                more=more,
                back=[other_day, self._views.menu_button(language)],
            )
        ]

    def _staff_ids(self, service: Service) -> set[str]:
        return {member.id for member in self._config.booking.eligible_staff(service)}

    def _valid_ref(self, ref: SlotRef | None) -> bool:
        if ref is None:
            return False
        service = self._config.booking.service(ref.service_id)
        return service is not None and (ref.staff_id is None or ref.staff_id in self._staff_ids(service))

    def _on_slot(self, ref: SlotRef | None, message: Incoming, conversation: Conversation, now: dt.datetime) -> Reply:
        lang = conversation.language
        fresh = conversation.reset()
        if ref is None or not self._valid_ref(ref):
            return self._stale(lang), fresh
        slot = self._service.find(ref.service_id, ref.staff_id, ref.start, now)
        if slot is None:
            return self._taken(ref, lang, now), fresh
        if message.is_admin:
            return self._ask_walk_in(ref, slot, fresh, lang)
        count = self._store.active_count(message.user, now)
        if count >= self._config.booking.max_active_per_customer:
            # Сразу, а не после имени и телефона: иначе покупатель зря пройдёт три шага.
            return self._views.too_many(count, lang), fresh
        profile = self._store.customer(message.user)
        if profile is not None and profile.complete:
            return [self._views.confirm(ref, slot, profile, lang)], fresh
        ask = self._views.ask_name(slot, clean_name(message.name), lang)
        return [ask], msgspec.structs.replace(fresh, step=Step.NAME, ref=ref)

    def _on_edit(self, ref: SlotRef | None, message: Incoming, conversation: Conversation, now: dt.datetime) -> Reply:
        lang = conversation.language
        fresh = conversation.reset()
        if ref is None or not self._valid_ref(ref):
            return self._stale(lang), fresh
        slot = self._service.find(ref.service_id, ref.staff_id, ref.start, now)
        if slot is None:
            return self._taken(ref, lang, now), fresh
        if message.is_admin:
            return self._ask_walk_in(ref, slot, fresh, lang)
        ask = self._views.ask_name(slot, clean_name(message.name), lang)
        return [ask], msgspec.structs.replace(fresh, step=Step.NAME, ref=ref)

    def _ask_walk_in(self, ref: SlotRef, slot: Slot, conversation: Conversation, language: str) -> Reply:
        # Администратор записывает гостя: имя и телефон спрашиваются каждый раз и не
        # сохраняются в его профиль, иначе следующий гость унаследовал бы данные предыдущего.
        ask = self._views.ask_walkin_name(slot, language)
        return [ask], msgspec.structs.replace(conversation, step=Step.NAME, ref=ref, walk_in=True)

    def _taken(self, ref: SlotRef, language: str, now: dt.datetime) -> list[Outgoing]:
        alternatives = self._service.nearest(ref.service_id, ref.start, now, limit=_ALTERNATIVES, staff_id=ref.staff_id)
        if not alternatives:
            return self._views.notice_and_menu(language, 'slot_taken_none')
        rows = self._views.suggestion_rows(
            alternatives,
            language,
            local_today(self._config, now),
            self._views.slot_ref_data(cb.TIME, ref.source, ref.staff_id),
        )
        return [
            Outgoing(text=self._texts.get(language, 'slot_taken'), buttons=[*rows, [self._views.menu_button(language)]])
        ]

    # Имя, телефон, подтверждение

    def _on_name(self, raw: str, message: Incoming, conversation: Conversation) -> Reply:
        lang = conversation.language
        name = clean_name(raw)
        if name is None:
            hint = None if conversation.walk_in else clean_name(message.name)
            return [self._views.bad_name(hint, lang)], conversation
        ask = self._views.ask_walkin_phone(lang) if conversation.walk_in else self._views.ask_phone(name, lang)
        return [ask], msgspec.structs.replace(conversation, step=Step.PHONE, name=name)

    def _on_phone(
        self, raw: str, message: Incoming, conversation: Conversation, now: dt.datetime, *, international: bool
    ) -> Reply:
        lang = conversation.language
        ref = conversation.ref
        if conversation.step not in (Step.NAME, Step.PHONE) or ref is None:
            return [
                Outgoing(text=self._texts.get(lang, 'got_contact'), remove_keyboard=True),
                self._views.menu(lang),
            ], (conversation.reset())
        phone = clean_phone(raw, region=self._config.booking.phone_region, shared=international)
        if phone is None:
            # Клавиатура «Отправить номер» ещё на экране: достаточно подсказки с примером.
            return [self._views.bad_phone(lang)], conversation
        name = conversation.name or (None if conversation.walk_in else clean_name(message.name))
        if name is None:
            return [self._views.bad_name(None, lang)], conversation
        customer = Customer(key=message.user, name=name, phone=phone, language=lang)
        after = conversation.reset()
        if conversation.walk_in:
            after = msgspec.structs.replace(after, ref=ref, guest=customer)
        else:
            self._store.save_customer(customer, now)
        slot = self._service.find(ref.service_id, ref.staff_id, ref.start, now)
        done = Outgoing(text=self._texts.get(lang, 'got_contact'), remove_keyboard=True)
        if slot is None:
            return [done, *self._taken(ref, lang, now)], conversation.reset()
        return [done, self._views.confirm(ref, slot, customer, lang)], after

    async def _on_confirm(
        self, ref: SlotRef | None, message: Incoming, conversation: Conversation, now: dt.datetime
    ) -> Reply:
        lang = conversation.language
        fresh = conversation.reset()
        if ref is None or not self._valid_ref(ref):
            return self._stale(lang), fresh
        if message.is_admin:
            guest = conversation.guest if conversation.ref == ref else None
            if guest is None:
                # Данные гостя забыты (истёк разговор, повторное нажатие): спрашиваем заново.
                return self._on_slot(ref, message, conversation, now)
            return await self._book(ref, guest, lang, now, source='admin', max_active=None)
        profile = self._store.customer(message.user)
        if profile is None or not profile.complete:
            # Профиль потерян (или кнопка чужая): спрашиваем данные заново для этого же слота.
            return self._on_slot(ref, message, conversation, now)
        duplicate = self._existing(message.user, ref, now)
        if duplicate is not None:
            when = self._texts.when(duplicate.start, lang)
            return [Outgoing(text=self._texts.get(lang, 'already_booked', id=duplicate.id, when=when))], fresh
        return await self._book(
            ref, profile, lang, now, source=ref.source, max_active=self._config.booking.max_active_per_customer
        )

    async def _book(
        self,
        ref: SlotRef,
        customer: Customer,
        language: str,
        now: dt.datetime,
        *,
        source: Source,
        max_active: int | None,
    ) -> Reply:
        fresh = Conversation(language=language, expires_at=now)
        try:
            booking = await self._service.book(
                service_id=ref.service_id,
                staff_id=ref.staff_id,
                start=ref.start,
                customer=customer,
                language=language,
                source=source,
                now=now,
                max_active=max_active,
            )
        except SlotTakenError:
            return self._taken(ref, language, now), fresh
        except TooManyBookingsError:
            return self._views.too_many(self._store.active_count(customer.key, now), language), fresh
        booked = self._views.booked(booking, language, remind=will_remind(self._config, booking, now))
        return [booked, self._views.admin_new(booking)], fresh

    def _existing(self, user: str, ref: SlotRef, now: dt.datetime) -> Booking | None:
        return next(
            (
                booking
                for booking in self._store.for_customer(user, now)
                if booking.service_id == ref.service_id and booking.start == ref.start
            ),
            None,
        )

    # Мои записи: отмена и перенос

    def _own(self, booking_id: str, message: Incoming, now: dt.datetime) -> Booking | None:
        booking = self._store.get(booking_id)
        if booking is None or booking.customer != message.user or not booking.active or booking.start <= now:
            return None
        return booking

    def _too_late(self, booking: Booking, message: Incoming, now: dt.datetime) -> bool:
        # Политика отмены — для покупателей; администратор правит и свои записи гостей.
        return not message.is_admin and booking.start - now < self._service.notice

    def _too_late_reply(self, language: str) -> list[Outgoing]:
        limit = self._texts.duration(self._config.booking.cancellation_min_before, language)
        text = self._texts.get(language, 'too_late', limit=limit)
        phone = self._config.booking.contact_phone
        if phone:
            text = f'{text}\n{self._texts.get(language, "call_us", phone=phone)}'
        return [Outgoing(text=text), self._views.menu(language)]

    def _booking_card(self, booking_id: str, message: Incoming, language: str, now: dt.datetime) -> list[Outgoing]:
        booking = self._own(booking_id, message, now)
        if booking is None:
            return self._views.notice_and_menu(language, 'not_active', id=booking_id)
        return [self._views.booking_detail(booking, language)]

    def _cancel_ask(self, booking_id: str, message: Incoming, language: str, now: dt.datetime) -> list[Outgoing]:
        booking = self._own(booking_id, message, now)
        if booking is None:
            return self._views.notice_and_menu(language, 'not_active', id=booking_id)
        if self._too_late(booking, message, now):
            return self._too_late_reply(language)
        text = self._texts.get(
            language, 'confirm_cancel', id=booking.id, when=self._texts.when(booking.start, language)
        )
        rows = [
            [Button(self._texts.get(language, 'btn_yes_cancel'), cb.join(cb.CANCEL_YES, booking.id))],
            [Button(self._texts.get(language, 'btn_keep'), cb.join(cb.BOOKING, booking.id))],
        ]
        return [Outgoing(text=text, buttons=rows)]

    async def _cancel_yes(self, booking_id: str, message: Incoming, language: str, now: dt.datetime) -> list[Outgoing]:
        try:
            booking = await self._service.cancel(booking_id, now=now, by_admin=message.is_admin, customer=message.user)
        except CancellationTooLateError:
            return self._too_late_reply(language)
        except BookingError:
            return self._views.notice_and_menu(language, 'not_active', id=booking_id)
        return [
            Outgoing(text=self._texts.get(language, 'cancelled', id=booking.id)),
            self._views.menu(language),
            self._views.admin_cancelled(booking, by_admin=message.is_admin),
        ]

    def _move_days(self, booking_id: str, message: Incoming, language: str, now: dt.datetime) -> list[Outgoing]:
        booking = self._own(booking_id, message, now)
        if booking is None:
            return self._views.notice_and_menu(language, 'not_active', id=booking_id)
        if self._too_late(booking, message, now):
            return self._too_late_reply(language)
        days = self._service.bookable_days(
            booking.service_id, now, staff_id=booking.staff_id, limit=DAYS_PER_PAGE, ignore=booking.id
        )
        if not days:
            return self._views.no_days(booking.service_id, language)
        text = self._texts.get(
            language, 'reschedule_day', id=booking.id, when=self._texts.when(booking.start, language)
        )
        back = Button(self._texts.get(language, 'btn_back'), cb.join(cb.BOOKING, booking.id))
        screen = self._views.day_picker(
            text=text,
            days=days,
            today=local_today(self._config, now),
            language=language,
            day_data=lambda day: cb.join(cb.MOVE_DAY, booking.id, cb.encode_day(day)),
            more=None,
            back=back,
        )
        return [screen]

    def _move_times(
        self, booking_id: str, raw_day: str, message: Incoming, language: str, now: dt.datetime
    ) -> list[Outgoing]:
        booking = self._own(booking_id, message, now)
        day = cb.decode_day(raw_day)
        if booking is None or day is None:
            return self._views.notice_and_menu(language, 'not_active', id=booking_id)
        slots = self._service.free_slots(booking.service_id, day, now, staff_id=booking.staff_id, ignore=booking.id)
        if not slots:
            return self._move_days(booking_id, message, language, now)
        text = self._texts.get(
            language,
            'reschedule_time',
            id=booking.id,
            day=self._texts.day_label(day, language, local_today(self._config, now)),
        )
        back = Button(self._texts.get(language, 'btn_back'), cb.join(cb.MOVE, booking.id))
        screen = self._views.time_picker(
            text=text,
            slots=slots[:TIMES_PER_PAGE],
            language=language,
            slot_data=lambda slot: cb.join(cb.MOVE_TIME, booking.id, cb.encode_ts(slot.start)),
            more=None,
            back=[back],
        )
        return [screen]

    def _move_confirm(
        self, booking_id: str, raw_ts: str, message: Incoming, language: str, now: dt.datetime
    ) -> list[Outgoing]:
        booking = self._own(booking_id, message, now)
        start = cb.decode_ts(raw_ts)
        if booking is None or start is None:
            return self._views.notice_and_menu(language, 'not_active', id=booking_id)
        slot = self._service.find(booking.service_id, booking.staff_id, start, now, ignore=booking.id)
        if slot is None:
            return self._move_alternatives(booking, start, language, now)
        text = self._texts.get(
            language,
            'reschedule_confirm',
            id=booking.id,
            old=self._texts.when(booking.start, language),
            new=self._texts.when(slot.start, language),
        )
        rows = [
            [Button(self._texts.get(language, 'btn_yes_move'), cb.join(cb.MOVE_YES, booking.id, raw_ts))],
            [Button(self._texts.get(language, 'btn_keep'), cb.join(cb.BOOKING, booking.id))],
        ]
        return [Outgoing(text=text, buttons=rows)]

    def _move_alternatives(
        self, booking: Booking, start: dt.datetime, language: str, now: dt.datetime
    ) -> list[Outgoing]:
        alternatives = self._service.nearest(
            booking.service_id, start, now, limit=_ALTERNATIVES, staff_id=booking.staff_id, ignore=booking.id
        )
        if not alternatives:
            return self._views.notice_and_menu(language, 'slot_taken_none')
        rows = self._views.suggestion_rows(
            alternatives,
            language,
            local_today(self._config, now),
            lambda slot: cb.join(cb.MOVE_TIME, booking.id, cb.encode_ts(slot.start)),
        )
        return [
            Outgoing(text=self._texts.get(language, 'slot_taken'), buttons=[*rows, [self._views.menu_button(language)]])
        ]

    async def _move_yes(
        self, booking_id: str, raw_ts: str, message: Incoming, language: str, now: dt.datetime
    ) -> list[Outgoing]:
        start = cb.decode_ts(raw_ts)
        if start is None:
            return self._stale(language)
        try:
            old, new = await self._service.reschedule(
                booking_id, start, now=now, by_admin=message.is_admin, customer=message.user
            )
        except CancellationTooLateError:
            return self._too_late_reply(language)
        except SlotTakenError:
            booking = self._own(booking_id, message, now)
            if booking is None:
                return self._views.notice_and_menu(language, 'not_active', id=booking_id)
            return self._move_alternatives(booking, start, language, now)
        except BookingError:
            return self._views.notice_and_menu(language, 'not_active', id=booking_id)
        text = self._texts.get(
            language,
            'rescheduled',
            id=new.id,
            when=self._texts.when(new.start, language),
            staff=self._texts.staff_name(new.staff_id),
        )
        return [
            Outgoing(
                text=text,
                buttons=[[Button(self._texts.get(language, 'btn_my'), cb.MY), self._views.menu_button(language)]],
            ),
            self._views.admin_rescheduled(old, new, by_admin=message.is_admin),
        ]

    # Свободный текст: модель предлагает, код проверяет

    async def _on_free_text(self, text: str, message: Incoming, conversation: Conversation, now: dt.datetime) -> Reply:
        lang = conversation.language
        fresh = conversation.reset()
        try:
            raw = await self._llm.extract(
                system=intent_system_prompt(self._config, self._texts, now),
                prompt=wrap_customer_message(text),
                schema=BookingIntent,
            )
        except LLMError as exc:
            await self._alert_operator(exc, now)
            return self._views.notice_and_menu(lang, 'not_understood'), fresh
        request = validate_intent(raw, self._config, now)
        match request.intent:
            case 'book':
                return self._book_from_text(request, fresh, now)
            case 'cancel' | 'reschedule':
                # Модель только выбрала экран: отмена — всё равно две явные кнопки покупателя.
                bookings = self._store.for_customer(message.user, now)
                return self._views.my_bookings(bookings, lang, intro_key='ai_my_bookings'), fresh
            case 'question':
                return await self._answer_question(text, message, fresh, now)
            case 'human':
                return self._forward_to_human(text, message, fresh, now)
            case 'greeting':
                return self._views.greeting(lang, clean_name(message.name) or ''), fresh
            case 'thanks':
                # «Спасибо» после записи — самое частое сообщение; меню целиком тут лишнее.
                welcome = Outgoing(
                    text=self._texts.get(lang, 'you_are_welcome'), buttons=[[self._views.menu_button(lang)]]
                )
                return [welcome], fresh
            case _:
                return self._views.notice_and_menu(lang, 'not_understood'), fresh

    def _book_from_text(self, request: BookingRequest, conversation: Conversation, now: dt.datetime) -> Reply:
        lang = conversation.language
        notes: list[str] = []
        if request.beyond_horizon:
            days = self._texts.days(self._config.booking.horizon_days, lang)
            notes.append(self._texts.get(lang, 'ai_beyond_horizon', days=days))
        if request.past_date:
            notes.append(self._texts.get(lang, 'ai_past_date'))
        service = self._config.booking.service(request.service_id) if request.service_id else None
        if service is None:
            remembered = msgspec.structs.replace(conversation, pref_day=request.day, pref_window=request.window)
            return [self._views.services(lang, intro='\n'.join(notes) or None)], remembered
        return self._propose(service, request.day, request.window, notes, lang, now), conversation

    def _propose(
        self,
        service: Service,
        day: dt.date | None,
        window: TimeWindow | None,
        notes: Sequence[str],
        language: str,
        now: dt.datetime,
    ) -> list[Outgoing]:
        zone = self._config.client.zone
        if day is not None:
            slots = [
                slot
                for slot in self._service.free_slots(service.id, day, now)
                if window is None or window.contains(self._minute(slot.start))
            ][:SUGGESTIONS]
        else:
            slots = self._service.next_slots(service.id, now, limit=SUGGESTIONS, window=window)
        key = 'ai_slots'
        if not slots:
            key = 'ai_nearest'
            if day is not None and window is not None and window.start is not None:
                target = wall_to_utc(day, window.start, zone)
                if target is not None:
                    slots = self._service.nearest(service.id, target, now, limit=_ALTERNATIVES)
            if not slots:
                slots = self._service.next_slots(service.id, now, limit=_ALTERNATIVES, start_day=day, window=window)
            if not slots:
                slots = self._service.next_slots(service.id, now, limit=_ALTERNATIVES, start_day=day)
        if not slots:
            return self._views.no_days(service.id, language)
        header = self._texts.get(language, key, service=self._texts.pick(service.name, language))
        rows = self._views.suggestion_rows(
            slots, language, local_today(self._config, now), self._views.slot_ref_data(cb.TIME, 'ai', None)
        )
        other_day = Button(self._texts.get(language, 'btn_other_day'), cb.join(cb.SERVICE, service.id))
        text = '\n\n'.join([*notes, header])
        return [Outgoing(text=text, buttons=[*rows, [other_day, self._views.menu_button(language)]])]

    def _minute(self, moment: dt.datetime) -> int:
        local = self._texts.local(moment)
        return local.hour * 60 + local.minute

    async def _answer_question(
        self, text: str, message: Incoming, conversation: Conversation, now: dt.datetime
    ) -> Reply:
        lang = conversation.language
        answer: str | None = None
        faq_ids: list[str] = []
        try:
            raw = await self._llm.extract(
                system=faq_system_prompt(self._config, self._texts, lang),
                prompt=wrap_customer_message(text),
                schema=FaqAnswer,
            )
        except LLMError as exc:
            await self._alert_operator(exc, now)
        else:
            answer = validate_answer(raw, self._config)
            faq_ids = sorted(set(raw.faq_ids))
        if answer is not None:
            self._service.record('booking.question_answered', now, {'faq': ','.join(faq_ids), 'language': lang})
            rows = [[Button(self._texts.get(lang, 'btn_book'), cb.BOOK), self._views.menu_button(lang)]]
            return [Outgoing(text=answer, buttons=rows)], conversation
        return self._handoff('unanswered', 'handoff', 'admin_question', text, message, lang, now), conversation

    def _forward_to_human(self, text: str, message: Incoming, conversation: Conversation, now: dt.datetime) -> Reply:
        lang = conversation.language
        return self._handoff('requested', 'human_sent', 'admin_human', text, message, lang, now), conversation

    def _handoff(
        self,
        reason: str,
        reply_key: str,
        alert_key: str,
        text: str,
        message: Incoming,
        lang: str,
        now: dt.datetime,
    ) -> list[Outgoing]:
        verdict = self._handoffs.decide(message.user, text, now)
        if verdict == Handoff.REPEAT:
            # Этот текст у владельца уже есть: «уже передали» — правда, второе уведомление — шум.
            return [Outgoing(text=self._texts.get(lang, 'handoff_pending'))]
        if verdict == Handoff.LIMITED:
            return self._handoff_refused(reason, message, lang, now)
        # Язык покупателя запоминается: ответ владельца через /reply придёт на нём.
        self._store.save_customer(Customer(key=message.user, language=lang), now)
        self._service.record('booking.handoff', now, {'reason': reason, 'language': lang})
        return [Outgoing(text=self._texts.get(lang, reply_key)), self._admin_alert(alert_key, text, message)]

    def _handoff_refused(self, reason: str, message: Incoming, lang: str, now: dt.datetime) -> list[Outgoing]:
        # Поток просьб не становится потоком уведомлений, но и вопрос не теряется молча:
        # покупатель знает, что это сообщение не передано, и может позвонить.
        logger.info('просьба позвать человека от %s не передана: лимит уведомлений', message.user)
        self._service.record('booking.handoff_throttled', now, {'reason': reason, 'language': lang})
        text = self._texts.get(lang, 'handoff_limit')
        phone = self._config.booking.contact_phone
        if phone:
            text = f'{text}\n{self._texts.get(lang, "call_us", phone=phone)}'
        return [Outgoing(text=text, buttons=[[self._views.menu_button(lang)]])]

    def _admin_alert(self, key: str, text: str, message: Incoming) -> Outgoing:
        customer = message.name or message.user
        if message.handle:
            customer = f'{customer} ({message.handle})'
        body = self._texts.get(self._config.owner_language, key, customer=customer, text=text[:1000], user=message.user)
        return Outgoing(to=ADMINS, text=body)

    async def _alert_operator(self, error: LLMError, now: dt.datetime) -> None:
        logger.warning('модель недоступна, покупателю показано меню: %s', error)
        if self._operator is None or (self._last_alert is not None and now - self._last_alert < _ALERT_EVERY):
            return
        self._last_alert = now
        try:
            await self._operator.send(f'⚠️ {self._config.client.name} / booking: модель недоступна: {error}')
        except NotifyError as exc:
            logger.error('оператор не уведомлён о сбое модели: %s', exc)

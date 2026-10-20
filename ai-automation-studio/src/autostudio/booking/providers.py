"""dishka-провайдеры пакета booking поверх `CoreProvider`.

`BookingProvider` собирает всё, что не зависит от канала (база, журнал, таблица,
модель, Google Calendar, движок), — им пользуются `run`, `chat` и демо. `ChannelProvider`
добавляет каналы боевого `run`: бота Telegram, WhatsApp (если настроен), общую отправку
по префиксу ключа чата и цикл напоминаний — консольному чату и демо не нужны токены.

Два решения, которые отличают боевой режим от демо:

- относительный `sink.csv_dir` считается от каталога данных клиента (`/data/<id>/…` в
  контейнере), а не от рабочего каталога процесса: в контейнере писать можно только в `/data`;
- уведомления владельцу в боевом режиме идут только в настоящие каналы (бот, Telegram-чат,
  вебхук). Консольный канал ядра там не используется: в нём имена, телефоны и тексты
  вопросов покупателей попали бы в stdout, а значит — в логи контейнера.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import httpx
import msgspec
from aiogram import Bot
from aiogram.utils.token import TokenValidationError
from dishka import Provider, Scope, from_context, provide

from autostudio.booking.admin import AdminDesk
from autostudio.booking.alarm import OperatorAlarm
from autostudio.booking.calsync import CalendarAvailability, CalendarSync
from autostudio.booking.config import BookingConfig
from autostudio.booking.engine import Engine
from autostudio.booking.gcal import CalendarApi, GoogleCalendar
from autostudio.booking.heuristics import booking_fake_llm
from autostudio.booking.messages import RoutingSender, Sender
from autostudio.booking.relay import Relay
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.service import BookingService
from autostudio.booking.store import BookingStore
from autostudio.booking.telegram import BookingBot, TelegramSender
from autostudio.booking.texts import Texts
from autostudio.booking.views import Views
from autostudio.booking.whatsapp import WhatsAppBot, WhatsAppSender
from autostudio.clientconf import NotifyConfig, SinkConfig, client_data_dir, events_path, require_env
from autostudio.clock import Clock
from autostudio.errors import ConfigError, LLMError
from autostudio.events import EventLog
from autostudio.llm import LLMClient, make_llm
from autostudio.notify import Notifier, make_notifier
from autostudio.providers import OperatorAlerts
from autostudio.runlog import Heartbeat
from autostudio.settings import Settings
from autostudio.sinks import TableSink, make_sink

STORE_FILE: Final = 'booking.sqlite'


class CalendarLink:
    """Календарь клиента для контейнера: API или None, если `[booking.calendar]` не настроен.

    Args:
        api: Google Calendar (или заглушка демо) либо None.
    """

    def __init__(self, api: CalendarApi | None) -> None:
        self.api = api


class CalendarParts:
    """Занятость и синхронизация событий, собранные поверх `CalendarLink` (оба None без календаря).

    Args:
        availability: Кеш занятости.
        sync: Запись событий.
    """

    def __init__(self, availability: CalendarAvailability | None, sync: CalendarSync | None) -> None:
        self.availability = availability
        self.sync = sync


class WhatsAppLink:
    """Отправка в WhatsApp или None, если `[booking.whatsapp]` не настроен.

    Args:
        sender: Отправитель WhatsApp или None.
    """

    def __init__(self, sender: WhatsAppSender | None) -> None:
        self.sender = sender


class WhatsAppEndpoint:
    """Вебхук WhatsApp или None.

    Args:
        bot: Обработчик вебхука или None.
    """

    def __init__(self, bot: WhatsAppBot | None) -> None:
        self.bot = bot


def resolve_sink(sink: SinkConfig, settings: Settings, config: BookingConfig) -> SinkConfig:
    """`[sink]` с `csv_dir`, привязанным к каталогу данных клиента, если путь относительный.

    Args:
        sink: Секция `[sink]` из конфига.
        settings: Настройки процесса (`data_dir`).
        config: Конфиг клиента.

    Returns:
        Та же секция с абсолютным или привязанным к данным клиента `csv_dir`.
    """
    if sink.csv_dir.is_absolute():
        return sink
    return msgspec.structs.replace(sink, csv_dir=client_data_dir(settings.data_dir, config.client) / sink.csv_dir)


def owner_notifier(notify: NotifyConfig, http: httpx.AsyncClient) -> Notifier | None:
    """Канал `[notify]` для боевого режима: без консоли; None, если настоящих каналов нет.

    Args:
        notify: Секция `[notify]`.
        http: Общий httpx-клиент.

    Returns:
        Telegram-чат и/или вебхук владельца или None.
    """
    if not (notify.telegram_chat_id or notify.webhook_url_env):
        return None
    return make_notifier(msgspec.structs.replace(notify, console=False), http)


def csv_dir_problem(path: Path) -> str | None:
    """Почему в каталог CSV не получится писать, или None (проверка без создания файлов).

    Args:
        path: Каталог выгрузок.

    Returns:
        Описание проблемы для `check` или None.
    """
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return f'{path}: нет ни каталога, ни родителя'
        probe = probe.parent
    if not probe.is_dir():
        return f'{probe} — не каталог'
    if not os.access(probe, os.W_OK | os.X_OK):
        return f'{probe}: нет прав на запись для этого пользователя'
    return None


class BookingProvider(Provider):
    """Журнал, база записей, таблица, уведомления, модель, движок."""

    scope = Scope.APP

    config = from_context(provides=BookingConfig, scope=Scope.APP)

    @provide
    def events(self, settings: Settings, config: BookingConfig) -> EventLog:
        return EventLog(events_path(settings.data_dir, config.client))

    @provide
    def store(self, settings: Settings, config: BookingConfig) -> BookingStore:
        return BookingStore(client_data_dir(settings.data_dir, config.client) / STORE_FILE)

    @provide
    def sink(self, settings: Settings, config: BookingConfig) -> TableSink:
        return make_sink(resolve_sink(config.sink, settings, config))

    @provide
    async def llm(
        self, settings: Settings, config: BookingConfig, operator: OperatorAlerts
    ) -> AsyncIterator[LLMClient]:
        notifier = operator.notifier

        async def degraded(error: LLMError) -> None:
            # Ядро шлёт этот сигнал не чаще раза в час: бот тем временем работает на эвристиках.
            # Модель, не справившаяся с одной репликой (`content`), — не повод будить оператора.
            if notifier is None or error.kind == 'content':
                return
            what = 'проверьте ключ, права и модель' if error.kind == 'config' else 'провайдер недоступен'
            await notifier.send(f'⚠️ {config.client.name} / booking: модель недоступна ({what}), бот на правилах: {error}')

        client = make_llm(settings, booking_fake_llm(), on_fallback=degraded)
        yield client
        await client.aclose()

    @provide
    def texts(self, config: BookingConfig) -> Texts:
        return Texts(config.booking, config.client.zone)

    @provide
    def views(self, config: BookingConfig, texts: Texts) -> Views:
        return Views(config, texts)

    @provide
    def alarm(self, config: BookingConfig, operator: OperatorAlerts, clock: Clock) -> OperatorAlarm:
        return OperatorAlarm(operator.notifier, f'{config.client.name} / booking', clock=clock)

    @provide
    def calendar_link(self, config: BookingConfig, http: httpx.AsyncClient) -> CalendarLink:
        section = config.booking.calendar
        if section is None:
            return CalendarLink(None)
        # google-auth — в extras `sheets`: импорт только при настроенном календаре.
        from autostudio.booking.gauth import ServiceAccountTokens

        key = Path(require_env(section.credentials_env, os.environ))
        return CalendarLink(GoogleCalendar(http, ServiceAccountTokens(key)))

    @provide
    def calendar_parts(
        self,
        config: BookingConfig,
        link: CalendarLink,
        store: BookingStore,
        texts: Texts,
        alarm: OperatorAlarm,
        clock: Clock,
    ) -> CalendarParts:
        if link.api is None:
            return CalendarParts(None, None)
        availability = CalendarAvailability(config=config, api=link.api, alarm=alarm, clock=clock)
        sync = CalendarSync(
            config=config, store=store, api=link.api, texts=texts, alarm=alarm, clock=clock, availability=availability
        )
        return CalendarParts(availability, sync)

    @provide
    def service(
        self,
        config: BookingConfig,
        store: BookingStore,
        events: EventLog,
        sink: TableSink,
        texts: Texts,
        calendar: CalendarParts,
    ) -> BookingService:
        return BookingService(
            config=config,
            store=store,
            events=events,
            sink=sink,
            texts=texts,
            availability=calendar.availability,
            calendar_sync=calendar.sync,
        )

    @provide
    def admin(self, config: BookingConfig, texts: Texts, service: BookingService) -> AdminDesk:
        return AdminDesk(config=config, texts=texts, service=service)

    @provide
    def engine(
        self,
        config: BookingConfig,
        views: Views,
        service: BookingService,
        admin: AdminDesk,
        llm: LLMClient,
        clock: Clock,
        operator: OperatorAlerts,
    ) -> Engine:
        return Engine(
            config=config,
            views=views,
            service=service,
            admin=admin,
            llm=llm,
            clock=clock,
            operator=operator.notifier,
        )


class ChannelProvider(Provider):
    """Каналы боевого `run`: бот Telegram, WhatsApp (если настроен), общая отправка и цикл напоминаний."""

    scope = Scope.APP

    @provide
    async def bot(self, config: BookingConfig) -> AsyncIterator[Bot]:
        token = require_env(config.booking.bot_token_env, os.environ)
        try:
            bot = Bot(token)
        except TokenValidationError:
            # Сам токен в текст ошибки не попадает.
            raise ConfigError(f'{config.booking.bot_token_env}: это не токен бота от @BotFather') from None
        yield bot
        await bot.session.close()

    @provide
    def telegram_sender(self, bot: Bot) -> TelegramSender:
        return TelegramSender(bot)

    @provide
    def whatsapp_link(
        self,
        config: BookingConfig,
        http: httpx.AsyncClient,
        store: BookingStore,
        texts: Texts,
        events: EventLog,
        alarm: OperatorAlarm,
        clock: Clock,
    ) -> WhatsAppLink:
        section = config.booking.whatsapp
        if section is None:
            return WhatsAppLink(None)
        sender = WhatsAppSender(
            http=http,
            config=config,
            token=require_env(section.token_env, os.environ),
            store=store,
            texts=texts,
            events=events,
            alarm=alarm,
            clock=clock,
        )
        return WhatsAppLink(sender)

    @provide
    def sender(self, telegram: TelegramSender, whatsapp: WhatsAppLink) -> Sender:
        # Ответ администратора из Telegram покупателю в WhatsApp (`/reply wa:…`) и уведомление
        # владельцу о записи из WhatsApp идут через один отправитель — по префиксу ключа чата.
        routes: dict[str, Sender] = {'tg': telegram}
        if whatsapp.sender is not None:
            routes['wa'] = whatsapp.sender
        return RoutingSender(routes)

    @provide
    def booking_bot(
        self,
        config: BookingConfig,
        engine: Engine,
        sender: Sender,
        texts: Texts,
        http: httpx.AsyncClient,
        alarm: OperatorAlarm,
    ) -> BookingBot:
        return BookingBot(
            config=config,
            engine=engine,
            sender=sender,
            texts=texts,
            notifier=owner_notifier(config.notify, http),
            alarm=alarm,
        )

    @provide
    def whatsapp_endpoint(
        self,
        config: BookingConfig,
        engine: Engine,
        sender: Sender,
        whatsapp: WhatsAppLink,
        store: BookingStore,
        texts: Texts,
        http: httpx.AsyncClient,
        alarm: OperatorAlarm,
        clock: Clock,
    ) -> WhatsAppEndpoint:
        section = config.booking.whatsapp
        if section is None or whatsapp.sender is None:
            return WhatsAppEndpoint(None)
        admins = [f'tg:{chat_id}' for chat_id in config.booking.admin_chat_ids]
        relay = Relay(
            engine=engine,
            sender=sender,
            texts=texts,
            admins=admins,
            notifier=owner_notifier(config.notify, http),
            alarm=alarm,
        )
        bot = WhatsAppBot(
            config=config,
            relay=relay,
            wa_sender=whatsapp.sender,
            store=store,
            texts=texts,
            app_secret=require_env(section.app_secret_env, os.environ),
            verify_token=require_env(section.verify_token_env, os.environ),
            alarm=alarm,
            clock=clock,
        )
        return WhatsAppEndpoint(bot)

    @provide
    def reminder_loop(
        self,
        config: BookingConfig,
        store: BookingStore,
        sender: Sender,
        sink: TableSink,
        events: EventLog,
        texts: Texts,
        heartbeat: Heartbeat,
        operator: OperatorAlerts,
        clock: Clock,
        calendar: CalendarParts,
    ) -> ReminderLoop:
        return ReminderLoop(
            config=config,
            store=store,
            sender=sender,
            sink=sink,
            events=events,
            texts=texts,
            heartbeat=heartbeat,
            operator=operator.notifier,
            clock=clock,
            calendar_sync=calendar.sync,
        )

"""dishka-провайдеры пакета booking поверх `CoreProvider`.

`BookingProvider` собирает всё, что не зависит от канала (база, журнал, таблица,
модель, движок), — им пользуются `run`, `chat` и демо. `TelegramProvider` добавляет
бота, отправку и цикл напоминаний — только для боевого `run`, чтобы консольному
чату и демо не нужен был токен.

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

import httpx
import msgspec
from aiogram import Bot
from aiogram.utils.token import TokenValidationError
from dishka import Provider, Scope, from_context, provide

from autostudio.booking.admin import AdminDesk
from autostudio.booking.config import BookingConfig
from autostudio.booking.engine import Engine
from autostudio.booking.heuristics import booking_fake_llm
from autostudio.booking.messages import Sender
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.service import BookingService
from autostudio.booking.store import BookingStore
from autostudio.booking.telegram import BookingBot, TelegramSender
from autostudio.booking.texts import Texts
from autostudio.booking.views import Views
from autostudio.clientconf import NotifyConfig, SinkConfig, client_data_dir, require_env
from autostudio.clock import Clock
from autostudio.errors import ConfigError, LLMError
from autostudio.events import EventLog
from autostudio.llm import LLMClient, make_llm
from autostudio.notify import Notifier, make_notifier
from autostudio.providers import OperatorAlerts
from autostudio.runlog import Heartbeat
from autostudio.settings import Settings
from autostudio.sinks import TableSink, make_sink

EVENTS_FILE = 'events.sqlite'
STORE_FILE = 'booking.sqlite'


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
        return EventLog(client_data_dir(settings.data_dir, config.client) / EVENTS_FILE)

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
            if notifier is not None:
                await notifier.send(f'⚠️ {config.client.name} / booking: модель недоступна, бот на правилах: {error}')

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
    def service(
        self, config: BookingConfig, store: BookingStore, events: EventLog, sink: TableSink, texts: Texts
    ) -> BookingService:
        return BookingService(config=config, store=store, events=events, sink=sink, texts=texts)

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


class TelegramProvider(Provider):
    """Бот Telegram, отправка через него и цикл напоминаний (боевой `run`)."""

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
    def sender(self, bot: Bot) -> Sender:
        return TelegramSender(bot)

    @provide
    def booking_bot(
        self,
        config: BookingConfig,
        engine: Engine,
        sender: Sender,
        texts: Texts,
        http: httpx.AsyncClient,
        operator: OperatorAlerts,
        clock: Clock,
    ) -> BookingBot:
        return BookingBot(
            config=config,
            engine=engine,
            sender=sender,
            texts=texts,
            notifier=owner_notifier(config.notify, http),
            operator=operator.notifier,
            clock=clock,
        )

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
        )

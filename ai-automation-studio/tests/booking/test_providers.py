import pytest
from aiogram import Bot
from dishka import Provider, Scope, make_async_container, provide

from autostudio.booking.config import BookingConfig
from autostudio.booking.engine import Engine
from autostudio.booking.messages import RoutingSender, Sender
from autostudio.booking.providers import BookingProvider, ChannelProvider
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.store import BookingStore
from autostudio.booking.telegram import BookingBot, TelegramSender
from autostudio.errors import ConfigError, LLMError
from autostudio.events import EventLog
from autostudio.llm import FakeLLM, LLMClient, LLMUsage, ResilientLLM
from autostudio.providers import CoreProvider, OperatorAlerts
from autostudio.settings import LLMProvider, Settings
from tests.booking.helpers import Recorder


def container(tmp_path, config: BookingConfig, *, telegram: bool = False):
    providers = [CoreProvider(), BookingProvider(), *([ChannelProvider()] if telegram else [])]
    settings = Settings(data_dir=tmp_path / 'data')
    return make_async_container(*providers, context={Settings: settings, BookingConfig: config})


async def test__booking_provider__container__engine_fake_llm_client_dirs(tmp_path, config):
    built = container(tmp_path, config)
    try:
        engine = await built.get(Engine)
        assert isinstance(engine, Engine)
        assert isinstance(await built.get(LLMClient), FakeLLM)
        store = await built.get(BookingStore)
        assert store.path == tmp_path / 'data' / 'test-barbers' / 'booking.sqlite'
        assert isinstance(await built.get(EventLog), EventLog)
    finally:
        await built.close()


async def test__telegram_provider__valid_token__bot_sender_loop_built(tmp_path, config, monkeypatch):
    monkeypatch.setenv('BOOKING_BOT_TOKEN', '123456:' + 'A' * 35)
    built = container(tmp_path, config, telegram=True)
    bot = await built.get(Bot)
    assert isinstance(await built.get(Sender), RoutingSender)
    assert isinstance(await built.get(TelegramSender), TelegramSender)
    assert isinstance(await built.get(BookingBot), BookingBot)
    assert isinstance(await built.get(ReminderLoop), ReminderLoop)
    await built.close()
    assert bot.token.startswith('123456:')


async def test__telegram_provider__missing_or_malformed_token__config_error_without_secret(
    tmp_path, config, monkeypatch
):
    monkeypatch.delenv('BOOKING_BOT_TOKEN', raising=False)
    built = container(tmp_path, config, telegram=True)
    with pytest.raises(ConfigError, match='BOOKING_BOT_TOKEN'):
        await built.get(Bot)
    await built.close()
    monkeypatch.setenv('BOOKING_BOT_TOKEN', 'not-a-token-secret-value')
    built = container(tmp_path, config, telegram=True)
    with pytest.raises(ConfigError) as caught:
        await built.get(Bot)
    assert 'not-a-token-secret-value' not in str(caught.value)
    await built.close()


class _Operator(Provider):
    scope = Scope.APP

    def __init__(self, recorder: Recorder) -> None:
        super().__init__()
        self._recorder = recorder

    @provide
    def operator(self) -> OperatorAlerts:
        return OperatorAlerts(self._recorder)


async def test__booking_provider__real_provider__resilient_llm_alerts_operator_and_closes(
    tmp_path, config, monkeypatch
):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    recorder = Recorder()
    settings = Settings(llm_provider=LLMProvider.OPENAI, llm_model='gpt-5.4-mini', data_dir=tmp_path / 'data')
    built = make_async_container(
        CoreProvider(),
        BookingProvider(),
        _Operator(recorder),
        context={Settings: settings, BookingConfig: config},
    )
    llm = await built.get(LLMClient)
    assert isinstance(llm, ResilientLLM)
    # Хук, который ядро зовёт при деградации: недоступность — сигнал оператору, а модель, не
    # справившаяся с одной репликой (`content`), — нет.
    await llm._on_fallback(LLMError('OpenAI 429: quota', status_code=429, kind='outage'))
    await llm._on_fallback(LLMError('OpenAI 401: key', status_code=401, kind='config'))
    await llm._on_fallback(LLMError('ответ не по схеме'))
    assert recorder.texts == [
        '⚠️ Test Barbers / booking: модель недоступна (провайдер недоступен), бот на правилах: OpenAI 429: quota',
        '⚠️ Test Barbers / booking: модель недоступна (проверьте ключ, права и модель), бот на правилах: OpenAI 401: key',
    ]
    await built.close()


class FailingModel:
    """Модель провайдера, которая падает заданными ошибками по очереди."""

    model = 'gpt-test'

    def __init__(self, errors: list[LLMError]) -> None:
        self.errors = errors
        self.usage = LLMUsage()

    async def extract(self, *, system, prompt, schema):
        raise self.errors.pop(0)

    async def write(self, *, system, prompt):
        raise self.errors.pop(0)

    async def aclose(self) -> None:
        return None


async def test__booking_provider__refused_reply_then_key_revoked__key_alert_not_swallowed(
    tmp_path, config, monkeypatch
):
    from autostudio.booking import providers

    errors = [
        LLMError('ответ не по схеме'),  # одна реплика — не повод будить оператора и занимать слот сигнала
        LLMError('OpenAI 401: key', status_code=401, kind='config'),
        LLMError('OpenAI 401: key', status_code=401, kind='config'),
        LLMError('OpenAI 503', status_code=503, kind='outage'),
    ]
    monkeypatch.setattr(providers, 'make_llm', lambda settings: FailingModel(errors))
    recorder = Recorder()
    settings = Settings(llm_provider=LLMProvider.OPENAI, llm_model='gpt-5.4-mini', data_dir=tmp_path / 'data')
    built = make_async_container(
        CoreProvider(), BookingProvider(), _Operator(recorder), context={Settings: settings, BookingConfig: config}
    )
    llm = await built.get(LLMClient)
    for _ in range(4):
        # Бот не встаёт: каждую реплику отвечают эвристики пакета.
        assert isinstance(await llm.write(system='s', prompt='p'), str)
    await built.close()
    # Ключ — сразу после «плохой реплики», второй раз за час — тишина; сбой провайдера — свой сигнал.
    assert len(recorder.texts) == 2
    assert 'проверьте ключ' in recorder.texts[0] and 'провайдер недоступен' in recorder.texts[1]


async def test__booking_provider__no_operator__degradation_hook_silent(tmp_path, config, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    settings = Settings(llm_provider=LLMProvider.OPENAI, llm_model='gpt-5.4-mini', data_dir=tmp_path / 'data')
    built = make_async_container(CoreProvider(), BookingProvider(), context={Settings: settings, BookingConfig: config})
    llm = await built.get(LLMClient)
    await llm._on_fallback(LLMError('down', kind='outage'))
    await built.close()


async def test__owner_notifier__console_only_or_channels__none_or_real_channel(monkeypatch):
    import httpx

    from autostudio.booking.providers import owner_notifier
    from autostudio.clientconf import NotifyConfig
    from autostudio.notify import ConsoleNotifier, WebhookNotifier

    async with httpx.AsyncClient() as http:
        # Консоль в боевом режиме не канал: имена и телефоны не должны попадать в логи.
        assert owner_notifier(NotifyConfig(console=True), http) is None
        assert owner_notifier(NotifyConfig(), http) is None
        monkeypatch.setenv('HOOK', 'https://hooks.example.test/x')
        channel = owner_notifier(NotifyConfig(console=True, webhook_url_env='HOOK'), http)
        assert isinstance(channel, WebhookNotifier) and not isinstance(channel, ConsoleNotifier)


async def test__telegram_provider__console_notify__owner_alert_not_printed(tmp_path, monkeypatch, capsys):
    import msgspec

    from autostudio.booking.messages import ADMINS, Outgoing, deliver
    from autostudio.clientconf import NotifyConfig
    from tests.booking.helpers import make_config

    monkeypatch.setenv('BOOKING_BOT_TOKEN', '123456:' + 'A' * 35)
    config = msgspec.structs.replace(make_config(), notify=NotifyConfig(console=True))
    built = container(tmp_path, config, telegram=True)
    booking_bot = await built.get(BookingBot)
    assert booking_bot.notifier is None
    alert = Outgoing(to=ADMINS, text='Customer: Anna, +995 555 12 34 56')
    await deliver(Recorder(), 'tg:1', [alert], admins=booking_bot.admin_keys, notifier=booking_bot.notifier)
    assert '+995' not in capsys.readouterr().out
    await built.close()


def test__resolve_sink__relative_csv_dir__under_client_data_dir(tmp_path, config):
    from pathlib import Path

    from autostudio.booking.providers import resolve_sink
    from autostudio.clientconf import SinkConfig

    settings = Settings(data_dir=Path('/data'))
    assert resolve_sink(SinkConfig(csv_dir=Path('out')), settings, config).csv_dir == Path('/data/test-barbers/out')
    absolute = SinkConfig(csv_dir=tmp_path / 'abs')
    assert resolve_sink(absolute, settings, config) is absolute


async def test__booking_provider__sink__csv_written_under_data_dir(tmp_path, config):
    from autostudio.sinks import CsvSink, TableSink

    built = container(tmp_path, config)
    sink = await built.get(TableSink)
    assert isinstance(sink, CsvSink)
    assert sink.path_for('Bookings') == tmp_path / 'data' / 'test-barbers' / 'var' / 'out' / 'Bookings.csv'
    await built.close()


def test__csv_dir_problem__writable_file_in_way_or_no_rights__reported(tmp_path, monkeypatch):
    from autostudio.booking import providers

    assert providers.csv_dir_problem(tmp_path / 'new' / 'out') is None
    blocker = tmp_path / 'file'
    blocker.write_text('x', encoding='utf-8')
    assert 'не каталог' in providers.csv_dir_problem(blocker / 'out')
    monkeypatch.setattr(providers.os, 'access', lambda path, mode: False)
    assert 'нет прав на запись' in providers.csv_dir_problem(tmp_path / 'out')


async def test__booking_provider__calendar_configured__google_calendar_and_service_wired(tmp_path, monkeypatch):
    from autostudio.booking.gcal import GoogleCalendar
    from autostudio.booking.providers import CalendarLink, CalendarParts
    from autostudio.booking.service import BookingService
    from tests.booking.helpers import make_config

    config = make_config(calendar={'calendar_id': 'salon@x'})
    monkeypatch.delenv('GOOGLE_APPLICATION_CREDENTIALS', raising=False)
    built = container(tmp_path, config)
    with pytest.raises(ConfigError, match='GOOGLE_APPLICATION_CREDENTIALS'):
        await built.get(CalendarLink)
    await built.close()
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', str(tmp_path / 'key.json'))
    built = container(tmp_path, config)
    assert isinstance((await built.get(CalendarLink)).api, GoogleCalendar)
    calendar = await built.get(CalendarParts)
    assert calendar.availability is not None and calendar.sync is not None
    assert isinstance(await built.get(BookingService), BookingService)
    await built.close()
    plain = container(tmp_path, make_config())
    parts = await plain.get(CalendarParts)
    assert parts.availability is None and parts.sync is None
    await plain.close()


async def test__channel_provider__whatsapp__routing_sender_and_endpoint(tmp_path, monkeypatch):
    from autostudio.booking.providers import WhatsAppEndpoint
    from autostudio.booking.whatsapp import WhatsAppBot
    from tests.booking.helpers import make_config

    monkeypatch.setenv('BOOKING_BOT_TOKEN', '123456:' + 'A' * 35)
    for name in ('WHATSAPP_TOKEN', 'WHATSAPP_APP_SECRET', 'WHATSAPP_VERIFY_TOKEN'):
        monkeypatch.setenv(name, f'{name.lower()}-value')
    built = container(tmp_path, make_config(whatsapp={'phone_number_id': '106540352242922'}), telegram=True)
    sender = await built.get(Sender)
    assert isinstance(sender, RoutingSender) and set(sender._routes) == {'tg', 'wa'}
    assert isinstance((await built.get(WhatsAppEndpoint)).bot, WhatsAppBot)
    await built.close()
    plain = container(tmp_path, make_config(), telegram=True)
    assert (await plain.get(WhatsAppEndpoint)).bot is None
    assert set((await plain.get(Sender))._routes) == {'tg'}
    await plain.close()

import pytest
from aiogram import Bot
from dishka import Provider, Scope, make_async_container, provide

from autostudio.booking.config import BookingConfig
from autostudio.booking.engine import Engine
from autostudio.booking.messages import Sender
from autostudio.booking.providers import BookingProvider, TelegramProvider
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.store import BookingStore
from autostudio.booking.telegram import BookingBot, TelegramSender
from autostudio.errors import ConfigError, LLMError
from autostudio.events import EventLog
from autostudio.llm import FakeLLM, LLMClient, ResilientLLM
from autostudio.providers import CoreProvider, OperatorAlerts
from autostudio.settings import LLMProvider, Settings
from tests.booking.helpers import Recorder


def container(tmp_path, config: BookingConfig, *, telegram: bool = False):
    providers = [CoreProvider(), BookingProvider(), *([TelegramProvider()] if telegram else [])]
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
    assert isinstance(await built.get(Sender), TelegramSender)
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
    await llm._on_fallback(LLMError('OpenAI 429: quota'))  # хук, который ядро зовёт при деградации
    assert recorder.texts == ['⚠️ Test Barbers / booking: модель недоступна, бот на правилах: OpenAI 429: quota']
    await built.close()


async def test__booking_provider__no_operator__degradation_hook_silent(tmp_path, config, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    settings = Settings(llm_provider=LLMProvider.OPENAI, llm_model='gpt-5.4-mini', data_dir=tmp_path / 'data')
    built = make_async_container(CoreProvider(), BookingProvider(), context={Settings: settings, BookingConfig: config})
    llm = await built.get(LLMClient)
    await llm._on_fallback(LLMError('down'))
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
    assert booking_bot._notifier is None
    alert = Outgoing(to=ADMINS, text='Customer: Anna, +995 555 12 34 56')
    await deliver(Recorder(), 'tg:1', [alert], admins=booking_bot.admin_keys, notifier=booking_bot._notifier)
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

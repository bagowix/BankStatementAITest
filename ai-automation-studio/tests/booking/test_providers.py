import pytest
from aiogram import Bot
from dishka import make_async_container

from autostudio.booking.config import BookingConfig
from autostudio.booking.engine import Engine
from autostudio.booking.messages import Sender
from autostudio.booking.providers import BookingProvider, TelegramProvider
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.store import BookingStore
from autostudio.booking.telegram import BookingBot, TelegramSender
from autostudio.errors import ConfigError
from autostudio.events import EventLog
from autostudio.llm import FakeLLM, LLMClient
from autostudio.providers import CoreProvider
from autostudio.settings import Settings


def container(tmp_path, config: BookingConfig, *, telegram: bool = False):
    providers = [CoreProvider(), BookingProvider(), *([TelegramProvider()] if telegram else [])]
    settings = Settings(data_dir=tmp_path / 'data')
    return make_async_container(*providers, context={Settings: settings, BookingConfig: config})


async def test__booking_provider__builds_engine_with_fake_llm_and_client_dirs(tmp_path, config):
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


async def test__telegram_provider__bot_sender_loop_and_session_closed(tmp_path, config, monkeypatch):
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

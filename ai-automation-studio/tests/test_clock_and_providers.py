import datetime as dt

import httpx
import pytest
from dishka import make_async_container

from autostudio.clock import FrozenClock, system_now
from autostudio.providers import CoreProvider, OperatorAlerts
from autostudio.runlog import Heartbeat
from autostudio.settings import Settings


def test__system_now__aware_utc():
    assert system_now().tzinfo is dt.UTC


def test__frozen_clock__advance_and_set():
    clock = FrozenClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    clock.advance(dt.timedelta(hours=2))
    assert clock().hour == 2
    clock.set(dt.datetime(2026, 5, 1, tzinfo=dt.UTC))
    assert clock().month == 5
    with pytest.raises(ValueError):
        clock.set(dt.datetime(2026, 5, 1))  # noqa: DTZ001
    with pytest.raises(ValueError):
        FrozenClock(dt.datetime(2026, 5, 1))  # noqa: DTZ001


async def test__core_provider__builds_and_closes(monkeypatch):
    monkeypatch.setenv('AUTOSTUDIO_OPERATOR_BOT_TOKEN', 't')
    settings = Settings.from_env({'AUTOSTUDIO_OPERATOR_CHAT_ID': '1'})
    container = make_async_container(CoreProvider(), context={Settings: settings})
    http = await container.get(httpx.AsyncClient)
    assert isinstance(await container.get(Heartbeat), Heartbeat)
    assert (await container.get(OperatorAlerts)).notifier is not None
    await container.close()
    assert http.is_closed


async def test__core_provider__no_operator__none():
    container = make_async_container(CoreProvider(), context={Settings: Settings.from_env({})})
    assert (await container.get(OperatorAlerts)).notifier is None
    await container.close()

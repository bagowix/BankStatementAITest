import io

import httpx
import pytest
import respx

from autostudio.clientconf import NotifyConfig
from autostudio.errors import NotifyError
from autostudio.notify import (
    ConsoleNotifier,
    FanoutNotifier,
    TelegramNotifier,
    WebhookNotifier,
    clip,
    make_notifier,
)


@respx.mock
async def test__telegram_send__ok__posts_chat_and_text():
    route = respx.post('https://api.telegram.org/bot123:abc/sendMessage').mock(
        return_value=httpx.Response(200, json={'ok': True})
    )
    async with httpx.AsyncClient() as http:
        await TelegramNotifier(http, '123:abc', '42').send('hi')
    assert route.called
    assert route.calls[0].request.content == b'{"chat_id":"42","text":"hi"}'


@respx.mock
async def test__telegram_send__network_error__no_token_in_message():
    respx.post(url__startswith='https://api.telegram.org/').mock(side_effect=httpx.ConnectError('down'))
    async with httpx.AsyncClient() as http:
        with pytest.raises(NotifyError) as info:
            await TelegramNotifier(http, 'SECRET-TOKEN', '42').send('hi')
    assert 'SECRET-TOKEN' not in str(info.value)


@respx.mock
async def test__telegram_send__api_error__notify_error():
    respx.post(url__startswith='https://api.telegram.org/').mock(return_value=httpx.Response(403, text='blocked'))
    async with httpx.AsyncClient() as http:
        with pytest.raises(NotifyError, match='403'):
            await TelegramNotifier(http, 't', '42').send('hi')


@respx.mock
async def test__webhook_send__ok_and_error():
    respx.post('https://hooks.example.test/a').mock(return_value=httpx.Response(204))
    respx.post('https://hooks.example.test/b').mock(return_value=httpx.Response(500))
    respx.post('https://hooks.example.test/c').mock(side_effect=httpx.ReadTimeout('slow'))
    async with httpx.AsyncClient() as http:
        await WebhookNotifier(http, 'https://hooks.example.test/a').send('x')
        with pytest.raises(NotifyError, match='500'):
            await WebhookNotifier(http, 'https://hooks.example.test/b').send('x')
        with pytest.raises(NotifyError, match='ReadTimeout'):
            await WebhookNotifier(http, 'https://hooks.example.test/c').send('x')


async def test__console_send__prints():
    stream = io.StringIO()
    await ConsoleNotifier(stream).send('новая запись')
    assert 'новая запись' in stream.getvalue()


class _Failing:
    async def send(self, text):
        raise NotifyError('nope')


async def test__fanout__one_fails__others_delivered():
    stream = io.StringIO()
    await FanoutNotifier([_Failing(), ConsoleNotifier(stream)]).send('ok')
    assert 'ok' in stream.getvalue()


async def test__fanout__all_fail__notify_error():
    with pytest.raises(NotifyError, match='nope'):
        await FanoutNotifier([_Failing(), _Failing()]).send('x')


def test__clip__long__ellipsis():
    assert clip('x' * 5000).endswith('…')
    assert len(clip('x' * 5000)) == 4000
    assert clip('short') == 'short'


async def test__make_notifier__variants(monkeypatch):
    async with httpx.AsyncClient() as http:
        assert isinstance(make_notifier(NotifyConfig(), http), ConsoleNotifier)
        monkeypatch.setenv('AUTOSTUDIO_NOTIFY_BOT_TOKEN', 't')
        assert isinstance(make_notifier(NotifyConfig(telegram_chat_id='1'), http), TelegramNotifier)
        monkeypatch.setenv('HOOK', 'https://hooks.example.test/a')
        both = make_notifier(NotifyConfig(telegram_chat_id='1', webhook_url_env='HOOK', console=True), http)
        assert isinstance(both, FanoutNotifier)


@respx.mock
async def test__telegram_send__429_then_ok__retries_with_retry_after():
    route = respx.post('https://api.telegram.org/bott/sendMessage').mock(
        side_effect=[
            httpx.Response(429, json={'ok': False, 'parameters': {'retry_after': 3}}),
            httpx.Response(200, json={'ok': True}),
        ]
    )
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    async with httpx.AsyncClient() as http:
        await TelegramNotifier(http, 't', '42', sleep=sleep).send('hi')
    assert route.call_count == 2
    assert slept == [3.0]


@respx.mock
async def test__telegram_send__5xx_exhausted__notify_error_after_retries():
    route = respx.post('https://api.telegram.org/bott/sendMessage').mock(
        return_value=httpx.Response(502, text='bad gateway')
    )
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    async with httpx.AsyncClient() as http:
        with pytest.raises(NotifyError, match='502'):
            await TelegramNotifier(http, 't', '42', sleep=sleep).send('hi')
    assert route.call_count == 3
    assert slept == [1.0, 2.0]


@respx.mock
async def test__telegram_send__huge_retry_after__capped():
    respx.post('https://api.telegram.org/bott/sendMessage').mock(
        side_effect=[httpx.Response(429, json={'parameters': {'retry_after': 3600}}), httpx.Response(200)]
    )
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    async with httpx.AsyncClient() as http:
        await TelegramNotifier(http, 't', '42', sleep=sleep).send('hi')
    assert slept == [30.0]


def test__clip__emoji__counted_as_two_utf16_units():
    from autostudio.notify import utf16_len

    text = '😀' * 2500  # 5000 единиц UTF-16
    clipped = clip(text)
    assert utf16_len(clipped) <= 4000
    assert clipped.endswith('…')
    assert utf16_len('a😀') == 3

"""OAuth2 для IMAP: строка XOAUTH2, токены Google и Microsoft 365, разовое согласие Google."""

import datetime as dt
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from autostudio.clock import FrozenClock
from autostudio.errors import ConfigError
from autostudio.inbox import oauth
from autostudio.inbox.errors import MailboxError
from autostudio.inbox.google_oauth import GoogleHttpRequest, GoogleRefreshTokenProvider
from autostudio.inbox.oauth import (
    GOOGLE_SCOPE,
    MICROSOFT_SCOPE,
    MicrosoftClientCredentialsProvider,
    XOAuth2Responder,
    describe_token_error,
    read_client_secrets,
    run_google_consent,
    xoauth2_string,
)
from tests.inbox.helpers import NOW

TENANT = '11111111-2222-3333-4444-555555555555'
MS_URL = f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token'
GOOGLE_URL = 'https://oauth2.googleapis.com/token'
SECRET = 'ms-secret-value-123'
REFRESH = '1//refresh-token-xyz'


def test__xoauth2_string__exact_sasl_format():
    # Формат Google и Microsoft: user=<u>^Aauth=Bearer <token>^A^A (base64 добавляет imaplib).
    assert xoauth2_string('info@acme.example', 'ya29.tok') == 'user=info@acme.example\x01auth=Bearer ya29.tok\x01\x01'


def test__xoauth2_responder__payload_once_then_empty_answer_to_error_continuation():
    responder = XOAuth2Responder('info@acme.example', 'ya29.tok')
    assert responder(b'') == b'user=info@acme.example\x01auth=Bearer ya29.tok\x01\x01'
    # RFC 7628: на продолжение-ошибку клиент отвечает пустой строкой, а не повтором токена.
    assert responder(b'{"status":"400","schemes":"Bearer"}') == b''
    assert responder.error == '{"status":"400","schemes":"Bearer"}'


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('{"status":"400","schemes":"Bearer","scope":"https://mail.google.com/"}', 'status=400, scope=https://mail.google.com/'),
        ('{"status":"401"}', 'status=401'),
        ('not json at all', ''),
        ('[1, 2]', ''),
        ('', ''),
    ],
)
def test__describe_token_error__gmail_json_or_nothing(raw, expected):
    assert describe_token_error(raw) == expected


# --- Microsoft 365 -----------------------------------------------------------------------


def _ms(http: httpx.AsyncClient, clock: FrozenClock | None = None) -> MicrosoftClientCredentialsProvider:
    return MicrosoftClientCredentialsProvider(
        tenant_id=TENANT, client_id='app-id', client_secret=SECRET, http=http, clock=clock or FrozenClock(NOW)
    )


@respx.mock
async def test__microsoft__client_credentials__form_scope_and_token_cached_until_near_expiry():
    route = respx.post(MS_URL).mock(
        return_value=httpx.Response(200, json={'token_type': 'Bearer', 'expires_in': 3599, 'access_token': 'eyJ.ms'})
    )
    clock = FrozenClock(NOW)
    async with httpx.AsyncClient() as http:
        provider = _ms(http, clock)
        assert await provider.access_token() == 'eyJ.ms'
        assert await provider.access_token() == 'eyJ.ms'
        assert route.call_count == 1
        form = parse_qs(route.calls[0].request.content.decode())
        assert form == {
            'client_id': ['app-id'],
            'client_secret': [SECRET],
            'scope': [MICROSOFT_SCOPE],
            'grant_type': ['client_credentials'],
        }
        clock.advance(dt.timedelta(minutes=56))  # меньше 5 минут до конца — берём новый
        await provider.access_token()
        assert route.call_count == 2
        provider.invalidate()
        await provider.access_token()
        assert route.call_count == 3
    assert provider.label == 'Microsoft 365' and 'IMAP.AccessAsApp' in provider.hint


@pytest.mark.parametrize(
    ('code', 'hint'),
    [
        ('AADSTS7000215', 'секрет приложения неверный'),
        ('AADSTS700016', 'нет в этом тенанте'),
        ('AADSTS90002', 'тенант не найден'),
        ('AADSTS50000', ''),
    ],
)
@respx.mock
async def test__microsoft__token_refused__reason_and_hint_without_secret(code, hint):
    body = {
        'error': 'invalid_client',
        'error_description': f'{code}: Invalid client secret provided {SECRET}.\r\nTrace ID: abc\r\nTimestamp: x',
    }
    respx.post(MS_URL).mock(return_value=httpx.Response(401, json=body))
    async with httpx.AsyncClient() as http:
        with pytest.raises(MailboxError) as caught:
            await _ms(http).access_token()
    message = str(caught.value)
    assert code in message and 'HTTP 401' in message and hint in message
    assert SECRET not in message and 'Trace ID' not in message


@respx.mock
async def test__microsoft__non_json_error_and_reply_without_token__clear_errors():
    respx.post(MS_URL).mock(side_effect=[httpx.Response(502, text='<html>bad gateway</html>'), httpx.Response(200, json={})])
    async with httpx.AsyncClient() as http:
        provider = _ms(http)
        with pytest.raises(MailboxError, match='HTTP 502'):
            await provider.access_token()
        with pytest.raises(MailboxError, match='без access_token'):
            await provider.access_token()


@respx.mock
async def test__microsoft__network_error__type_only():
    respx.post(MS_URL).mock(side_effect=httpx.ConnectError('dns failure for login.microsoftonline.com'))
    async with httpx.AsyncClient() as http:
        with pytest.raises(MailboxError, match=r'недоступен: ConnectError$'):
            await _ms(http).access_token()


@pytest.mark.parametrize('tenant', ['', 'a/b', '../x', 'contoso..com', 'x?y=1'])
async def test__microsoft__bad_tenant__config_error_before_any_request(tenant):
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConfigError, match='тенанта'):
            MicrosoftClientCredentialsProvider(tenant_id=tenant, client_id='a', client_secret='b', http=http)


def test__microsoft__tenant_domain_accepted():
    provider = MicrosoftClientCredentialsProvider(
        tenant_id='contoso.onmicrosoft.com', client_id='a', client_secret='b', http=httpx.AsyncClient()
    )
    assert provider.label == 'Microsoft 365'


# --- Google -------------------------------------------------------------------------------


def _google(handler: httpx.MockTransport) -> GoogleRefreshTokenProvider:
    return GoogleRefreshTokenProvider(
        client_id='cid.apps.googleusercontent.com',
        client_secret='google-secret',
        refresh_token=REFRESH,
        http=httpx.Client(transport=handler),
    )


async def test__google__refresh_token__google_auth_posts_grant_and_token_cached():
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={'access_token': 'ya29.fresh', 'expires_in': 3599, 'scope': GOOGLE_SCOPE})

    provider = _google(httpx.MockTransport(handle))
    assert await provider.access_token() == 'ya29.fresh'
    assert await provider.access_token() == 'ya29.fresh'
    assert len(seen) == 1 and str(seen[0].url) == GOOGLE_URL and seen[0].method == 'POST'
    form = parse_qs(seen[0].content.decode())
    assert form['grant_type'] == ['refresh_token'] and form['refresh_token'] == [REFRESH]
    assert form['scope'] == [GOOGLE_SCOPE] and form['client_id'] == ['cid.apps.googleusercontent.com']
    provider.invalidate()
    await provider.access_token()
    assert len(seen) == 2
    assert provider.label == 'Google' and 'https://mail.google.com/' in provider.hint


async def test__google__revoked_refresh_token__error_names_cause_and_fix_without_secrets():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={'error': 'invalid_grant', 'error_description': f'Token has been expired or revoked. {REFRESH}'}
        )

    with pytest.raises(MailboxError) as caught:
        await _google(httpx.MockTransport(handle)).access_token()
    message = str(caught.value)
    assert 'invalid_grant' in message and 'oauth-google' in message
    assert REFRESH not in message and 'google-secret' not in message


async def test__google__network_down__transport_error_type_only():
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout('timed out', request=request)

    with pytest.raises(MailboxError, match='Google OAuth недоступен: ConnectTimeout'):
        await _google(httpx.MockTransport(handle)).access_token()


async def test__google__reply_without_access_token__mailbox_error():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'expires_in': 3599})

    with pytest.raises(MailboxError, match='refresh token не принят'):
        await _google(httpx.MockTransport(handle)).access_token()


async def test__google__refresh_leaves_no_token__mailbox_error(monkeypatch):
    provider = _google(httpx.MockTransport(lambda request: httpx.Response(500)))
    monkeypatch.setattr(provider, '_refresh', lambda: None)
    with pytest.raises(MailboxError, match='не выдал access token'):
        await provider.access_token()


def test__google_http_request__adapts_httpx_response_for_google_auth():
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers['x-test'] == '1' and request.content == b'body'
        return httpx.Response(201, headers={'X-Reply': 'yes'}, content=b'{"ok":1}')

    request = GoogleHttpRequest(httpx.Client(transport=httpx.MockTransport(handle)))
    response = request('https://example.test/token', method='POST', body=b'body', headers={'x-test': '1'})
    assert response.status == 201 and response.data == b'{"ok":1}' and response.headers['x-reply'] == 'yes'


# --- разовое согласие Google ----------------------------------------------------------------


def _secrets(tmp_path: Path, kind: str = 'installed') -> Path:
    path = tmp_path / 'client_secret.json'
    client = {
        'client_id': 'cid.apps.googleusercontent.com',
        'client_secret': 'GOCSPX-desktop',
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': GOOGLE_URL,
        'redirect_uris': ['http://localhost'],
    }
    path.write_text(json.dumps({kind: client}), encoding='utf-8')
    return path


class _Creds:
    def __init__(self, refresh_token: str | None) -> None:
        self.refresh_token = refresh_token


class _Flow:
    def __init__(self, refresh_token: str | None) -> None:
        self.refresh_token = refresh_token
        self.kwargs: dict[str, object] = {}

    def run_local_server(self, **kwargs: object) -> _Creds:
        self.kwargs = kwargs
        return _Creds(self.refresh_token)


def test__run_google_consent__loopback_offline_consent__refresh_token_returned(tmp_path):
    flow = _Flow(REFRESH)
    made: list[tuple[Path, list[str]]] = []

    def factory(path: Path, scopes: list[str]) -> _Flow:
        made.append((path, scopes))
        return flow

    consent = run_google_consent(_secrets(tmp_path), port=8765, open_browser=False, flow_factory=factory)
    assert consent.refresh_token == REFRESH and consent.client_id == 'cid.apps.googleusercontent.com'
    assert consent.client_secret == 'GOCSPX-desktop'
    assert made == [(tmp_path / 'client_secret.json', [GOOGLE_SCOPE])]
    assert flow.kwargs['host'] == 'localhost' and flow.kwargs['port'] == 8765 and flow.kwargs['open_browser'] is False
    assert flow.kwargs['access_type'] == 'offline' and flow.kwargs['prompt'] == 'consent'


def test__run_google_consent__no_refresh_token__config_error(tmp_path):
    with pytest.raises(ConfigError, match='refresh token'):
        run_google_consent(_secrets(tmp_path), flow_factory=lambda path, scopes: _Flow(None))


def test__read_client_secrets__web_client_missing_or_broken_file__config_error(tmp_path):
    with pytest.raises(ConfigError, match='Desktop app'):
        read_client_secrets(_secrets(tmp_path, kind='web'))
    with pytest.raises(ConfigError, match='не читается'):
        read_client_secrets(tmp_path / 'missing.json')
    broken = tmp_path / 'broken.json'
    broken.write_text('{not json', encoding='utf-8')
    with pytest.raises(ConfigError, match='не JSON'):
        read_client_secrets(broken)


def test__installed_app_flow__real_library_parses_desktop_client_without_network(tmp_path):
    flow = oauth._installed_app_flow(_secrets(tmp_path), [GOOGLE_SCOPE])
    assert type(flow).__name__ == 'InstalledAppFlow'


def test__installed_app_flow__library_missing__install_hint(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith('google_auth_oauthlib'):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, '__import__', fake_import)
    with pytest.raises(ConfigError, match='--extra oauth'):
        oauth._installed_app_flow(_secrets(tmp_path), [GOOGLE_SCOPE])

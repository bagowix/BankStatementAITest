import json
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from autostudio.booking.gauth import HttpxAuthTransport, ServiceAccountTokens
from autostudio.booking.gcal import SCOPES, CalendarError

TOKEN_URI = 'https://oauth2.googleapis.com/token'


@pytest.fixture(scope='module')
def private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture
def key_file(tmp_path: Path, private_key: str) -> Path:
    path = tmp_path / 'service-account.json'
    path.write_text(
        json.dumps(
            {
                'type': 'service_account',
                'project_id': 'demo',
                'private_key_id': 'abc',
                'private_key': private_key,
                'client_email': 'booking-bot@demo.iam.gserviceaccount.com',
                'client_id': '1',
                'token_uri': TOKEN_URI,
            }
        )
    )
    return path


@respx.mock
async def test__tokens__jwt_grant_through_httpx__cached_until_stale(key_file):
    route = respx.post(TOKEN_URI).mock(
        return_value=httpx.Response(
            200, json={'access_token': 'ya29.fresh', 'expires_in': 3600, 'token_type': 'Bearer'}
        )
    )
    tokens = ServiceAccountTokens(key_file)
    assert await tokens.token() == 'ya29.fresh'
    assert await tokens.token() == 'ya29.fresh'
    assert route.call_count == 1  # второй раз — тот же токен, без обмена
    form = route.calls[0].request.content.decode()
    assert 'grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer' in form and 'assertion=' in form


@respx.mock
async def test__tokens__google_refuses__calendar_error_without_key_material(key_file, private_key):
    respx.post(TOKEN_URI).mock(return_value=httpx.Response(400, json={'error': 'invalid_grant'}))
    with pytest.raises(CalendarError, match='токен Google не получен') as caught:
        await ServiceAccountTokens(key_file).token()
    assert 'PRIVATE KEY' not in str(caught.value) and str(key_file) in str(caught.value)


@respx.mock
async def test__tokens__network_down__transport_error_mapped(key_file):
    respx.post(TOKEN_URI).mock(side_effect=httpx.ConnectError('down'))
    with pytest.raises(CalendarError, match=r'RefreshError|TransportError'):
        await ServiceAccountTokens(key_file).token()


async def test__tokens__missing_or_broken_key__calendar_error(tmp_path):
    with pytest.raises(CalendarError, match='FileNotFoundError'):
        await ServiceAccountTokens(tmp_path / 'absent.json').token()
    broken = tmp_path / 'broken.json'
    broken.write_text('{"type": "service_account"}')
    with pytest.raises(CalendarError, match='MalformedError'):
        await ServiceAccountTokens(broken).token()


@respx.mock
async def test__transport__injected__used_for_refresh_scopes_in_assertion(key_file):
    route = respx.post(TOKEN_URI).mock(
        return_value=httpx.Response(200, json={'access_token': 'ya29.x', 'expires_in': 60})
    )
    with httpx.Client() as client:
        tokens = ServiceAccountTokens(key_file, transport=HttpxAuthTransport(client))
        assert await tokens.token() == 'ya29.x'
    assert route.called and len(SCOPES) == 2


@respx.mock
def test__transport__response_adapter__status_headers_data():
    respx.get('https://example.test/x').mock(return_value=httpx.Response(201, headers={'X-A': '1'}, content=b'body'))
    with httpx.Client() as client:
        response = HttpxAuthTransport(client)('https://example.test/x')
    assert response.status == 201 and response.headers['x-a'] == '1' and response.data == b'body'

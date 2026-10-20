"""Access token Google для XOAUTH2: refresh token → google-auth (скоуп https://mail.google.com/).

Модуль импортируется только для `auth = "google_oauth"`: google-auth стоит в extras `sheets`
(он есть в образе Docker), а клиентам с паролем приложения он не нужен.
"""

import asyncio
from collections.abc import Mapping
from typing import override

import google.auth.exceptions
import google.auth.transport
import httpx
from google.oauth2.credentials import Credentials

from autostudio.inbox.errors import MailboxError
from autostudio.inbox.oauth import GOOGLE_HINT, GOOGLE_SCOPE, GOOGLE_TOKEN_URI, TIMEOUT, scrub


class _GoogleResponse(google.auth.transport.Response):
    """Ответ httpx в форме, которую ждёт google-auth."""

    def __init__(self, response: httpx.Response) -> None:
        self._status = response.status_code
        self._headers = dict(response.headers)
        self._data = response.content

    @property
    @override
    def status(self) -> int:
        return self._status

    @property
    @override
    def headers(self) -> Mapping[str, str]:
        return self._headers

    @property
    @override
    def data(self) -> bytes:
        return self._data


class GoogleHttpRequest(google.auth.transport.Request):
    """Транспорт google-auth поверх httpx.

    google-auth из коробки ходит через requests, а он у нас только транзитивная зависимость
    gspread. httpx — прямая зависимость проекта, и тесты подменяют его через respx, как
    остальную сеть пакета.

    Args:
        client: Синхронный httpx-клиент (refresh вызывается в потоке).
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    @override
    def __call__(
        self,
        url: str,
        method: str = 'GET',
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: object,
    ) -> _GoogleResponse:
        try:
            response = self._client.request(
                method, url, content=body, headers=dict(headers or {}), timeout=timeout or TIMEOUT
            )
        except httpx.HTTPError as exc:
            # В тексте исключения httpx бывает URL; секретов в нём нет, но пишем только тип.
            raise google.auth.exceptions.TransportError(type(exc).__name__) from None
        return _GoogleResponse(response)


class GoogleRefreshTokenProvider:
    """Access token Google по refresh token (google-auth, скоуп https://mail.google.com/).

    Args:
        client_id: Client ID OAuth-клиента (Desktop app в GCP-проекте клиента).
        client_secret: Его client secret.
        refresh_token: Refresh token из `autostudio inbox oauth-google`.
        http: Синхронный httpx-клиент; по умолчанию создаётся свой.
    """

    def __init__(
        self, *, client_id: str, client_secret: str, refresh_token: str, http: httpx.Client | None = None
    ) -> None:
        self._secrets = (client_secret, refresh_token)
        self._credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=[GOOGLE_SCOPE],
        )
        self._request = GoogleHttpRequest(http or httpx.Client(timeout=TIMEOUT))

    @property
    def label(self) -> str:
        return 'Google'

    @property
    def hint(self) -> str:
        return GOOGLE_HINT

    async def access_token(self) -> str:
        token = self._credentials.token
        if token and self._credentials.valid:
            return token
        await asyncio.to_thread(self._refresh)
        token = self._credentials.token
        if not token:
            raise MailboxError('Google OAuth: сервер не выдал access token')
        return token

    def invalidate(self) -> None:
        self._credentials.token = None

    def _refresh(self) -> None:
        try:
            self._credentials.refresh(self._request)
        except google.auth.exceptions.RefreshError as exc:
            # invalid_grant — refresh token отозван (пользователь, администратор, смена пароля)
            # или выдан другому клиенту: лечится только новым согласием.
            reason = scrub(str(exc.args[0]) if exc.args else type(exc).__name__, self._secrets)
            raise MailboxError(
                f'Google OAuth: refresh token не принят: {reason} — получите новый: '
                '`autostudio inbox oauth-google --client-secrets <файл>`'
            ) from None
        except google.auth.exceptions.TransportError as exc:
            raise MailboxError(f'Google OAuth недоступен: {scrub(str(exc), self._secrets)}') from None

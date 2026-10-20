"""Токен сервисного аккаунта Google для Calendar API (google-auth из extras `sheets`).

Подпись JWT и обмен на access token делает google-auth — криптографию руками не
пишем. Транспорт обмена — свой, поверх синхронного httpx: стандартный
`google.auth.transport.requests` тянет `requests`, которого нет в зависимостях пакета,
а httpx есть и мокается в тестах. Оба дают один и тот же токен — сравнение в
`var/review_booking/compare_libraries.py`.

Модуль импортируется лениво (только при настроенном `[booking.calendar]`): развёртыванию
без Google не нужно ставить google-auth.
"""

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, override

import google.auth.exceptions
import google.auth.transport
import httpx
from google.auth.credentials import TokenState
from google.oauth2.service_account import Credentials

from autostudio.booking.gcal import SCOPES, CalendarError

TOKEN_TIMEOUT_S: Final = 10.0


class _HttpxResponse(google.auth.transport.Response):
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    @override
    def status(self) -> int:
        return self._response.status_code

    @property
    @override
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    @property
    @override
    def data(self) -> bytes:
        return self._response.content


class HttpxAuthTransport(google.auth.transport.Request):
    """Транспорт google-auth поверх синхронного httpx (вызывается из потока).

    Args:
        client: Синхронный httpx-клиент.
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
        **kwargs: str,
    ) -> _HttpxResponse:
        try:
            response = self._client.request(
                method, url, content=body, headers=headers, timeout=timeout or TOKEN_TIMEOUT_S
            )
        except httpx.HTTPError as exc:
            # google-auth ждёт TransportError; в тексте — только тип.
            raise google.auth.exceptions.TransportError(type(exc).__name__) from None
        return _HttpxResponse(response)


class ServiceAccountTokens:
    """`TokenSource`: токен сервисного аккаунта, обновляемый заранее.

    Ключ читается при первом обращении; токен живёт час, а google-auth помечает его
    «устаревающим» (`TokenState.STALE`) за несколько минут до конца — тогда он обновляется.

    Args:
        path: JSON-ключ сервисного аккаунта.
        transport: Транспорт google-auth; по умолчанию — `HttpxAuthTransport` на время обмена.
        scopes: Права токена.
    """

    def __init__(
        self,
        path: Path,
        *,
        transport: google.auth.transport.Request | None = None,
        scopes: Sequence[str] = SCOPES,
    ) -> None:
        self._path = path
        self._transport = transport
        self._scopes = list(scopes)
        self._credentials: Credentials | None = None
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        # Замок: десяток одновременных сообщений не должен запустить десяток обменов JWT.
        async with self._lock:
            return await asyncio.to_thread(self._token)

    def _token(self) -> str:
        try:
            if self._credentials is None:
                self._credentials = Credentials.from_service_account_file(str(self._path), scopes=self._scopes)
            credentials = self._credentials
            if credentials.token_state != TokenState.FRESH:
                if self._transport is None:
                    with httpx.Client() as client:
                        credentials.refresh(HttpxAuthTransport(client))
                else:
                    credentials.refresh(self._transport)
        except (OSError, ValueError, google.auth.exceptions.GoogleAuthError) as exc:
            # Текст ошибок google-auth может цитировать ответ сервера; показываем тип и путь к ключу.
            raise CalendarError(f'токен Google не получен ({type(exc).__name__}), ключ {self._path}') from None
        token = credentials.token
        if not isinstance(token, str) or not token:
            raise CalendarError('Google не выдал токен сервисного аккаунта')
        return token

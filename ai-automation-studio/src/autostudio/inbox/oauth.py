"""OAuth2 для IMAP (SASL XOAUTH2): Google Workspace и Microsoft 365.

Почему OAuth, а не пароль: в Google Workspace вход в IMAP по логину и паролю отключён с
01.05.2025, в Exchange Online базовая аутентификация выключена совсем. Пароль приложения
остаётся для личного Gmail, Яндекса, Zoho и прочих ящиков.

Как устроено:
- Google: refresh token, выданный один раз командой `autostudio inbox oauth-google` на
  OAuth-клиенте в GCP-проекте САМОГО клиента (Internal-приложение его организации), меняется
  на access token через google-auth. Скоуп `https://mail.google.com/` — restricted: приложение
  оператора, общее для многих клиентов, потребовало бы проверки Google и ежегодной оценки
  CASA, поэтому такое приложение мы не делаем никогда.
- Microsoft 365: приложение в тенанте клиента с application-разрешением IMAP.AccessAsApp
  получает токен по client credentials (без пользователя); ящик открыт приложению командой
  администратора `Add-MailboxPermission`. Пользователь IMAP — адрес ящика.

Токены живут только в памяти процесса: ни в лог, ни в текст ошибки, ни в SQLite.

Провайдер Google — в `google_oauth.py`: google-auth стоит в extras `sheets`, и его импорт
нужен только клиентам с `auth = "google_oauth"`.
"""

import datetime as dt
import errno
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Final, Protocol

import httpx
import msgspec

from autostudio.clock import Clock, system_now
from autostudio.errors import ConfigError
from autostudio.inbox.errors import MailboxError

logger = logging.getLogger(__name__)

GOOGLE_SCOPE: Final = 'https://mail.google.com/'
GOOGLE_TOKEN_URI: Final = 'https://oauth2.googleapis.com/token'  # noqa: S105 - адрес, а не секрет
MICROSOFT_SCOPE: Final = 'https://outlook.office365.com/.default'
MICROSOFT_TOKEN_URL: Final = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'  # noqa: S105
# ID тенанта уходит в путь URL: только GUID или домен, без «/», «?» и «..». Шаблоны без
# вложенных квантификаторов: прежний `(?:[…]{0,62}\.?)*` на GUID с хвостовым пробелом или
# «# комментарием» из .env перебирал варианты экспоненциально — час CPU до ConfigError.
_GUID: Final = re.compile(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}')
_DOMAIN_LABEL: Final = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?')
_DOMAIN_LIMIT: Final = 253
# Домен — это хотя бы «имя.зона»: одиночное «contoso» или «common» для client credentials не годится.
_DOMAIN_LABELS_MIN: Final = 2
_SHOWN_LIMIT: Final = 80
# Токен берётся заново чуть раньше срока: вход в ящик и первые команды не должны попасть на границу.
_EARLY: Final = dt.timedelta(minutes=5)
TIMEOUT: Final = 30.0
_DETAIL_LIMIT: Final = 300
_AADSTS: Final = re.compile(r'AADSTS\d+')
_CONSENT_TIMEOUT: Final = 300

GOOGLE_HINT: Final = (
    'проверьте, что IMAP включён администратором Workspace, refresh token выдан этому же ящику '
    '(`autostudio inbox oauth-google`) со скоупом https://mail.google.com/, а OAuth-клиент не удалён и не '
    'заблокирован в Admin console → Security → API controls'
)
MICROSOFT_HINT: Final = (
    'проверьте в тенанте клиента: разрешение IMAP.AccessAsApp (Office 365 Exchange Online) с согласием '
    'администратора, New-ServicePrincipal и Add-MailboxPermission на этот ящик, IMAP включён для ящика '
    '(Set-CASMailbox -ImapEnabled $true); после выдачи прав подождите до 30 минут'
)


class TokenProvider(Protocol):
    """Источник access token для XOAUTH2."""

    @property
    def label(self) -> str:
        """Название провайдера для сообщений оператору (`Google`, `Microsoft 365`)."""
        ...

    @property
    def hint(self) -> str:
        """Что проверить, если сервер IMAP отверг токен."""
        ...

    async def access_token(self) -> str:
        """Действующий access token (из памяти или свежий).

        Raises:
            MailboxError: Токен не выдан: отозван, неверный секрет, сеть.
        """
        ...

    def invalidate(self) -> None:
        """Забыть токен: сервер его не принял, следующий вход возьмёт новый."""
        ...


def xoauth2_string(user: str, token: str) -> str:
    """Строка SASL XOAUTH2 до base64: `user=<u>^Aauth=Bearer <token>^A^A`.

    Формат Google (developers.google.com/workspace/gmail/imap/xoauth2-protocol) и Microsoft
    (Exchange Online, «Authenticate an IMAP connection using OAuth») один и тот же; base64
    добавляет сам `imaplib.IMAP4.authenticate`.

    Args:
        user: Адрес ящика.
        token: Access token.

    Returns:
        Строка для механизма XOAUTH2.
    """
    return f'user={user}\x01auth=Bearer {token}\x01\x01'


class XOAuth2Responder:
    """Ответ на продолжения сервера в `imaplib.IMAP4.authenticate('XOAUTH2', …)`.

    Первый вызов отдаёт строку XOAUTH2. Если сервер отверг токен, он присылает ещё одно
    продолжение — JSON с причиной (Gmail: `{"status":"400","schemes":"Bearer",…}`); по
    RFC 7628 клиент отвечает на него пустой строкой, и сервер завершает обмен ответом NO с
    понятной причиной. Повторная отправка той же строки (так делает imap-tools) — нарушение
    протокола: сервер отвечает BAD, и настоящая причина теряется.

    Args:
        user: Адрес ящика.
        token: Access token.
    """

    def __init__(self, user: str, token: str) -> None:
        self._payload = xoauth2_string(user, token).encode()
        self._sent = False
        self.error = ''

    def __call__(self, challenge: bytes) -> bytes:
        if self._sent:
            self.error = challenge.decode('utf-8', 'replace')[:_DETAIL_LIMIT]
            return b''
        self._sent = True
        return self._payload


# --- Microsoft 365 --------------------------------------------------------------------


class _TokenReply(msgspec.Struct, frozen=True):
    access_token: str
    expires_in: int = 3600


class _TokenFailure(msgspec.Struct, frozen=True):
    error: str = ''
    error_description: str = ''


class MicrosoftClientCredentialsProvider:
    """Access token Exchange Online по client credentials (приложение в тенанте клиента).

    Запрос — `POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token` со
    скоупом `https://outlook.office365.com/.default`: так Entra выдаёт токен со всеми
    application-разрешениями приложения на Exchange Online, в том числе IMAP.AccessAsApp.

    Args:
        tenant_id: ID тенанта клиента (GUID) или его домен.
        client_id: Application (client) ID приложения.
        client_secret: Секрет приложения.
        http: Общий асинхронный httpx-клиент.
        clock: Часы (в тестах — замороженные).
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient,
        clock: Clock = system_now,
    ) -> None:
        tenant = tenant_id.strip()
        if not valid_tenant(tenant):
            raise ConfigError(
                f'ID тенанта Microsoft {tenant[:_SHOWN_LIMIT]!r}: нужен GUID или домен (contoso.onmicrosoft.com); '
                'в .env комментарий «#» в строке значения становится частью значения'
            )
        self._url = MICROSOFT_TOKEN_URL.format(tenant=tenant)
        self._client_id = client_id
        self._secret = client_secret
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._expires_at: dt.datetime | None = None

    @property
    def label(self) -> str:
        return 'Microsoft 365'

    @property
    def hint(self) -> str:
        return MICROSOFT_HINT

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = None

    async def access_token(self) -> str:
        now = self._clock()
        if self._token and self._expires_at is not None and now < self._expires_at - _EARLY:
            return self._token
        form = {
            'client_id': self._client_id,
            'client_secret': self._secret,
            'scope': MICROSOFT_SCOPE,
            'grant_type': 'client_credentials',
        }
        try:
            response = await self._http.post(self._url, data=form, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise MailboxError(f'Microsoft 365 OAuth недоступен: {type(exc).__name__}') from None
        if response.status_code != httpx.codes.OK:
            raise MailboxError(self._failure(response))
        try:
            reply = msgspec.json.decode(response.content, type=_TokenReply)
        except (msgspec.ValidationError, msgspec.DecodeError):
            raise MailboxError('Microsoft 365 OAuth: ответ без access_token') from None
        self._token = reply.access_token
        self._expires_at = now + dt.timedelta(seconds=max(reply.expires_in, 0))
        return reply.access_token

    def _failure(self, response: httpx.Response) -> str:
        try:
            failure = msgspec.json.decode(response.content, type=_TokenFailure)
        except (msgspec.ValidationError, msgspec.DecodeError):
            failure = _TokenFailure()
        # Первая строка описания — код AADSTS и суть; дальше Trace ID и время, оператору не нужны.
        description = failure.error_description.splitlines()[0] if failure.error_description else ''
        codes = set(_AADSTS.findall(description))
        hint = ''
        if 'AADSTS7000215' in codes or 'AADSTS7000222' in codes:
            hint = ' — секрет приложения неверный или истёк: выпустите новый в Certificates & secrets'
        elif 'AADSTS700016' in codes:
            hint = ' — приложения с таким client ID нет в этом тенанте'
        elif 'AADSTS90002' in codes:
            hint = ' — тенант не найден: проверьте ID тенанта'
        detail = scrub(f'{failure.error}: {description}'.strip(': '), (self._secret,))[:_DETAIL_LIMIT]
        return f'Microsoft 365 OAuth: токен не выдан (HTTP {response.status_code}) {detail}{hint}'


def valid_tenant(value: str) -> bool:
    """ID тенанта Entra: GUID (Directory ID) или домен из двух и более меток.

    Проверка линейная по длине строки: метки домена сверяются по одной, без шаблона с
    вложенными повторами.

    Args:
        value: ID тенанта (уже без пробелов по краям).

    Returns:
        True, если значение можно подставить в путь URL токена.
    """
    if _GUID.fullmatch(value):
        return True
    labels = value.split('.')
    return (
        len(value) <= _DOMAIN_LIMIT
        and len(labels) >= _DOMAIN_LABELS_MIN
        and all(_DOMAIN_LABEL.fullmatch(label) for label in labels)
    )


def scrub(text: str, secrets: tuple[str, ...]) -> str:
    """Вычищает секреты из текста ошибки."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, '***')
    return text


# --- согласие Google (разовое, на компьютере оператора или клиента) ---------------------------


class GoogleConsent(msgspec.Struct, frozen=True):
    """Что дала разовая авторизация: всё это кладётся в `.env` клиента."""

    client_id: str
    client_secret: str
    refresh_token: str


class _InstalledClient(msgspec.Struct, frozen=True):
    client_id: str
    client_secret: str


class _ClientSecrets(msgspec.Struct, frozen=True):
    installed: _InstalledClient | None = None


class ConsentCredentials(Protocol):
    """Часть `google.oauth2.credentials.Credentials`, которая нужна после согласия."""

    @property
    def refresh_token(self) -> str | None: ...


class ConsentFlow(Protocol):
    """Часть `google_auth_oauthlib.flow.InstalledAppFlow`, которой пользуется команда."""

    def run_local_server(
        self,
        *,
        host: str,
        port: int,
        open_browser: bool,
        timeout_seconds: int,
        authorization_prompt_message: str,
        access_type: str,
        prompt: str,
    ) -> ConsentCredentials: ...


type FlowFactory = Callable[[Path, list[str]], ConsentFlow]


def _installed_app_flow(client_secrets: Path, scopes: list[str]) -> ConsentFlow:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise ConfigError('нужен google-auth-oauthlib: uv sync --extra oauth') from None
    # Библиотека без аннотаций типов: её объект описан протоколом `ConsentFlow` выше.
    flow: ConsentFlow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=scopes)
    return flow


def read_client_secrets(path: Path) -> _InstalledClient:
    """Читает JSON OAuth-клиента Google и проверяет, что это Desktop app.

    Args:
        path: Файл, скачанный из Google Cloud Console (Credentials → OAuth client ID).

    Returns:
        Client ID и secret.

    Raises:
        ConfigError: Файла нет, он битый или это клиент не того типа.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f'{path}: не читается ({type(exc).__name__})') from None
    try:
        secrets = msgspec.json.decode(raw, type=_ClientSecrets)
    except (msgspec.ValidationError, msgspec.DecodeError):
        raise ConfigError(f'{path}: это не JSON OAuth-клиента Google') from None
    if secrets.installed is None:
        # У клиента «Web application» redirect URI нужно регистрировать заранее; петлевой
        # адрес со случайным портом Google разрешает только клиентам «Desktop app».
        raise ConfigError(f'{path}: нужен OAuth-клиент типа «Desktop app» (в JSON нет секции "installed")')
    return secrets.installed


def consent_failure(exc: Exception, port: int) -> ConfigError | None:
    """Понятная оператору причина, по которой согласие Google не получено, или None.

    `run_local_server` бросает разнородные исключения: `WSGITimeoutError` (наследник
    AttributeError) по тайм-ауту, ошибки oauthlib (`access_denied` — нажата «Отмена»),
    `Warning` oauthlib, если выдан не тот скоуп, и `OSError`, если порт занят или нет сети.
    Текст исключения в причину не попадает: в нём бывает адрес возврата с кодом авторизации.

    Args:
        exc: Исключение из потока согласия.
        port: Порт петлевого сервера (для подсказки).

    Returns:
        ConfigError с подсказкой или None, если исключение незнакомое (это баг, пусть падает).
    """
    try:
        from google_auth_oauthlib.flow import WSGITimeoutError

        # oauthlib без аннотаций и без stubs (types-oauthlib не в зависимостях); нужен только класс для isinstance.
        from oauthlib.oauth2.rfc6749.errors import OAuth2Error  # pyrefly: ignore[untyped-import]
    except ImportError:  # pragma: no cover - без extra `oauth` поток согласия не создаётся вовсе
        return None
    if isinstance(exc, WSGITimeoutError):
        return ConfigError(
            f'Google: вход не завершён за {_CONSENT_TIMEOUT // 60} минут — запустите команду снова и '
            'разрешите доступ в открывшемся окне'
        )
    if isinstance(exc, OAuth2Error):
        code = exc.error or type(exc).__name__
        if code == 'access_denied':
            return ConfigError(
                'Google: доступ не разрешён (нажата «Отмена» или вход не в ящик клиента) — повторите и '
                'разрешите доступ к почте'
            )
        return ConfigError(f'Google: согласие не завершено ({code}) — проверьте OAuth-клиент «Desktop app» и повторите')
    if isinstance(exc, Warning):
        # oauthlib сообщает так о сменившемся скоупе: на экране согласия сняли галочку доступа к Gmail.
        return ConfigError(
            f'Google выдал доступ без почты: на экране согласия оставьте отмеченным доступ к Gmail ({GOOGLE_SCOPE})'
        )
    if isinstance(exc, OSError):
        if exc.errno == errno.EADDRINUSE:
            return ConfigError(f'порт {port} занят — укажите другой --port или 0 (любой свободный)')
        return ConfigError(f'Google: нет связи ({type(exc).__name__}) — проверьте сеть и повторите')
    return None


def run_google_consent(
    client_secrets: Path, *, port: int = 0, open_browser: bool = True, flow_factory: FlowFactory | None = None
) -> GoogleConsent:
    """Разовое согласие владельца ящика: браузер → Google → refresh token.

    Запускает `InstalledAppFlow.run_local_server` (петлевой адрес localhost) со скоупом
    https://mail.google.com/. `prompt=consent` и `access_type=offline` нужны, чтобы Google
    выдал refresh token даже при повторном согласии.

    Args:
        client_secrets: JSON OAuth-клиента «Desktop app» из GCP-проекта клиента.
        port: Порт петлевого сервера; 0 — любой свободный.
        open_browser: Открыть браузер самому (иначе ссылка печатается).
        flow_factory: Фабрика потока (в тестах — заглушка).

    Returns:
        Client ID, secret и refresh token.

    Raises:
        ConfigError: Файл клиента не подходит, согласие не получено (тайм-аут, отказ, не тот
            скоуп, порт занят, нет сети) или Google не выдал refresh token.
    """
    client = read_client_secrets(client_secrets)
    flow = (flow_factory or _installed_app_flow)(client_secrets, [GOOGLE_SCOPE])
    try:
        credentials = flow.run_local_server(
            host='localhost',
            port=port,
            open_browser=open_browser,
            timeout_seconds=_CONSENT_TIMEOUT,
            authorization_prompt_message='Откройте ссылку и войдите в ящик клиента: {url}',
            access_type='offline',
            prompt='consent',
        )
    except Exception as exc:
        failure = consent_failure(exc, port)
        if failure is None:
            raise
        raise failure from None
    if not credentials.refresh_token:
        raise ConfigError('Google не выдал refresh token — отзовите доступ приложения в аккаунте и повторите')
    return GoogleConsent(
        client_id=client.client_id, client_secret=client.client_secret, refresh_token=credentials.refresh_token
    )


class _XOAuthError(msgspec.Struct, frozen=True):
    status: str = ''
    scope: str = ''


def describe_token_error(raw: str) -> str:
    """Причина отказа XOAUTH2 из JSON-продолжения сервера (Gmail) — без токена.

    Args:
        raw: Раскодированное продолжение сервера (`{"status":"400","scope":"…"}`).

    Returns:
        Короткая причина или пустая строка, если это не JSON Gmail.
    """
    try:
        error = msgspec.json.decode(raw, type=_XOAuthError)
    except (msgspec.ValidationError, msgspec.DecodeError):
        return ''
    parts = [f'{name}={value}' for name, value in (('status', error.status), ('scope', error.scope)) if value]
    return ', '.join(parts)[:_DETAIL_LIMIT]

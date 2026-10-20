"""Почтовый ящик по IMAP: тонкий адаптер вокруг stdlib imaplib плюс заглушка для демо.

Правила работы с чужим ящиком:
- папка открывается только на чтение (EXAMINE), письма читаются через BODY.PEEK —
  разбор не помечает письма прочитанными;
- черновик кладётся APPEND-ом в папку черновиков с флагом \\Draft — он никуда не уходит;
- «переложить в папку» — это COPY: из исходной папки ничего не удаляется (v1 не
  делает ни STORE \\Deleted, ни EXPUNGE);
- логин, пароль и OAuth-токен не попадают ни в текст ошибки, ни в журнал.

Вход — паролем приложения (LOGIN) или OAuth2 (AUTHENTICATE XOAUTH2, Google Workspace и
Microsoft 365); токен даёт `oauth.TokenProvider`.

imaplib синхронный, поэтому каждый вызов уходит в поток (`asyncio.to_thread`).

Почему stdlib imaplib, а не imap-tools (проверено на одном IMAP-сервере, сравнение в
var/review_inbox/compare_imap.py): по проводу обе дают одно и то же для PEEK, APPEND с
\\Draft, COPY и имён папок в modified UTF-7, но imap-tools не умеет частичную выборку
`BODY.PEEK[]<0.N>` (письмо на 25 МБ качается целиком), отдаёт письмо пересобранным, а не
байтами сервера (ломается хеш письма без Message-ID), берёт UIDVALIDITY отдельной командой
STATUS вместо ответа EXAMINE, при отказе XOAUTH2 повторяет строку с токеном вместо пустого
ответа по RFC 7628 (сервер отвечает BAD, причина теряется), а при импорте меняет глобальный
`imaplib._MAXLINE` для всего процесса. Проще наш адаптер от неё не станет: всё равно
понадобились бы `client.response()` и обход ошибок на уровне imaplib.
"""

import asyncio
import base64
import datetime as dt
import imaplib
import logging
import re
import ssl
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Final, Protocol, Self

import msgspec

from autostudio.inbox.errors import MailboxError
from autostudio.inbox.oauth import TokenProvider, XOAuth2Responder, describe_token_error

logger = logging.getLogger(__name__)

# Письмо читается не целиком, а первыми байтами: текст идёт в начале MIME, а вложение на
# 20 МБ разбору не нужно. Граница вмещает PDF-счёт до `max_pdf_kb` (3,5 МБ, в base64 ≈ 4,7 МБ);
# письмо длиннее обрезается, и PDF в нём честно помечается «не читается». Частичный FETCH —
# тоже PEEK, флаги не меняются.
MAX_FETCH_BYTES: Final = 5_000_000
_MONTHS: Final = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
_UID: Final = re.compile(rb'UID (\d+)')
_LIST_LINE: Final = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')
_MUTF7_RUN: Final = re.compile(r'&([A-Za-z0-9+,]*)-')
_AUTH_FAILED: Final = re.compile(
    r'AUTHENTICATIONFAILED|AUTHORIZATIONFAILED|invalid credentials|authentication failed|login failed|'
    r'incorrect (?:username|password)|username and password not accepted|application-specific password',
    re.I,
)

type ImapData = Sequence[bytes | tuple[bytes, bytes] | None]
type ImapReply = tuple[str, ImapData]


class ImapConnection(Protocol):
    """Часть `imaplib.IMAP4`, которой пользуется адаптер (в тестах — поддельный сервер)."""

    def login(self, user: str, password: str) -> ImapReply: ...
    # typeshed объявляет ответ AUTHENTICATE как (str, str), на деле это те же строки байтов.
    def authenticate(
        self, mechanism: str, authobject: Callable[[bytes], bytes | None]
    ) -> tuple[str, str | ImapData]: ...
    def select(self, mailbox: str = ..., readonly: bool = ...) -> ImapReply: ...
    def response(self, code: str) -> ImapReply: ...
    def uid(self, command: str, *args: str) -> ImapReply: ...
    def append(self, mailbox: str, flags: str, date_time: str, message: bytes) -> ImapReply: ...
    def list(self, directory: str = ..., pattern: str = ...) -> ImapReply: ...
    def logout(self) -> ImapReply: ...


type Connector = Callable[[str, int, float], ImapConnection]


class FolderState(msgspec.Struct, frozen=True):
    """Состояние открытой папки.

    Attributes:
        uidvalidity: Эпоха нумерации UID: если она сменилась, старые UID ничего не значат.
        uidnext: Следующий UID или None, если сервер его не сообщил.
        exists: Сколько писем в папке.
    """

    uidvalidity: int
    uidnext: int | None
    exists: int


class Folder(msgspec.Struct, frozen=True):
    """Папка ящика.

    Attributes:
        name: Имя для людей и для конфига (раскодированное из modified UTF-7).
        flags: Флаги LIST, в том числе special-use (`\\Drafts`, `\\Sent`).
    """

    name: str
    flags: frozenset[str]


class MailBox(Protocol):
    """Ящик, с которым работает разбор: открыть, найти новые, прочитать, положить черновик."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...

    async def folders(self) -> list[Folder]:
        """Папки ящика."""
        ...

    async def select(self, folder: str) -> FolderState:
        """Открывает папку только на чтение."""
        ...

    async def search(self, *, after_uid: int | None = None, since: dt.date | None = None) -> list[int]:
        """UID писем после `after_uid` или с даты `since` (по возрастанию)."""
        ...

    async def fetch(self, uid: int) -> bytes | None:
        """Байты письма без пометки «прочитано»; None, если письмо уже удалено."""
        ...

    async def append(self, folder: str, message: bytes, when: dt.datetime) -> None:
        """Кладёт черновик в папку с флагом \\Draft."""
        ...

    async def copy(self, uid: int, folder: str) -> None:
        """Копирует письмо в папку (ярлык Gmail), не удаляя из исходной."""
        ...


# --- имена папок: modified UTF-7 (RFC 3501 §5.1.3) ----------------------------------------


def encode_folder(name: str) -> str:
    """Имя папки в modified UTF-7: «Черновики» → `&BCcENQRABD0EPgQyBDgEOgQ4-`."""
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            packed = base64.b64encode(''.join(pending).encode('utf-16-be')).decode().rstrip('=')
            out.append('&' + packed.replace('/', ',') + '-')
            pending.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append('&-' if ch == '&' else ch)
        else:
            pending.append(ch)
    flush()
    return ''.join(out)


def decode_folder(name: str) -> str:
    """Обратное к `encode_folder`; битые последовательности остаются как есть."""

    def repl(match: re.Match[str]) -> str:
        packed = match.group(1)
        if not packed:
            return '&'
        try:
            raw = base64.b64decode(packed.replace(',', '/') + '=' * (-len(packed) % 4))
            return raw.decode('utf-16-be')
        except ValueError:  # UnicodeDecodeError — тоже ValueError
            return match.group(0)

    return _MUTF7_RUN.sub(repl, name)


def quote_folder(name: str) -> str:
    """Имя папки для команды IMAP: кодировка, кавычки и экранирование."""
    encoded = encode_folder(name)
    return '"' + encoded.replace('\\', '\\\\').replace('"', '\\"') + '"'


def imap_date(day: dt.date) -> str:
    """Дата для SEARCH SINCE (`17-Oct-2026`) — месяц по-английски независимо от локали."""
    return f'{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}'


def parse_list(data: ImapData) -> list[Folder]:
    """Разбирает ответ LIST, включая имена-литералы и экранированные кавычки."""
    folders: list[Folder] = []
    for item in data:
        if item is None:
            continue
        if isinstance(item, tuple):
            line, literal = item[0], item[1]
            match = _LIST_LINE.match(line.strip())
            raw_name = literal.decode('utf-8', 'replace') if match else ''
        else:
            match = _LIST_LINE.match(item.strip())
            raw_name = match.group('name').decode('utf-8', 'replace').strip() if match else ''
        if match is None or not raw_name:
            continue
        if raw_name.startswith('"') and raw_name.endswith('"') and len(raw_name) >= 2:
            raw_name = re.sub(r'\\(.)', r'\1', raw_name[1:-1])
        flags = frozenset(flag.decode().lower() for flag in match.group('flags').split())
        folders.append(Folder(name=decode_folder(raw_name), flags=flags))
    return folders


def _first_int(data: ImapData) -> int | None:
    for item in data:
        if isinstance(item, bytes) and item.strip().isdigit():
            return int(item.strip())
    return None


def _parse_fetch(data: ImapData, uid: int) -> bytes | None:
    # Ответ FETCH: [(b'1 (UID 5 BODY[]<0> {123}', b'...'), b')']. UID может прийти
    # и после литерала, в хвостовом b' UID 5)' — проверяем обе позиции.
    items = list(data)
    for index, item in enumerate(items):
        if not isinstance(item, tuple):
            continue
        header, literal = item
        found = _UID.search(header)
        if found is None and index + 1 < len(items):
            tail = items[index + 1]
            found = _UID.search(tail) if isinstance(tail, bytes) else None
        if found is None or int(found.group(1)) == uid:
            return literal
    return None


def _default_connector(host: str, port: int, timeout: float) -> ImapConnection:
    return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=timeout)


class ImapMailBox:
    """Реализация `MailBox` поверх imaplib (SSL, таймауты, только чтение).

    Args:
        host: IMAP-сервер.
        port: Порт SSL.
        user: Логин ящика (при OAuth — адрес ящика).
        password: Пароль приложения или None при OAuth.
        tokens: Источник OAuth-токена (XOAUTH2) или None при входе паролем.
        timeout: Таймаут сокета на каждую операцию, секунды.
        connector: Фабрика соединения (в тестах — поддельный сервер).

    Raises:
        ValueError: Не задан способ входа или заданы оба.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str | None = None,
        tokens: TokenProvider | None = None,
        timeout: float = 30.0,
        connector: Connector | None = None,
    ) -> None:
        if (password is None) == (tokens is None):
            raise ValueError('ImapMailBox: нужен ровно один способ входа — пароль или OAuth')
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._tokens = tokens
        self._secrets: list[str] = [password] if password else []
        self._timeout = timeout
        self._connector = connector or _default_connector
        self._conn: ImapConnection | None = None

    @property
    def user(self) -> str:
        return self._user

    def _scrub(self, text: str) -> str:
        # Сервер иногда повторяет логин в тексте отказа; пароль и токен — тем более не должны утечь.
        for secret in (*self._secrets, self._user):
            if secret:
                text = text.replace(secret, '***')
        # imaplib кладёт ответ сервера в исключение как repr байтов: b'[ALERT] …'.
        if len(text) >= 3 and text[0] == 'b' and text[1] in '\'"' and text[-1] == text[1]:
            text = text[2:-1]
        return text[:300]

    def _error(self, action: str, exc: BaseException) -> MailboxError:
        detail = self._scrub(str(exc)) if isinstance(exc, imaplib.IMAP4.error) else ''
        suffix = f': {detail}' if detail else ''
        return MailboxError(f'IMAP {self._host}: {action} — {type(exc).__name__}{suffix}')

    async def _call[T](self, action: str, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise self._error(action, exc) from None

    def _connection(self) -> ImapConnection:
        if self._conn is None:
            raise MailboxError('IMAP: ящик не открыт (используйте `async with`)')
        return self._conn

    @staticmethod
    def _ok(reply: ImapReply, action: str) -> ImapData:
        status, data = reply
        if status != 'OK':
            detail = b' '.join(item for item in data if isinstance(item, bytes)).decode('utf-8', 'replace')
            raise MailboxError(f'IMAP: {action} — сервер ответил {status}: {detail[:200]}')
        return data

    async def __aenter__(self) -> Self:
        token = await self._access_token()
        responder = XOAuth2Responder(self._user, token) if token is not None else None

        def connect() -> ImapConnection:
            conn = self._connector(self._host, self._port, self._timeout)
            try:
                if responder is not None:
                    conn.authenticate('XOAUTH2', responder)
                else:
                    conn.login(self._user, self._password or '')
            except BaseException:
                _quiet_logout(conn)
                raise
            return conn

        try:
            self._conn = await asyncio.to_thread(connect)
        except imaplib.IMAP4.error as exc:
            # Причина сервера нужна оператору: «слишком много подключений» или «IMAP выключен»
            # лечатся не сменой пароля. Логин, пароль и токен из текста вычищаются.
            reason = self._scrub(str(exc))
            raise MailboxError(
                f'IMAP {self._host}: вход не выполнен: {reason}{self._login_hint(reason, responder)}'
            ) from None
        except OSError as exc:
            raise self._error('подключение', exc) from None
        return self

    async def _access_token(self) -> str | None:
        if self._tokens is None:
            return None
        token = await self._tokens.access_token()
        self._secrets.append(token)
        return token

    def _login_hint(self, reason: str, responder: XOAuth2Responder | None) -> str:
        if responder is None or self._tokens is None:
            return ' — проверьте логин и пароль приложения (не основной пароль)' if _AUTH_FAILED.search(reason) else ''
        # Токен не принят: следующий вход возьмёт свежий, а не тот же из памяти.
        self._tokens.invalidate()
        detail = describe_token_error(responder.error)
        server = f' ({detail})' if detail else ''
        return f'{server} — {self._tokens.label} OAuth: {self._tokens.hint}'

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await asyncio.to_thread(_quiet_logout, conn)

    async def folders(self) -> list[Folder]:
        conn = self._connection()
        data = self._ok(await self._call('LIST', lambda: conn.list('""', '*')), 'LIST')
        return parse_list(data)

    async def select(self, folder: str) -> FolderState:
        conn = self._connection()

        def run() -> FolderState:
            data = self._ok(conn.select(quote_folder(folder), readonly=True), f'EXAMINE {folder}')
            uidvalidity = _first_int(conn.response('UIDVALIDITY')[1])
            if uidvalidity is None:
                raise MailboxError(f'IMAP: сервер не сообщил UIDVALIDITY папки {folder}')
            return FolderState(
                uidvalidity=uidvalidity,
                uidnext=_first_int(conn.response('UIDNEXT')[1]),
                exists=_first_int(data) or 0,
            )

        return await self._call(f'EXAMINE {folder}', run)

    async def search(self, *, after_uid: int | None = None, since: dt.date | None = None) -> list[int]:
        conn = self._connection()
        criteria: tuple[str, ...]
        if after_uid is not None:
            criteria = ('UID', f'{after_uid + 1}:*')
        elif since is not None:
            criteria = ('SINCE', imap_date(since))
        else:
            criteria = ('ALL',)
        data = self._ok(await self._call('SEARCH', lambda: conn.uid('SEARCH', *criteria)), 'SEARCH')
        uids = {int(token) for item in data if isinstance(item, bytes) for token in item.split() if token.isdigit()}
        # `UID n:*` всегда возвращает последнее письмо, даже если его UID меньше n.
        return sorted(uid for uid in uids if after_uid is None or uid > after_uid)

    async def fetch(self, uid: int) -> bytes | None:
        conn = self._connection()
        query = f'(UID BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)'
        data = self._ok(await self._call('FETCH', lambda: conn.uid('FETCH', str(uid), query)), 'FETCH')
        return _parse_fetch(data, uid)

    async def append(self, folder: str, message: bytes, when: dt.datetime) -> None:
        conn = self._connection()
        stamp = imaplib.Time2Internaldate(when)
        reply = await self._call(
            'APPEND', lambda: conn.append(quote_folder(folder), '(\\Draft \\Seen)', stamp, message)
        )
        self._ok(reply, f'APPEND в {folder}')

    async def copy(self, uid: int, folder: str) -> None:
        conn = self._connection()
        reply = await self._call('COPY', lambda: conn.uid('COPY', str(uid), quote_folder(folder)))
        self._ok(reply, f'COPY в {folder}')


def _quiet_logout(conn: ImapConnection) -> None:
    # Выход «всегда»: даже после ошибки соединение закрывается, а сбой выхода не
    # заслоняет настоящую ошибку.
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError) as exc:
        logger.debug('IMAP logout не удался: %s', type(exc).__name__)


# --- заглушка для демо и тестов ---------------------------------------------------------


class FakeFolder:
    """Папка заглушки: письма по UID и флаги."""

    def __init__(self, uidvalidity: int = 1) -> None:
        self.uidvalidity = uidvalidity
        self.messages: dict[int, bytes] = {}
        self.flags: dict[int, set[str]] = {}
        self.next_uid = 1

    def add(self, raw: bytes, flags: set[str] | None = None) -> int:
        uid = self.next_uid
        self.next_uid += 1
        self.messages[uid] = raw
        self.flags[uid] = set(flags or ())
        return uid


class AppendedMessage(msgspec.Struct, frozen=True):
    """Что заглушка получила командой APPEND."""

    folder: str
    raw: bytes
    flags: frozenset[str]
    when: dt.datetime


class FakeMailBox:
    """Ящик в памяти: те же операции, что у `ImapMailBox`, плюс журнал действий.

    Args:
        folders: Имена папок, которые существуют сразу (INBOX и Drafts — всегда).
    """

    def __init__(self, folders: Sequence[str] = ()) -> None:
        self.folders_: dict[str, FakeFolder] = {name: FakeFolder() for name in ('INBOX', 'Drafts', *folders)}
        self.appended: list[AppendedMessage] = []
        self.copies: list[tuple[int, str]] = []
        self.fetched: list[int] = []
        self.sessions = 0
        self.open = False
        self.fail_fetch: set[int] = set()
        self._selected: str | None = None

    def add(self, raw: bytes, folder: str = 'INBOX') -> int:
        """Кладёт письмо в папку (как будто оно пришло)."""
        return self.folders_.setdefault(folder, FakeFolder()).add(raw)

    def renumber(self, folder: str = 'INBOX', uidvalidity: int = 2) -> None:
        """Имитирует смену UIDVALIDITY: те же письма получают новые UID."""
        old = self.folders_[folder]
        fresh = FakeFolder(uidvalidity)
        fresh.next_uid = 1000
        for uid in sorted(old.messages):
            fresh.add(old.messages[uid], old.flags[uid])
        self.folders_[folder] = fresh

    def _folder(self) -> FakeFolder:
        if self._selected is None:
            raise MailboxError('FakeMailBox: папка не выбрана')
        return self.folders_[self._selected]

    async def __aenter__(self) -> Self:
        self.sessions += 1
        self.open = True
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.open = False
        self._selected = None

    async def folders(self) -> list[Folder]:
        return [
            Folder(name=name, flags=frozenset({'\\drafts'} if name == 'Drafts' else set())) for name in self.folders_
        ]

    async def select(self, folder: str) -> FolderState:
        if folder not in self.folders_:
            raise MailboxError(f'FakeMailBox: нет папки {folder}')
        self._selected = folder
        current = self.folders_[folder]
        return FolderState(uidvalidity=current.uidvalidity, uidnext=current.next_uid, exists=len(current.messages))

    async def search(self, *, after_uid: int | None = None, since: dt.date | None = None) -> list[int]:
        uids = sorted(self._folder().messages)
        return [uid for uid in uids if after_uid is None or uid > after_uid]

    async def fetch(self, uid: int) -> bytes | None:
        if uid in self.fail_fetch:
            raise MailboxError(f'FakeMailBox: FETCH {uid} оборвался')
        self.fetched.append(uid)
        return self._folder().messages.get(uid)

    async def append(self, folder: str, message: bytes, when: dt.datetime) -> None:
        if folder not in self.folders_:
            raise MailboxError(f'FakeMailBox: нет папки {folder} для черновика')
        self.appended.append(
            AppendedMessage(folder=folder, raw=message, flags=frozenset({'\\Draft', '\\Seen'}), when=when)
        )

    async def copy(self, uid: int, folder: str) -> None:
        if folder not in self.folders_:
            raise MailboxError(f'FakeMailBox: нет папки {folder} — [TRYCREATE]')
        self.copies.append((uid, folder))

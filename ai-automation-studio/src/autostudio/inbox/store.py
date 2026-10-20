"""Состояние разбора почты в SQLite: что уже разобрано, где остановились, что повторить.

Тела писем здесь не хранятся никогда. Разбор письма (категория, поля), конверт (дата,
отправитель, тема) и текст черновика лежат только до тех пор, пока не выполнены действия
и не записана строка таблицы, затем стираются, а остаются Message-ID, категория и
срочность — для дедупликации и отчёта. Всё, что старше `RETENTION` пайплайна, удаляется.

Идемпотентность держится на трёх вещах: курсор UID на пару (папка, UIDVALIDITY),
список выполненных действий на письмо и Message-ID как ключ, который переживает
смену UIDVALIDITY.
"""

import datetime as dt
import secrets
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final, Literal, Self

import msgspec

from autostudio.inbox.mail import ParsedEmail
from autostudio.inbox.triage import Draft, TriageResult

type Status = Literal['triaged', 'done', 'failed']

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    category TEXT,
    urgency TEXT,
    source TEXT,
    actions TEXT NOT NULL DEFAULT '',
    triage TEXT,
    envelope TEXT,
    draft TEXT,
    sheet_row TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uids (
    folder TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    message_id TEXT,
    done_at TEXT NOT NULL,
    PRIMARY KEY (folder, uidvalidity, uid)
);
CREATE TABLE IF NOT EXISTS cursors (
    folder TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    last_uid INTEGER NOT NULL,
    PRIMARY KEY (folder, uidvalidity)
);
CREATE TABLE IF NOT EXISTS failures (
    folder TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    last_error TEXT NOT NULL,
    message_id TEXT,
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (folder, uidvalidity, uid)
);
CREATE TABLE IF NOT EXISTS locks (
    name TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    token TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS health (
    name TEXT PRIMARY KEY,
    ok_at TEXT NOT NULL
);
"""

# Колонки, добавленные после первой версии схемы: база, созданная раньше, дополняется
# при открытии (CREATE TABLE IF NOT EXISTS старую таблицу не меняет).
_ADDED_COLUMNS: Final[tuple[tuple[str, str, str], ...]] = (
    ('messages', 'envelope', 'ALTER TABLE messages ADD COLUMN envelope TEXT'),
    ('messages', 'draft', 'ALTER TABLE messages ADD COLUMN draft TEXT'),
    ('failures', 'message_id', 'ALTER TABLE failures ADD COLUMN message_id TEXT'),
    ('failures', 'updated_at', "ALTER TABLE failures ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"),
    ('locks', 'token', "ALTER TABLE locks ADD COLUMN token TEXT NOT NULL DEFAULT ''"),
)


class Envelope(msgspec.Struct, frozen=True, kw_only=True):
    """Конверт письма без текста: чтобы дописать строку таблицы, если письмо исчезло из ящика.

    Attributes:
        date: Дата письма.
        from_name: Имя отправителя.
        from_addr: Адрес отправителя.
        reply_to: Reply-To, если отличается.
        subject: Тема.
    """

    date: dt.datetime
    from_name: str = ''
    from_addr: str = ''
    reply_to: str | None = None
    subject: str = ''

    @classmethod
    def of(cls, email: ParsedEmail) -> Self:
        """Конверт разобранного письма."""
        return cls(
            date=email.date,
            from_name=email.from_name,
            from_addr=email.from_addr,
            reply_to=email.reply_to,
            subject=email.subject,
        )

    def as_email(self, message_id: str) -> ParsedEmail:
        """Письмо без текста — для строки таблицы."""
        return ParsedEmail(
            message_id=message_id,
            date=self.date,
            from_name=self.from_name,
            from_addr=self.from_addr,
            reply_to=self.reply_to,
            subject=self.subject,
        )


class StoredMessage(msgspec.Struct, frozen=True, kw_only=True):
    """Что известно о письме по его Message-ID."""

    message_id: str
    status: Status
    category: str | None
    actions: frozenset[str]
    triage: TriageResult | None
    envelope: Envelope | None = None
    draft: Draft | None = None
    created_at: dt.datetime


def _ts(moment: dt.datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError('время должно быть с часовым поясом')
    return moment.astimezone(dt.UTC).isoformat()


def _json[T](raw: str | None, kind: type[T]) -> T | None:
    return None if raw is None else msgspec.json.decode(raw, type=kind)


class InboxStore:
    """Состояние разбора почты одного клиента.

    Args:
        path: Файл SQLite; каталог создаётся при открытии.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for table, column, ddl in _ADDED_COLUMNS:
                existing = {row[1] for row in conn.execute('SELECT * FROM pragma_table_info(?)', (table,))}
                if column not in existing:
                    conn.execute(ddl)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=10)) as conn, conn:
            yield conn

    # --- блокировка запуска ---------------------------------------------------------

    def acquire_lock(self, now: dt.datetime, ttl: dt.timedelta) -> str | None:
        """Берёт блокировку запуска: два запуска по расписанию не разбирают одно письмо дважды.

        Args:
            now: Текущий момент.
            ttl: Срок, после которого брошенная (упавшим процессом) блокировка считается снятой.

        Returns:
            Токен блокировки (нужен, чтобы снять именно её) или None, если идёт другой запуск.
        """
        token = secrets.token_hex(8)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT expires_at FROM locks WHERE name = 'run'").fetchone()
            if row is not None and dt.datetime.fromisoformat(row[0]) > now:
                return None
            conn.execute(
                "INSERT INTO locks (name, expires_at, token) VALUES ('run', ?, ?) "
                'ON CONFLICT(name) DO UPDATE SET expires_at = excluded.expires_at, token = excluded.token',
                (_ts(now + ttl), token),
            )
        return token

    def release_lock(self, token: str | None = None) -> None:
        """Снимает блокировку запуска.

        Args:
            token: Токен из `acquire_lock`. Запуск, который проработал дольше срока блокировки,
                не должен снять чужую — более позднего запуска; None снимает любую (вручную).
        """
        with self._connect() as conn:
            if token is None:
                conn.execute("DELETE FROM locks WHERE name = 'run'")
            else:
                conn.execute("DELETE FROM locks WHERE name = 'run' AND token = ?", (token,))

    # --- здоровье зависимостей -----------------------------------------------------------

    def mark_healthy(self, name: str, now: dt.datetime) -> None:
        """Запоминает, что зависимость (модель) сейчас отвечает."""
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO health (name, ok_at) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET ok_at = excluded.ok_at',
                (name, _ts(now)),
            )

    def healthy_at(self, name: str) -> dt.datetime | None:
        """Когда зависимость последний раз отвечала."""
        with self._connect() as conn:
            row = conn.execute('SELECT ok_at FROM health WHERE name = ?', (name,)).fetchone()
        return None if row is None else dt.datetime.fromisoformat(row[0])

    # --- курсор и UID ---------------------------------------------------------------

    def cursor(self, folder: str, uidvalidity: int) -> int | None:
        """Последний UID, до которого всё разобрано, для этой пары папка/UIDVALIDITY."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT last_uid FROM cursors WHERE folder = ? AND uidvalidity = ?', (folder, uidvalidity)
            ).fetchone()
        return None if row is None else int(row[0])

    def set_cursor(self, folder: str, uidvalidity: int, last_uid: int) -> None:
        """Запоминает курсор (только вперёд)."""
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO cursors (folder, uidvalidity, last_uid) VALUES (?, ?, ?) '
                'ON CONFLICT(folder, uidvalidity) DO UPDATE SET last_uid = MAX(last_uid, excluded.last_uid)',
                (folder, uidvalidity, last_uid),
            )

    def uid_done(self, folder: str, uidvalidity: int, uid: int) -> bool:
        """UID уже разобран (или брошен после повторов) — его не нужно даже скачивать."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT 1 FROM uids WHERE folder = ? AND uidvalidity = ? AND uid = ?', (folder, uidvalidity, uid)
            ).fetchone()
        return row is not None

    def mark_uid(self, folder: str, uidvalidity: int, uid: int, message_id: str | None, now: dt.datetime) -> None:
        """Отмечает UID разобранным и снимает счётчик неудач по нему."""
        with self._connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO uids (folder, uidvalidity, uid, message_id, done_at) VALUES (?, ?, ?, ?, ?)',
                (folder, uidvalidity, uid, message_id, _ts(now)),
            )
            conn.execute(
                'DELETE FROM failures WHERE folder = ? AND uidvalidity = ? AND uid = ?', (folder, uidvalidity, uid)
            )

    # --- неудачи ------------------------------------------------------------------

    def record_failure(
        self,
        folder: str,
        uidvalidity: int,
        uid: int,
        error: str,
        *,
        now: dt.datetime,
        message_id: str | None = None,
        count: bool = True,
    ) -> int:
        """Отмечает неудачную попытку разобрать UID.

        Args:
            folder: Папка.
            uidvalidity: Эпоха UID.
            uid: UID письма.
            error: Текст ошибки (без тела письма).
            now: Текущий момент.
            message_id: Ключ письма, если оно разобралось (по нему письмо дописывается, если исчезнет).
            count: Засчитать попытку. Отложенное из-за общей поломки письмо попытку не тратит,
                но отмечается — следующий запуск возьмёт его после свежих писем.

        Returns:
            Число засчитанных попыток с учётом этой.
        """
        step = 1 if count else 0
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO failures (folder, uidvalidity, uid, attempts, last_error, message_id, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(folder, uidvalidity, uid) DO UPDATE SET attempts = attempts + excluded.attempts, '
                'last_error = excluded.last_error, message_id = COALESCE(excluded.message_id, message_id), '
                'updated_at = excluded.updated_at',
                (folder, uidvalidity, uid, step, error[:500], message_id, _ts(now)),
            )
            row = conn.execute(
                'SELECT attempts FROM failures WHERE folder = ? AND uidvalidity = ? AND uid = ?',
                (folder, uidvalidity, uid),
            ).fetchone()
        return int(row[0])

    def failed_uids(self, folder: str, uidvalidity: int) -> set[int]:
        """UID, которые уже не удавались: следующий запуск берёт их после свежих писем."""
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT uid FROM failures WHERE folder = ? AND uidvalidity = ?', (folder, uidvalidity)
            ).fetchall()
        return {int(uid) for (uid,) in rows}

    def failure_message_id(self, folder: str, uidvalidity: int, uid: int) -> str | None:
        """Message-ID письма, которое не удалось доделать (если оно успело разобраться)."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT message_id FROM failures WHERE folder = ? AND uidvalidity = ? AND uid = ?',
                (folder, uidvalidity, uid),
            ).fetchone()
        return None if row is None else row[0]

    # --- письма -----------------------------------------------------------------------

    def get(self, message_id: str) -> StoredMessage | None:
        """Что известно о письме по Message-ID."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT message_id, status, category, actions, triage, envelope, draft, created_at '
                'FROM messages WHERE message_id = ?',
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        mid, status, category, actions, triage, envelope, draft, created_at = row
        return StoredMessage(
            message_id=mid,
            status=status,
            category=category,
            actions=frozenset(a for a in actions.split(',') if a),
            triage=_json(triage, TriageResult),
            envelope=_json(envelope, Envelope),
            draft=_json(draft, Draft),
            created_at=dt.datetime.fromisoformat(created_at),
        )

    def save_triage(
        self, message_id: str, triage: TriageResult, now: dt.datetime, envelope: Envelope | None = None
    ) -> None:
        """Запоминает разбор до выполнения действий: повтор после сбоя не зовёт модель снова."""
        encoded_envelope = None if envelope is None else msgspec.json.encode(envelope).decode()
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO messages (message_id, status, category, urgency, source, triage, envelope, '
                "created_at, updated_at) VALUES (?, 'triaged', ?, ?, ?, ?, ?, ?, ?) "
                'ON CONFLICT(message_id) DO UPDATE SET triage = excluded.triage, category = excluded.category, '
                'urgency = excluded.urgency, source = excluded.source, '
                'envelope = COALESCE(excluded.envelope, envelope), updated_at = excluded.updated_at',
                (
                    message_id,
                    triage.category,
                    triage.urgency,
                    triage.source,
                    msgspec.json.encode(triage).decode(),
                    encoded_envelope,
                    _ts(now),
                    _ts(now),
                ),
            )

    def save_draft(self, message_id: str, draft: Draft, now: dt.datetime) -> None:
        """Запоминает текст черновика: если APPEND не прошёл, повтор не платит модели второй раз."""
        with self._connect() as conn:
            conn.execute(
                'UPDATE messages SET draft = ?, updated_at = ? WHERE message_id = ?',
                (msgspec.json.encode(draft).decode(), _ts(now), message_id),
            )

    def add_action(self, message_id: str, action: str, now: dt.datetime) -> None:
        """Отмечает выполненное действие (черновик, копия, уведомление) — повтор его пропустит."""
        stored = self.get(message_id)
        actions = sorted((stored.actions if stored else frozenset()) | {action})
        with self._connect() as conn:
            conn.execute(
                'UPDATE messages SET actions = ?, updated_at = ? WHERE message_id = ?',
                (','.join(actions), _ts(now), message_id),
            )

    def finish(
        self,
        message_id: str,
        *,
        status: Status,
        category: str | None,
        urgency: str | None,
        sheet_row: list[str] | None,
        now: dt.datetime,
    ) -> None:
        """Закрывает письмо: разбор, конверт и черновик стираются, остаётся строка до записи в таблицу.

        Args:
            message_id: Message-ID (или служебный ключ непрочитанного письма).
            status: `done` или `failed` (брошено после повторов).
            category: Категория для отчёта.
            urgency: Срочность для отчёта.
            sheet_row: Строка для таблицы клиента или None, если писать нечего.
            now: Текущий момент.
        """
        row = None if sheet_row is None else msgspec.json.encode(sheet_row).decode()
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO messages (message_id, status, category, urgency, sheet_row, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(message_id) DO UPDATE SET status = excluded.status, category = excluded.category, '
                'urgency = excluded.urgency, sheet_row = excluded.sheet_row, triage = NULL, envelope = NULL, '
                'draft = NULL, updated_at = excluded.updated_at',
                (message_id, status, category, urgency, row, _ts(now), _ts(now)),
            )

    # --- строки таблицы -----------------------------------------------------------------

    def pending_rows(self) -> list[tuple[str, list[str]]]:
        """Строки, которые ещё не записаны в таблицу клиента (в порядке разбора)."""
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT message_id, sheet_row FROM messages WHERE sheet_row IS NOT NULL ORDER BY updated_at, rowid'
            ).fetchall()
        return [(mid, msgspec.json.decode(row, type=list[str])) for mid, row in rows]

    def rows_written(self, message_ids: list[str]) -> None:
        """Стирает строки, записанные в таблицу: копия персональных данных больше не нужна."""
        with self._connect() as conn:
            conn.executemany('UPDATE messages SET sheet_row = NULL WHERE message_id = ?', [(m,) for m in message_ids])

    def purge(self, before: dt.datetime) -> int:
        """Удаляет старые записи: о разобранных письмах, брошенные разборы и неудачи.

        Разбор, застрявший в статусе `triaged` (письмо исчезло, процесс упал), тоже
        удаляется — извлечённые поля не должны жить дольше обещанного срока.

        Args:
            before: Граница: всё, что не менялось с этого момента, удаляется.

        Returns:
            Сколько записей о письмах удалено.
        """
        cutoff = _ts(before)
        with self._connect() as conn:
            deleted = conn.execute(
                'DELETE FROM messages WHERE updated_at < ? AND '
                "((status IN ('done', 'failed') AND sheet_row IS NULL) OR status = 'triaged')",
                (cutoff,),
            ).rowcount
            conn.execute('DELETE FROM uids WHERE done_at < ?', (cutoff,))
            conn.execute('DELETE FROM failures WHERE updated_at < ?', (cutoff,))
        return deleted

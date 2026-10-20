"""Состояние разбора почты в SQLite: что уже разобрано, где остановились, что повторить.

Тела писем здесь не хранятся никогда. Разбор письма (категория, поля) лежит только
до тех пор, пока не выполнены действия и не записана строка таблицы, затем поля
стираются, а остаются Message-ID, категория и срочность — для дедупликации и отчёта.

Идемпотентность держится на трёх вещах: курсор UID на пару (папка, UIDVALIDITY),
список выполненных действий на письмо и Message-ID как ключ, который переживает
смену UIDVALIDITY.
"""

import datetime as dt
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final, Literal

import msgspec

from autostudio.inbox.triage import TriageResult

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
    PRIMARY KEY (folder, uidvalidity, uid)
);
CREATE TABLE IF NOT EXISTS locks (
    name TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
);
"""


class StoredMessage(msgspec.Struct, frozen=True, kw_only=True):
    """Что известно о письме по его Message-ID."""

    message_id: str
    status: Status
    category: str | None
    actions: frozenset[str]
    triage: TriageResult | None


def _ts(moment: dt.datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError('время должно быть с часовым поясом')
    return moment.astimezone(dt.UTC).isoformat()


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

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path, timeout=10)) as conn, conn:
            yield conn

    # --- блокировка запуска ---------------------------------------------------------

    def acquire_lock(self, now: dt.datetime, ttl: dt.timedelta) -> bool:
        """Берёт блокировку запуска: два запуска по расписанию не разбирают одно письмо дважды.

        Args:
            now: Текущий момент.
            ttl: Срок, после которого брошенная (упавшим процессом) блокировка считается снятой.

        Returns:
            True, если блокировка взята.
        """
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT expires_at FROM locks WHERE name = 'run'").fetchone()
            if row is not None and dt.datetime.fromisoformat(row[0]) > now:
                return False
            conn.execute(
                "INSERT INTO locks (name, expires_at) VALUES ('run', ?) "
                'ON CONFLICT(name) DO UPDATE SET expires_at = excluded.expires_at',
                (_ts(now + ttl),),
            )
        return True

    def release_lock(self) -> None:
        """Снимает блокировку запуска."""
        with self._connect() as conn:
            conn.execute("DELETE FROM locks WHERE name = 'run'")

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

    def record_failure(self, folder: str, uidvalidity: int, uid: int, error: str) -> int:
        """Считает неудачную попытку разобрать UID.

        Returns:
            Число попыток с учётом этой.
        """
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO failures (folder, uidvalidity, uid, attempts, last_error) VALUES (?, ?, ?, 1, ?) '
                'ON CONFLICT(folder, uidvalidity, uid) DO UPDATE SET attempts = attempts + 1, '
                'last_error = excluded.last_error',
                (folder, uidvalidity, uid, error[:500]),
            )
            row = conn.execute(
                'SELECT attempts FROM failures WHERE folder = ? AND uidvalidity = ? AND uid = ?',
                (folder, uidvalidity, uid),
            ).fetchone()
        return int(row[0])

    # --- письма -----------------------------------------------------------------------

    def get(self, message_id: str) -> StoredMessage | None:
        """Что известно о письме по Message-ID."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT message_id, status, category, actions, triage FROM messages WHERE message_id = ?',
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        mid, status, category, actions, triage = row
        return StoredMessage(
            message_id=mid,
            status=status,
            category=category,
            actions=frozenset(a for a in actions.split(',') if a),
            triage=None if triage is None else msgspec.json.decode(triage, type=TriageResult),
        )

    def save_triage(self, message_id: str, triage: TriageResult, now: dt.datetime) -> None:
        """Запоминает разбор до выполнения действий: повтор после сбоя не зовёт модель снова."""
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO messages (message_id, status, category, urgency, source, triage, created_at, updated_at) '
                "VALUES (?, 'triaged', ?, ?, ?, ?, ?, ?) "
                'ON CONFLICT(message_id) DO UPDATE SET triage = excluded.triage, category = excluded.category, '
                'urgency = excluded.urgency, source = excluded.source, updated_at = excluded.updated_at',
                (
                    message_id,
                    triage.category,
                    triage.urgency,
                    triage.source,
                    msgspec.json.encode(triage).decode(),
                    _ts(now),
                    _ts(now),
                ),
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
        sheet_row: list[str],
        now: dt.datetime,
    ) -> None:
        """Закрывает письмо: поля разбора стираются, остаётся строка до записи в таблицу.

        Args:
            message_id: Message-ID (или служебный ключ непрочитанного письма).
            status: `done` или `failed` (брошено после повторов).
            category: Категория для отчёта.
            urgency: Срочность для отчёта.
            sheet_row: Строка для таблицы клиента.
            now: Текущий момент.
        """
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO messages (message_id, status, category, urgency, sheet_row, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(message_id) DO UPDATE SET status = excluded.status, category = excluded.category, '
                'urgency = excluded.urgency, sheet_row = excluded.sheet_row, triage = NULL, '
                'updated_at = excluded.updated_at',
                (message_id, status, category, urgency, msgspec.json.encode(sheet_row).decode(), _ts(now), _ts(now)),
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
        """Удаляет старые записи о разобранных письмах — дольше они для дедупликации не нужны.

        Args:
            before: Граница: всё закрытое раньше неё удаляется.

        Returns:
            Сколько записей о письмах удалено.
        """
        cutoff = _ts(before)
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM messages WHERE status IN ('done', 'failed') AND sheet_row IS NULL AND updated_at < ?",
                (cutoff,),
            ).rowcount
            conn.execute('DELETE FROM uids WHERE done_at < ?', (cutoff,))
        return deleted

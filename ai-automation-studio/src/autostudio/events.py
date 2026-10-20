"""Журнал событий клиента в SQLite.

Каждый пакет пишет сюда, что он сделал для клиента: запись создана, цена
изменилась, письмо разобрано. Из журнала строится месячный отчёт — то, что
клиент видит за свою абонентскую плату, — и считаются сбои для оператора.

SQLite вызывается синхронно: запись — доли миллисекунды на локальном диске,
а объёмы малого бизнеса (сотни событий в день) не стоят отдельного потока.
"""

import datetime as dt
import sqlite3
from collections.abc import Generator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final

import msgspec

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    package TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
"""


class Event(msgspec.Struct, frozen=True, kw_only=True):
    """Одно действие пакета.

    Attributes:
        at: Момент события, всегда с часовым поясом (UTC в базе).
        package: `booking`, `prices`, `inbox`.
        kind: Короткий код события, например `booking.created`.
        payload: Детали для отчёта (строки, без персональных данных сверх нужного).
    """

    at: dt.datetime
    package: str
    kind: str
    payload: dict[str, str] = msgspec.field(default_factory=dict)


class EventLog:
    """Журнал событий одного клиента.

    Args:
        path: Файл SQLite; каталог создаётся при открытии.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path)) as conn, conn:
            yield conn

    def record(self, package: str, kind: str, at: dt.datetime, payload: Mapping[str, str] | None = None) -> None:
        """Записывает событие.

        Args:
            package: Пакет-источник.
            kind: Код события.
            at: Момент с часовым поясом.
            payload: Детали.

        Raises:
            ValueError: Время без часового пояса — его нельзя честно положить в отчёт.
        """
        if at.tzinfo is None:
            raise ValueError('время события должно быть с часовым поясом')
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO events (at, package, kind, payload) VALUES (?, ?, ?, ?)',
                (at.astimezone(dt.UTC).isoformat(), package, kind, msgspec.json.encode(dict(payload or {})).decode()),
            )

    def last(self, package: str, kinds: tuple[str, ...]) -> Event | None:
        """Последнее событие пакета одного из видов.

        Args:
            package: Пакет-источник.
            kinds: Коды событий, из которых выбирать.

        Returns:
            Самое позднее такое событие или None.
        """
        if not kinds:
            return None
        marks = ', '.join('?' for _ in kinds)
        with self._connect() as conn:
            row = conn.execute(
                f'SELECT at, package, kind, payload FROM events WHERE package = ? AND kind IN ({marks}) '  # noqa: S608 - плейсхолдеры, не данные
                'ORDER BY at DESC, id DESC LIMIT 1',
                (package, *kinds),
            ).fetchone()
        if row is None:
            return None
        at, pkg, kind, payload = row
        return Event(
            at=dt.datetime.fromisoformat(at),
            package=pkg,
            kind=kind,
            payload=msgspec.json.decode(payload, type=dict[str, str]),
        )

    def between(self, start: dt.datetime, end: dt.datetime) -> list[Event]:
        """События в полуинтервале [start, end), по времени.

        Args:
            start: Начало, с часовым поясом.
            end: Конец (не включая), с часовым поясом.

        Returns:
            События по возрастанию времени.
        """
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT at, package, kind, payload FROM events WHERE at >= ? AND at < ? ORDER BY at, id',
                (start.astimezone(dt.UTC).isoformat(), end.astimezone(dt.UTC).isoformat()),
            ).fetchall()
        return [
            Event(
                at=dt.datetime.fromisoformat(at),
                package=package,
                kind=kind,
                payload=msgspec.json.decode(payload, type=dict[str, str]),
            )
            for at, package, kind, payload in rows
        ]

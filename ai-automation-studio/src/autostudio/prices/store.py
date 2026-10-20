"""История цен и состояние ссылок в SQLite.

Сумма хранится строкой (`TEXT`), а не REAL: SQLite округлил бы 2749.10 до ближайшего
двоичного числа, и «изменение цены» появилось бы из ниоткуда. Состояние ссылки — счётчик
сбоев подряд и последнее предупреждение — нужно, чтобы звать оператора один раз на
пороге, а не каждый запуск.

Запуск записывает всё одной транзакцией (`commit`): либо в базе весь результат
проверки, либо ничего — тогда следующий запуск честно повторит ту же работу.
"""

import datetime as dt
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Final

import msgspec

from autostudio.prices.extract import Availability, Method, PriceReading
from autostudio.prices.money import Qualifier

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    product_id TEXT NOT NULL,
    competitor TEXT NOT NULL,
    url TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT,
    method TEXT NOT NULL,
    availability TEXT NOT NULL,
    qualifier TEXT NOT NULL,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS readings_offer ON readings (product_id, competitor, url, at);
CREATE TABLE IF NOT EXISTS offer_state (
    product_id TEXT NOT NULL,
    competitor TEXT NOT NULL,
    url TEXT NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    last_reason TEXT,
    warning TEXT,
    PRIMARY KEY (product_id, competitor, url)
);
"""


class OfferKey(msgspec.Struct, frozen=True):
    """Ссылка конкурента на товар. Сменился URL — это новая страница и новая история."""

    product_id: str
    competitor: str
    url: str


class StoredReading(msgspec.Struct, frozen=True, kw_only=True):
    """Прочитанная ранее цена."""

    at: dt.datetime
    amount: Decimal
    currency: str | None
    method: Method
    availability: Availability
    qualifier: Qualifier
    raw: str


class OfferState(msgspec.Struct, frozen=True, kw_only=True):
    """Состояние ссылки между запусками.

    Attributes:
        failures: Сбоев подряд (0 — последняя проверка удалась).
        last_reason: Причина последнего сбоя.
        warning: Предупреждение последней удачной проверки (например, `selector_not_found`).
    """

    failures: int = 0
    last_reason: str | None = None
    warning: str | None = None


class PriceStore:
    """Хранилище монитора одного клиента.

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
        with closing(sqlite3.connect(self._path)) as conn, conn:
            yield conn

    def last_reading(self, key: OfferKey) -> StoredReading | None:
        """Последняя прочитанная цена ссылки или None, если её ещё не читали."""
        rows = self.readings(key, limit=1)
        return rows[0] if rows else None

    def readings(self, key: OfferKey, limit: int = 100) -> list[StoredReading]:
        """История ссылки, новые сверху.

        Args:
            key: Ссылка.
            limit: Сколько записей вернуть.

        Returns:
            Прочитанные цены.
        """
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT at, amount, currency, method, availability, qualifier, raw FROM readings '
                'WHERE product_id = ? AND competitor = ? AND url = ? ORDER BY at DESC, id DESC LIMIT ?',
                (key.product_id, key.competitor, key.url, limit),
            ).fetchall()
        return [
            StoredReading(
                at=dt.datetime.fromisoformat(at),
                amount=Decimal(amount),
                currency=currency,
                method=Method(method),
                availability=Availability(availability),
                qualifier=Qualifier(qualifier),
                raw=raw,
            )
            for at, amount, currency, method, availability, qualifier, raw in rows
        ]

    def state(self, key: OfferKey) -> OfferState:
        """Состояние ссылки (для новой — нулевое)."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT failures, last_reason, warning FROM offer_state '
                'WHERE product_id = ? AND competitor = ? AND url = ?',
                (key.product_id, key.competitor, key.url),
            ).fetchone()
        if row is None:
            return OfferState()
        return OfferState(failures=row[0], last_reason=row[1], warning=row[2])

    def commit(
        self,
        at: dt.datetime,
        readings: Sequence[tuple[OfferKey, PriceReading]],
        states: Sequence[tuple[OfferKey, OfferState]],
    ) -> None:
        """Записывает итог запуска одной транзакцией.

        Args:
            at: Момент проверки, с часовым поясом.
            readings: Прочитанные цены.
            states: Новое состояние каждой проверенной ссылки.

        Raises:
            ValueError: Время без часового пояса.
        """
        if at.tzinfo is None:
            raise ValueError('время проверки должно быть с часовым поясом')
        stamp = at.astimezone(dt.UTC).isoformat()
        with self._connect() as conn:
            conn.executemany(
                'INSERT INTO readings (at, product_id, competitor, url, amount, currency, method, availability, '
                'qualifier, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    (
                        stamp,
                        key.product_id,
                        key.competitor,
                        key.url,
                        format(reading.amount, 'f'),
                        reading.currency,
                        reading.method.value,
                        reading.availability.value,
                        reading.qualifier.value,
                        reading.raw,
                    )
                    for key, reading in readings
                ],
            )
            conn.executemany(
                'INSERT INTO offer_state (product_id, competitor, url, failures, last_reason, warning) '
                'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (product_id, competitor, url) DO UPDATE SET '
                'failures = excluded.failures, last_reason = excluded.last_reason, warning = excluded.warning',
                [
                    (key.product_id, key.competitor, key.url, state.failures, state.last_reason, state.warning)
                    for key, state in states
                ],
            )

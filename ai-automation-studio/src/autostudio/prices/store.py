"""История цен, состояние ссылок и исходящие сообщения в SQLite.

Сумма хранится строкой (`TEXT`), а не REAL: SQLite округлил бы 2749.10 до ближайшего
двоичного числа, и «изменение цены» появилось бы из ниоткуда. Состояние ссылки — счётчик
сбоев подряд, предупреждения последней проверки и неподтверждённый скачок цены — нужно,
чтобы звать оператора один раз на пороге, а не каждый запуск.

Запуск записывает всё одной транзакцией (`commit`): цены, состояние ссылок и «исходящие»
(`outbox`) — строки для вкладок «History»/«Changes» и уведомление владельцу. Исходящие
удаляются только после доставки, поэтому сбой Google Sheets или Telegram не теряет ни
строки, ни уведомления, а повтор не дублирует уже доставленное.

Проход берёт замок (`exclusive`): cron, `--every-hours` и ручной `run` не пройдут по одному
клиенту одновременно и не пришлют владельцу два одинаковых уведомления.
"""

import datetime as dt
import fcntl
import sqlite3
from collections.abc import Generator, Mapping, Sequence
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

import msgspec

from autostudio.prices.extract import Availability, Method, PriceReading
from autostudio.prices.money import Qualifier
from autostudio.sinks import Cell

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
    raw TEXT NOT NULL,
    own_price TEXT
);
CREATE INDEX IF NOT EXISTS readings_offer ON readings (product_id, competitor, url, at);
CREATE TABLE IF NOT EXISTS offer_state (
    product_id TEXT NOT NULL,
    competitor TEXT NOT NULL,
    url TEXT NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    last_reason TEXT,
    warning TEXT,
    pending TEXT,
    PRIMARY KEY (product_id, competitor, url)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flags (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Колонки, добавленные после первых развёртываний: старый файл базы дополняется на месте.
_ADDED_COLUMNS: Final[dict[tuple[str, str], str]] = {
    ('readings', 'own_price'): 'ALTER TABLE readings ADD COLUMN own_price TEXT',
    ('offer_state', 'pending'): 'ALTER TABLE offer_state ADD COLUMN pending TEXT',
}
_TABLE_COLUMNS: Final[dict[str, str]] = {
    'readings': "SELECT name FROM pragma_table_info('readings')",
    'offer_state': "SELECT name FROM pragma_table_info('offer_state')",
}

type Channel = Literal['sheet', 'owner']
# Строка вкладки: текст — текстом, суммы и проценты — числами (в Google Sheets — числа).
type SheetRow = list[Cell]


class OfferKey(msgspec.Struct, frozen=True):
    """Ссылка конкурента на товар. Сменился URL — это новая страница и новая история."""

    product_id: str
    competitor: str
    url: str


class StoredReading(msgspec.Struct, frozen=True, kw_only=True):
    """Прочитанная ранее цена.

    Attributes:
        own_price: Цена клиента на момент этой проверки (она тоже меняется).
    """

    at: dt.datetime
    amount: Decimal
    currency: str | None
    method: Method
    availability: Availability
    qualifier: Qualifier
    raw: str
    own_price: Decimal | None = None


class OfferState(msgspec.Struct, frozen=True, kw_only=True):
    """Состояние ссылки между запусками.

    Attributes:
        failures: Сбоев подряд (0 — последняя проверка удалась).
        last_reason: Причина последнего сбоя.
        warning: Предупреждения последней удачной проверки через запятую (`selector_not_found`, …).
        pending: Неподтверждённый скачок цены (`<сумма> <валюта>`): ждёт повторного чтения.
    """

    failures: int = 0
    last_reason: str | None = None
    warning: str | None = None
    pending: str | None = None

    @property
    def warnings(self) -> frozenset[str]:
        return frozenset(self.warning.split(',')) if self.warning else frozenset()


class OutboxEntry(msgspec.Struct, frozen=True, kw_only=True):
    """То, что нужно доставить клиенту: строки вкладки или уведомление владельцу.

    Attributes:
        id: Номер в очереди (0 — ещё не записано).
        channel: `sheet` — дописать строки во вкладку; `owner` — сообщение владельцу.
        tab: Вкладка (для `sheet`).
        header: Заголовок вкладки (для `sheet`).
        rows: Строки (для `sheet`): текст и числа.
        text: Текст сообщения (для `owner`).
    """

    id: int = 0
    channel: Channel
    tab: str = ''
    header: list[str] = msgspec.field(default_factory=list)
    rows: list[SheetRow] = msgspec.field(default_factory=list)
    text: str = ''


class _Number(msgspec.Struct, frozen=True):
    # Числовая ячейка в очереди. В JSON Decimal — строка, и без обёртки «2749.00» вернулось бы
    # из очереди текстом: в таблицу клиента ушла бы строка вместо числа.
    n: Decimal


type _StoredCell = str | _Number


class _Body(msgspec.Struct, frozen=True, kw_only=True):
    tab: str = ''
    header: list[str] = msgspec.field(default_factory=list)
    # Очередь первых версий хранила только строки — они читаются так же.
    rows: list[list[_StoredCell]] = msgspec.field(default_factory=list)
    text: str = ''


def _store_cell(cell: Cell) -> _StoredCell:
    return cell if isinstance(cell, str) else _Number(Decimal(cell))


def _load_cell(cell: _StoredCell) -> Cell:
    return cell.n if isinstance(cell, _Number) else cell


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
            for (table, column), statement in _ADDED_COLUMNS.items():
                columns = {row[0] for row in conn.execute(_TABLE_COLUMNS[table])}
                if column not in columns:
                    conn.execute(statement)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path)) as conn, conn:
            yield conn

    @contextmanager
    def exclusive(self) -> Generator[bool]:
        """Замок прохода по этому клиенту.

        Yields:
            True — замок взят; False — другой проход (cron, ручной `run`) ещё идёт.
        """
        lock = self._path.with_suffix('.lock')
        with lock.open('a') as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                locked = False
            else:
                locked = True
            # yield — вне except: исключение из тела `with` (RunSkipped) не должно тащить за собой
            # BlockingIOError как «причину» в логе.
            try:
                yield locked
            finally:
                if locked:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

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
                'SELECT at, amount, currency, method, availability, qualifier, raw, own_price FROM readings '
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
                own_price=Decimal(own_price) if own_price is not None else None,
            )
            for at, amount, currency, method, availability, qualifier, raw, own_price in rows
        ]

    def state(self, key: OfferKey) -> OfferState:
        """Состояние ссылки (для новой — нулевое)."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT failures, last_reason, warning, pending FROM offer_state '
                'WHERE product_id = ? AND competitor = ? AND url = ?',
                (key.product_id, key.competitor, key.url),
            ).fetchone()
        if row is None:
            return OfferState()
        return OfferState(failures=row[0], last_reason=row[1], warning=row[2], pending=row[3])

    def commit(
        self,
        at: dt.datetime,
        readings: Sequence[tuple[OfferKey, PriceReading]],
        states: Sequence[tuple[OfferKey, OfferState]],
        *,
        own_prices: Mapping[OfferKey, Decimal | None] | None = None,
        outbox: Sequence[OutboxEntry] = (),
    ) -> None:
        """Записывает итог запуска одной транзакцией.

        Args:
            at: Момент проверки, с часовым поясом.
            readings: Прочитанные цены.
            states: Новое состояние каждой проверенной ссылки.
            own_prices: Цена клиента для каждой ссылки на момент проверки.
            outbox: Что доставить клиенту (строки вкладок, уведомление).

        Raises:
            ValueError: Время без часового пояса.
        """
        if at.tzinfo is None:
            raise ValueError('время проверки должно быть с часовым поясом')
        stamp = at.astimezone(dt.UTC).isoformat()
        prices = own_prices or {}
        with self._connect() as conn:
            conn.executemany(
                'INSERT INTO readings (at, product_id, competitor, url, amount, currency, method, availability, '
                'qualifier, raw, own_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
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
                        format(own, 'f') if (own := prices.get(key)) is not None else None,
                    )
                    for key, reading in readings
                ],
            )
            conn.executemany(
                'INSERT INTO offer_state (product_id, competitor, url, failures, last_reason, warning, pending) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (product_id, competitor, url) DO UPDATE SET '
                'failures = excluded.failures, last_reason = excluded.last_reason, warning = excluded.warning, '
                'pending = excluded.pending',
                [
                    (
                        key.product_id,
                        key.competitor,
                        key.url,
                        state.failures,
                        state.last_reason,
                        state.warning,
                        state.pending,
                    )
                    for key, state in states
                ],
            )
            conn.executemany(
                'INSERT INTO outbox (created_at, channel, body) VALUES (?, ?, ?)',
                [
                    (
                        stamp,
                        entry.channel,
                        msgspec.json.encode(
                            _Body(
                                tab=entry.tab,
                                header=entry.header,
                                rows=[[_store_cell(cell) for cell in row] for row in entry.rows],
                                text=entry.text,
                            )
                        ).decode(),
                    )
                    for entry in outbox
                ],
            )

    def pending(self) -> list[OutboxEntry]:
        """Недоставленное, в порядке записи."""
        with self._connect() as conn:
            rows = conn.execute('SELECT id, channel, body FROM outbox ORDER BY id').fetchall()
        entries: list[OutboxEntry] = []
        for entry_id, channel, body in rows:
            data = msgspec.json.decode(body, type=_Body)
            entries.append(
                OutboxEntry(
                    id=entry_id,
                    channel='owner' if channel == 'owner' else 'sheet',
                    tab=data.tab,
                    header=data.header,
                    rows=[[_load_cell(cell) for cell in row] for row in data.rows],
                    text=data.text,
                )
            )
        return entries

    def delivered(self, entry_id: int) -> None:
        """Убирает доставленное из очереди."""
        with self._connect() as conn:
            conn.execute('DELETE FROM outbox WHERE id = ?', (entry_id,))

    def flag(self, name: str) -> str | None:
        """Служебная отметка клиента (например, «бюджет модели исчерпан») или None."""
        with self._connect() as conn:
            row = conn.execute('SELECT value FROM flags WHERE name = ?', (name,)).fetchone()
        return None if row is None else str(row[0])

    def set_flag(self, name: str, value: str | None) -> None:
        """Ставит (или снимает при None) служебную отметку."""
        with self._connect() as conn:
            if value is None:
                conn.execute('DELETE FROM flags WHERE name = ?', (name,))
            else:
                conn.execute(
                    'INSERT INTO flags (name, value) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET value = ?',
                    (name, value, value),
                )

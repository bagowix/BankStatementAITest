"""Хранилище офиса в одном SQLite: реестр счетов, книга поступлений и кэш курсов НБГ.

Номер счёта выдаётся один раз и навсегда: счётчик серии хранится отдельно от счетов,
берётся под блокировкой записи (`BEGIN IMMEDIATE`), а триггеры запрещают удалять счёт
и уменьшать счётчик. Аннулированный счёт остаётся в реестре с отметкой — налоговая
и банк клиента должны видеть непрерывную нумерацию.

Файл по умолчанию — `var/office/office.sqlite` (весь `var/` в .gitignore).
"""

import datetime as dt
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Final

import msgspec

from autostudio.errors import ConfigError
from autostudio.office.invoice import Invoice, InvoiceDraft, InvoiceStatus, validate_draft
from autostudio.office.ledger import CHANNELS, Channel, Receipt
from autostudio.office.rates import Rate, RateSource

DB_NAME: Final = 'office.sqlite'
# Второй процесс (две консоли выставляют счёт одновременно) ждёт блокировку, а не падает.
_BUSY_TIMEOUT_S: Final = 30.0

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS invoice_counters (
    prefix TEXT NOT NULL,
    year INTEGER NOT NULL,
    last_seq INTEGER NOT NULL,
    PRIMARY KEY (prefix, year)
);
CREATE TABLE IF NOT EXISTS invoices (
    number TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    year INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    issued_on TEXT NOT NULL,
    client TEXT NOT NULL,
    currency TEXT NOT NULL,
    total TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('issued', 'paid', 'void')),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    void_reason TEXT,
    voided_at TEXT,
    paid_on TEXT,
    UNIQUE (prefix, year, seq)
);
CREATE TRIGGER IF NOT EXISTS invoices_never_deleted BEFORE DELETE ON invoices
BEGIN
    SELECT RAISE(ABORT, 'invoices are never deleted: void them instead');
END;
CREATE TRIGGER IF NOT EXISTS invoice_counters_never_go_back BEFORE UPDATE ON invoice_counters
WHEN NEW.last_seq < OLD.last_seq
BEGIN
    SELECT RAISE(ABORT, 'invoice numbers are never reused');
END;
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_on TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    rate TEXT NOT NULL,
    rate_source TEXT NOT NULL,
    channel TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    client_country TEXT NOT NULL,
    invoice_number TEXT,
    platform_fee TEXT,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS receipts_received_on ON receipts (received_on);
CREATE TABLE IF NOT EXISTS nbg_rates (
    currency TEXT NOT NULL,
    on_date TEXT NOT NULL,
    gel_per_unit TEXT NOT NULL,
    quantity TEXT NOT NULL,
    published_rate TEXT NOT NULL,
    source_date TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (currency, on_date)
);
"""

_RECEIPT_COLUMNS: Final = (
    'id, received_on, amount, currency, rate, rate_source, channel, counterparty, client_country, '
    'invoice_number, platform_fee, note'
)


class InvoiceRecord(msgspec.Struct, frozen=True, kw_only=True):
    """Счёт в реестре со статусом.

    Attributes:
        invoice: Сам счёт, как он был выпущен.
        status: `issued`, `paid` или `void`.
        void_reason: Почему аннулирован.
        paid_on: Дата оплаты.
    """

    invoice: Invoice
    status: InvoiceStatus
    void_reason: str | None = None
    paid_on: dt.date | None = None


def _status(value: str) -> InvoiceStatus:
    match value:
        case 'paid':
            return 'paid'
        case 'void':
            return 'void'
        case _:
            return 'issued'


def _rate_source(value: str) -> RateSource:
    match value:
        case 'manual':
            return 'manual'
        case 'gel':
            return 'gel'
        case _:
            return 'nbg'


def _channel(value: str) -> Channel:
    return next((channel for channel in CHANNELS if channel == value), 'bank')


def format_number(prefix: str, year: int, seq: int) -> str:
    """Номер счёта: `AS-2026-001`; после 999 номер просто становится длиннее."""
    return f'{prefix}{year}-{seq:03d}'


class OfficeStore:
    """Реестр счетов, книга поступлений и кэш курсов.

    Каждый вызов открывает своё соединение: команды CLI короткие, а отдельные
    соединения делают конкурентную выдачу номеров честной.

    Args:
        path: Файл SQLite; каталог создаётся при первом обращении.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: транзакциями управляем сами, чтобы взять блокировку записи до чтения счётчика.
        with closing(sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_S, isolation_level=None)) as conn:
            conn.executescript(_SCHEMA)
            yield conn

    @contextmanager
    def _write(self) -> Generator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
            except BaseException:
                conn.execute('ROLLBACK')
                raise
            conn.execute('COMMIT')

    # --- счета ---------------------------------------------------------------------------

    def issue_invoice(self, draft: InvoiceDraft, *, prefix: str, now: dt.datetime) -> Invoice:
        """Выдаёт следующий номер серии `prefix` + год и сохраняет счёт.

        Args:
            draft: Проверяемый черновик.
            prefix: Префикс номера из профиля оператора.
            now: Момент выпуска, с часовым поясом.

        Returns:
            Счёт с номером.

        Raises:
            ConfigError: Черновик неверен (номер при этом не расходуется).
        """
        validate_draft(draft)
        year = draft.issue_date.year
        with self._write() as conn:
            counter = conn.execute(
                'SELECT last_seq FROM invoice_counters WHERE prefix = ? AND year = ?', (prefix, year)
            ).fetchone()
            highest = conn.execute(
                'SELECT COALESCE(MAX(seq), 0) FROM invoices WHERE prefix = ? AND year = ?', (prefix, year)
            ).fetchone()
            # Счётчик и максимум по счетам: номер не повторится, даже если строку счётчика кто-то потёр руками.
            seq = max(int(counter[0]) if counter else 0, int(highest[0])) + 1
            conn.execute(
                'INSERT INTO invoice_counters (prefix, year, last_seq) VALUES (?, ?, ?) '
                'ON CONFLICT (prefix, year) DO UPDATE SET last_seq = excluded.last_seq',
                (prefix, year, seq),
            )
            invoice = draft.numbered(format_number(prefix, year, seq))
            conn.execute(
                'INSERT INTO invoices (number, prefix, year, seq, issued_on, client, currency, total, status, '
                'payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    invoice.number,
                    prefix,
                    year,
                    seq,
                    invoice.issue_date.isoformat(),
                    invoice.client.name,
                    invoice.currency,
                    str(invoice.total),
                    'issued',
                    msgspec.json.encode(invoice).decode(),
                    now.astimezone(dt.UTC).isoformat(),
                ),
            )
        return invoice

    def get_invoice(self, number: str) -> InvoiceRecord:
        """Счёт из реестра.

        Raises:
            ConfigError: Такого номера нет.
        """
        with self._connect() as conn:
            row = conn.execute(
                'SELECT payload, status, void_reason, paid_on FROM invoices WHERE number = ?', (number.strip(),)
            ).fetchone()
        if row is None:
            raise ConfigError(f'счёта {number} нет в реестре (список: autostudio office invoice list)')
        return self._record(row)

    @staticmethod
    def _record(row: tuple[str, str, str | None, str | None]) -> InvoiceRecord:
        payload, status, void_reason, paid_on = row
        return InvoiceRecord(
            invoice=msgspec.json.decode(payload, type=Invoice),
            status=_status(status),
            void_reason=void_reason,
            paid_on=dt.date.fromisoformat(paid_on) if paid_on else None,
        )

    def list_invoices(self, year: int | None = None) -> list[InvoiceRecord]:
        """Счета по порядку номеров, при желании за один год."""
        query = 'SELECT payload, status, void_reason, paid_on FROM invoices'
        params: tuple[int, ...] = ()
        if year is not None:
            query += ' WHERE year = ?'
            params = (year,)
        with self._connect() as conn:
            rows = conn.execute(query + ' ORDER BY prefix, year, seq', params).fetchall()
        return [self._record(row) for row in rows]

    def void_invoice(self, number: str, *, reason: str, now: dt.datetime) -> InvoiceRecord:
        """Аннулирует счёт: он остаётся в реестре с отметкой, номер больше никому не достаётся.

        Raises:
            ConfigError: Нет такого счёта, он уже аннулирован или оплачен, или нет причины.
        """
        if not reason.strip():
            raise ConfigError('укажите причину аннулирования: --reason "..."')
        with self._write() as conn:
            status = self._current_status(conn, number)
            if status == 'void':
                raise ConfigError(f'счёт {number} уже аннулирован')
            if status == 'paid':
                raise ConfigError(f'счёт {number} оплачен: его не аннулируют, возврат оформляется отдельно')
            conn.execute(
                "UPDATE invoices SET status = 'void', void_reason = ?, voided_at = ? WHERE number = ?",
                (reason.strip(), now.astimezone(dt.UTC).isoformat(), number),
            )
        return self.get_invoice(number)

    def pay_invoice(self, number: str, receipt: Receipt, *, now: dt.datetime) -> tuple[InvoiceRecord, Receipt]:
        """Отмечает счёт оплаченным и записывает поступление — одной транзакцией.

        Raises:
            ConfigError: Нет такого счёта, он аннулирован или уже оплачен.
        """
        with self._write() as conn:
            status = self._current_status(conn, number)
            if status == 'void':
                raise ConfigError(f'счёт {number} аннулирован: оплату запишите как поступление без счёта')
            if status == 'paid':
                raise ConfigError(f'счёт {number} уже отмечен оплаченным')
            conn.execute(
                "UPDATE invoices SET status = 'paid', paid_on = ? WHERE number = ?",
                (receipt.received_on.isoformat(), number),
            )
            saved = self._insert_receipt(conn, msgspec.structs.replace(receipt, invoice_number=number), now)
        return self.get_invoice(number), saved

    @staticmethod
    def _current_status(conn: sqlite3.Connection, number: str) -> InvoiceStatus:
        row = conn.execute('SELECT status FROM invoices WHERE number = ?', (number,)).fetchone()
        if row is None:
            raise ConfigError(f'счёта {number} нет в реестре (список: autostudio office invoice list)')
        return _status(str(row[0]))

    # --- поступления ---------------------------------------------------------------------

    def add_receipt(self, receipt: Receipt, *, now: dt.datetime) -> Receipt:
        """Записывает поступление; номер счёта, если указан, должен быть в реестре и не аннулирован.

        Raises:
            ConfigError: Счёта нет или он аннулирован.
        """
        with self._write() as conn:
            if receipt.invoice_number is not None and self._current_status(conn, receipt.invoice_number) == 'void':
                raise ConfigError(f'счёт {receipt.invoice_number} аннулирован — уберите --invoice')
            return self._insert_receipt(conn, receipt, now)

    @staticmethod
    def _insert_receipt(conn: sqlite3.Connection, receipt: Receipt, now: dt.datetime) -> Receipt:
        cursor = conn.execute(
            'INSERT INTO receipts (received_on, amount, currency, rate, rate_source, channel, counterparty, '
            'client_country, invoice_number, platform_fee, note, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                receipt.received_on.isoformat(),
                str(receipt.amount),
                receipt.currency,
                str(receipt.rate),
                receipt.rate_source,
                receipt.channel,
                receipt.counterparty,
                receipt.client_country,
                receipt.invoice_number,
                None if receipt.platform_fee is None else str(receipt.platform_fee),
                receipt.note,
                now.astimezone(dt.UTC).isoformat(),
            ),
        )
        return msgspec.structs.replace(receipt, id=cursor.lastrowid)

    def receipts(self, *, until: dt.date | None = None) -> list[Receipt]:
        """Поступления по дате получения, при желании — до даты включительно."""
        query = f'SELECT {_RECEIPT_COLUMNS} FROM receipts'  # noqa: S608 - имена колонок — константа модуля
        params: tuple[str, ...] = ()
        if until is not None:
            query += ' WHERE received_on <= ?'
            params = (until.isoformat(),)
        with self._connect() as conn:
            rows = conn.execute(query + ' ORDER BY received_on, id', params).fetchall()
        return [
            Receipt(
                id=int(row_id),
                received_on=dt.date.fromisoformat(received_on),
                amount=Decimal(amount),
                currency=currency,
                rate=Decimal(rate),
                rate_source=_rate_source(rate_source),
                channel=_channel(channel),
                counterparty=counterparty,
                client_country=client_country,
                invoice_number=invoice_number,
                platform_fee=None if platform_fee is None else Decimal(platform_fee),
                note=note,
            )
            for (
                row_id,
                received_on,
                amount,
                currency,
                rate,
                rate_source,
                channel,
                counterparty,
                client_country,
                invoice_number,
                platform_fee,
                note,
            ) in rows
        ]

    # --- курсы ---------------------------------------------------------------------------

    def get_rate(self, currency: str, on_date: dt.date) -> Rate | None:
        """Курс НБГ из кэша."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT gel_per_unit, quantity, published_rate, source_date FROM nbg_rates '
                'WHERE currency = ? AND on_date = ?',
                (currency, on_date.isoformat()),
            ).fetchone()
        if row is None:
            return None
        gel_per_unit, quantity, published_rate, source_date = row
        return Rate(
            currency=currency,
            on_date=on_date,
            gel_per_unit=Decimal(gel_per_unit),
            quantity=Decimal(quantity),
            published_rate=Decimal(published_rate),
            source_date=dt.date.fromisoformat(source_date),
        )

    def put_rate(self, rate: Rate) -> None:
        """Кладёт курс НБГ в кэш (повторный запрос той же даты перезаписывает)."""
        with self._write() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO nbg_rates (currency, on_date, gel_per_unit, quantity, published_rate, '
                'source_date, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (
                    rate.currency,
                    rate.on_date.isoformat(),
                    str(rate.gel_per_unit),
                    str(rate.quantity),
                    str(rate.published_rate),
                    rate.source_date.isoformat(),
                    dt.datetime.now(dt.UTC).isoformat(),
                ),
            )

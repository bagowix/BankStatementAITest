"""Google Sheets через сервисный аккаунт.

Клиент делится таблицей с e-mail сервисного аккаунта (права «Редактор») — и всё:
ни OAuth-экрана, ни проверки приложения Google не нужно.
"""

import asyncio
import logging
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final

from autostudio.errors import SinkError
from autostudio.sinks.base import Cell, Rows
from autostudio.sinks.csv_sink import neutralize_formula

if TYPE_CHECKING:
    from gspread import Spreadsheet, Worksheet

logger = logging.getLogger(__name__)

_SCOPES: Final = ('https://www.googleapis.com/auth/spreadsheets',)


def sheet_cell(value: Cell) -> str | int | float:
    """Ячейка для Sheets API: строки защищены от формул, `Decimal` — числом.

    JSON API таблиц не знает `Decimal`; таблица всё равно хранит число как double,
    так что перевод во float на этой границе — единственное место, где он допустим.
    """
    if isinstance(value, str):
        return neutralize_formula(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _values(rows: Rows) -> list[list[str | int | float]]:
    return [[sheet_cell(cell) for cell in row] for row in rows]


class GoogleSheetsSink:
    """Реализация `TableSink` поверх gspread.

    gspread синхронный, поэтому каждый вызов уходит в поток: бот при этом не замирает.

    Args:
        spreadsheet_id: ID таблицы из её URL.
        credentials_path: JSON-ключ сервисного аккаунта.
    """

    def __init__(self, spreadsheet_id: str, credentials_path: Path) -> None:
        self._spreadsheet_id = spreadsheet_id
        self._credentials_path = credentials_path
        self._book: Spreadsheet | None = None

    async def append(self, tab: str, header: Sequence[str], rows: Rows) -> None:
        await asyncio.to_thread(self._append, tab, header, rows)

    async def replace(self, tab: str, header: Sequence[str], rows: Rows) -> None:
        await asyncio.to_thread(self._replace, tab, header, rows)

    def _open(self) -> 'Spreadsheet':
        if self._book is None:
            # Импорт здесь: gspread и google-auth стоят в extras `sheets`.
            import gspread
            from google.oauth2.service_account import Credentials

            try:
                creds = Credentials.from_service_account_file(str(self._credentials_path), scopes=list(_SCOPES))
                self._book = gspread.authorize(creds).open_by_key(self._spreadsheet_id)
            except Exception as exc:  # gspread и google-auth бросают десятки разных исключений
                raise SinkError(f'не удалось открыть таблицу {self._spreadsheet_id}: {exc}') from None
        return self._book

    def _worksheet(self, tab: str, header: Sequence[str]) -> 'Worksheet':
        import gspread
        from gspread.utils import ValueInputOption

        book = self._open()
        try:
            return book.worksheet(tab)
        except gspread.WorksheetNotFound:
            sheet = book.add_worksheet(title=tab, rows=1000, cols=max(len(header), 1))
            sheet.append_row([neutralize_formula(name) for name in header], value_input_option=ValueInputOption.raw)
            return sheet

    def _append(self, tab: str, header: Sequence[str], rows: Rows) -> None:
        from gspread.utils import ValueInputOption

        try:
            sheet = self._worksheet(tab, header)
            if rows:
                sheet.append_rows(_values(rows), value_input_option=ValueInputOption.raw)
        except SinkError:
            raise
        except Exception as exc:
            raise SinkError(f'Google Sheets, вкладка {tab!r}: {exc}') from None

    def _replace(self, tab: str, header: Sequence[str], rows: Rows) -> None:
        from gspread.utils import ValueInputOption

        try:
            sheet = self._worksheet(tab, header)
            sheet.clear()
            head: list[str | int | float] = [neutralize_formula(name) for name in header]
            sheet.update([head, *_values(rows)], value_input_option=ValueInputOption.raw)
        except SinkError:
            raise
        except Exception as exc:
            raise SinkError(f'Google Sheets, вкладка {tab!r}: {exc}') from None

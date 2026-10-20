"""Таблица в CSV-файлах: для демо, тестов и клиентов без Google."""

import asyncio
import csv
import re
from pathlib import Path
from typing import Final

from autostudio.errors import SinkError
from autostudio.sinks.base import Row

_UNSAFE: Final = re.compile(r'[^\w.-]+', re.UNICODE)
# Ячейка, начинающаяся с этих символов, в Excel и Sheets исполняется как формула.
# Данные приходят от посторонних людей (письма, сайты конкурентов), поэтому
# такие значения экранируются апострофом.
_FORMULA_PREFIXES: Final = ('=', '+', '-', '@', '\t', '\r')


def neutralize_formula(value: str) -> str:
    """Защищает от CSV/formula-инъекции, не трогая обычные отрицательные числа.

    Args:
        value: Значение ячейки.

    Returns:
        Значение, которое таблица покажет как текст.
    """
    if not value.startswith(_FORMULA_PREFIXES):
        return value
    if value.startswith('-') and _looks_numeric(value):
        return value
    return "'" + value


def _looks_numeric(value: str) -> bool:
    try:
        float(value.replace(',', '').replace(' ', ''))
    except ValueError:
        return False
    return True


class CsvSink:
    """Реализация `TableSink`: одна вкладка — один файл `<dir>/<tab>.csv`.

    Args:
        directory: Каталог выгрузок; создаётся при первой записи.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def path_for(self, tab: str) -> Path:
        """Путь файла вкладки (имя очищается от символов, опасных для файловой системы)."""
        name = _UNSAFE.sub('_', tab).strip('._') or 'sheet'
        return self._dir / f'{name}.csv'

    async def append(self, tab: str, header: Row, rows: list[Row]) -> None:
        await asyncio.to_thread(self._write, tab, header, rows, 'a')

    async def replace(self, tab: str, header: Row, rows: list[Row]) -> None:
        await asyncio.to_thread(self._write, tab, header, rows, 'w')

    def _write(self, tab: str, header: Row, rows: list[Row], mode: str) -> None:
        path = self.path_for(tab)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fresh = mode == 'w' or not path.exists() or path.stat().st_size == 0
            with path.open(mode, encoding='utf-8', newline='') as fh:
                writer = csv.writer(fh)
                if fresh:
                    writer.writerow(header)
                writer.writerows([[neutralize_formula(cell) for cell in row] for row in rows])
        except OSError as exc:
            raise SinkError(f'не удалось записать {path}: {exc}') from None

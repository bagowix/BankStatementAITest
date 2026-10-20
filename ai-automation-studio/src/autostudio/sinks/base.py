"""Таблица клиента: то место, где владелец бизнеса видит результат работы."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

# Ячейка: строка — как есть (с защитой от формул), число — числом, чтобы клиент мог
# сортировать, суммировать и строить графики без VALUE().
type Cell = str | int | Decimal
type Row = list[str]
type Rows = Sequence[Sequence[Cell]]


class TableSink(Protocol):
    """Пишет строки во вкладку таблицы.

    Строки пишутся как текст, числа (`int`, `Decimal`) — как числа. Даты и подписи
    форматирует пакет: только он знает, как их хочет видеть клиент.
    """

    async def append(self, tab: str, header: Sequence[str], rows: Rows) -> None:
        """Дописывает строки в конец вкладки, создавая её с заголовком при первом обращении.

        Args:
            tab: Имя вкладки (лист Google Sheets или CSV-файл).
            header: Заголовок колонок.
            rows: Строки данных.

        Raises:
            SinkError: Запись не удалась.
        """
        ...

    async def replace(self, tab: str, header: Sequence[str], rows: Rows) -> None:
        """Заменяет содержимое вкладки целиком (текущий срез: цены сейчас, слоты на неделю).

        Args:
            tab: Имя вкладки.
            header: Заголовок колонок.
            rows: Строки данных.

        Raises:
            SinkError: Запись не удалась.
        """
        ...

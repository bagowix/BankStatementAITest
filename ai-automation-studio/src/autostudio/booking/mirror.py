"""Строки в таблицу владельца — в фоне, по порядку и с ограничением времени.

Таблица — зеркало, а не источник истины, поэтому покупатель не должен ждать Google:
ответ «Вы записаны» и уведомление владельцу уходят сразу, а строка во вкладку
`Bookings` дописывается следом отдельной задачей. Задачи выстроены в цепочку: строки
«создана» и «отменена» одной записи не поменяются местами.

Что бывает при сбоях:

- `SinkError` (нет доступа, квота): строки остаются в очереди процесса и уходят вместе
  со следующей строкой этой вкладки — так короткий сбой Google ничего не теряет. Очередь
  в памяти и ограничена; после перезапуска её нет, а вкладка `Schedule` догоняет
  состояние при ежечасном обновлении.
- Зависание (у gspread по умолчанию нет таймаута): вызов прерывается через
  `SINK_TIMEOUT`, строки не повторяются — запрос мог дойти до Google, и повтор дал бы
  дубликат. Сбой пишется в лог.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Final

from autostudio.errors import SinkError
from autostudio.sinks import Cell, TableSink

logger = logging.getLogger(__name__)

SINK_TIMEOUT_S: Final = 20.0
PENDING_MAX: Final = 500

type Line = Sequence[Cell]


class SheetMirror:
    """Фоновая дозапись строк во вкладки таблицы владельца.

    Args:
        sink: Таблица владельца.
        timeout: Сколько секунд ждать одну запись в таблицу.
    """

    def __init__(self, sink: TableSink, *, timeout: float = SINK_TIMEOUT_S) -> None:
        self._sink = sink
        self._timeout = timeout
        self._pending: dict[str, list[Line]] = {}
        self._last: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending_rows(self) -> int:
        """Сколько строк ждут повтора после сбоя таблицы."""
        return sum(len(rows) for rows in self._pending.values())

    def append(self, tab: str, header: Sequence[str], rows: Sequence[Line]) -> None:
        """Ставит строки в очередь записи и сразу возвращает управление.

        Args:
            tab: Вкладка.
            header: Заголовок (для новой вкладки).
            rows: Строки.
        """
        task = asyncio.get_running_loop().create_task(self._after(self._last, tab, header, rows))
        self._last = task
        # Ссылка на задачу держится до её конца: иначе сборщик мусора может её прервать.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Дожидается всех поставленных записей (штатная остановка, демо, тесты)."""
        while self._tasks:
            await asyncio.wait(set(self._tasks))

    async def _after(
        self, previous: asyncio.Task[None] | None, tab: str, header: Sequence[str], rows: Sequence[Line]
    ) -> None:
        if previous is not None:
            # `wait`, а не `await`: исключения предыдущей задачи уже обработаны внутри неё.
            await asyncio.wait({previous})
        await self._write(tab, header, rows)

    async def _write(self, tab: str, header: Sequence[str], rows: Sequence[Line]) -> None:
        batch = [*self._pending.pop(tab, []), *rows]
        try:
            await asyncio.wait_for(self._sink.append(tab, header, batch), timeout=self._timeout)
        except SinkError as exc:
            self._pending[tab] = batch[-PENDING_MAX:]
            logger.error('строки не попали во вкладку %s (повторим со следующей): %s', tab, exc)
        except TimeoutError:
            logger.error('таблица не ответила за %.0f с, строк во вкладке %s: %d', self._timeout, tab, len(batch))

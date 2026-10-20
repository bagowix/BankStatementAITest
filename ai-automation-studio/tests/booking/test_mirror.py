import asyncio
import time

from autostudio.booking.mirror import PENDING_MAX, SheetMirror
from autostudio.errors import SinkError


class SlowSink:
    """Таблица, которая отвечает с задержкой (по одной на вызов) и копит строки."""

    def __init__(self, delays: list[float]) -> None:
        self.delays = delays
        self.rows: list[str] = []

    async def append(self, tab, header, rows):
        await asyncio.sleep(self.delays.pop(0) if self.delays else 0)
        self.rows += [row[0] for row in rows]

    async def replace(self, tab, header, rows):  # pragma: no cover - зеркало пишет только append
        raise AssertionError


class HangingSink:
    """Google без таймаута: вызов не возвращается никогда."""

    async def append(self, tab, header, rows):
        await asyncio.Event().wait()

    async def replace(self, tab, header, rows):  # pragma: no cover
        await asyncio.Event().wait()


class FlakySink:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.batches: list[list[str]] = []

    async def append(self, tab, header, rows):
        if self.failures:
            self.failures -= 1
            raise SinkError('quota exceeded')
        self.batches.append([row[0] for row in rows])

    async def replace(self, tab, header, rows):  # pragma: no cover
        raise AssertionError


async def test__mirror__append__returns_at_once_rows_written_in_order():
    sink = SlowSink([0.05, 0.0])
    mirror = SheetMirror(sink)
    mirror.append('Bookings', ['h'], [['created']])
    mirror.append('Bookings', ['h'], [['cancelled']])
    assert sink.rows == []  # покупатель не ждёт таблицу
    await mirror.drain()
    # Вторая строка быстрее первой, но «отменена» не обгоняет «создана».
    assert sink.rows == ['created', 'cancelled']


async def test__mirror__sheet_hangs__timeout_logged_next_rows_still_written(caplog):
    mirror = SheetMirror(HangingSink(), timeout=0.05)
    started = time.monotonic()
    mirror.append('Bookings', ['h'], [['a']])
    await mirror.drain()
    assert time.monotonic() - started < 2
    assert 'не ответила за' in caplog.text
    assert mirror.pending_rows == 0  # строки после таймаута не повторяются: мог бы выйти дубликат


async def test__mirror__sink_error__rows_retried_with_next_append(caplog):
    sink = FlakySink(failures=1)
    mirror = SheetMirror(sink)
    mirror.append('Bookings', ['h'], [['first']])
    await mirror.drain()
    assert sink.batches == [] and mirror.pending_rows == 1
    assert 'повторим со следующей' in caplog.text
    mirror.append('Bookings', ['h'], [['second']])
    await mirror.drain()
    assert sink.batches == [['first', 'second']] and mirror.pending_rows == 0


async def test__mirror__long_outage__pending_queue_bounded():
    sink = FlakySink(failures=PENDING_MAX + 10)
    mirror = SheetMirror(sink)
    for index in range(PENDING_MAX + 5):
        mirror.append('Bookings', ['h'], [[str(index)]])
    await mirror.drain()
    assert mirror.pending_rows == PENDING_MAX

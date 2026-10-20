"""Часы как зависимость: в тестах время замораживается, в проде — настоящее UTC."""

import datetime as dt
from typing import Protocol


class Clock(Protocol):
    def __call__(self) -> dt.datetime: ...


def system_now() -> dt.datetime:
    """Текущее время в UTC, всегда с часовым поясом."""
    return dt.datetime.now(dt.UTC)


class FrozenClock:
    """Часы, которые идут только по команде — для тестов и демо-сценариев.

    Args:
        start: Начальный момент, с часовым поясом.
    """

    def __init__(self, start: dt.datetime) -> None:
        if start.tzinfo is None:
            raise ValueError('FrozenClock требует время с часовым поясом')
        self._now = start

    def __call__(self) -> dt.datetime:
        return self._now

    def advance(self, delta: dt.timedelta) -> None:
        self._now += delta

    def set(self, moment: dt.datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError('FrozenClock требует время с часовым поясом')
        self._now = moment

"""Ограничение частоты по ключу: скользящее окно в памяти процесса.

Бот публичный: любой может написать ему сотню сообщений подряд. Каждое свободное
сообщение — вызов модели (деньги оператора), а каждая просьба позвать человека —
уведомление всем администраторам. Окно в памяти достаточно: после перезапуска счёт
начинается заново, и это не страшно — ограничение защищает от потока, а не ведёт учёт.
"""

import datetime as dt
from collections import deque
from typing import Final

_PRUNE_ABOVE: Final = 1000


class RateLimiter:
    """Не больше `limit` событий на ключ за `window`.

    Args:
        limit: Сколько событий разрешено в окне.
        window: Длина окна.
    """

    def __init__(self, limit: int, window: dt.timedelta) -> None:
        if limit < 1:
            raise ValueError('limit должен быть не меньше 1')
        self._limit = limit
        self._window = window
        self._hits: dict[str, deque[dt.datetime]] = {}

    def allow(self, key: str, now: dt.datetime) -> bool:
        """Учитывает событие, если оно укладывается в лимит.

        Args:
            key: Чей счётчик (ключ чата).
            now: Текущий момент.

        Returns:
            True — событие разрешено и учтено; False — лимит исчерпан (событие не учтено).
        """
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        if len(self._hits) > _PRUNE_ABOVE:
            self._prune(now)
        return True

    def _prune(self, now: dt.datetime) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self._window]
        for key in stale:
            del self._hits[key]

    def __len__(self) -> int:
        return len(self._hits)

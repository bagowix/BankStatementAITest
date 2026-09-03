"""Ограничение частоты запросов.

Своя реализация, а не slowapi: у стенда один процесс и нет Redis, а весь нужный
алгоритм — двадцать строк. На проде это место занимает шлюз (nginx, ingress),
который отбивает наплыв до питона; здесь же ограничение защищает от случайного
цикла в скрипте и от одного клиента, забившего очередь разборов.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Final

UPLOAD_BURST: Final[int] = 6
"""Сколько загрузок подряд разрешено одному клиенту. Пачку документов кладут
разом, поэтому запас должен покрывать обычную пачку целиком."""

UPLOAD_REFILL: Final[float] = 0.2
"""Токенов в секунду: примерно двенадцать загрузок в минуту в установившемся
режиме. Разбор одного документа идёт дольше, так что это не узкое место."""

MAX_CLIENTS: Final[int] = 4096
"""Сколько клиентов помнить, прежде чем вычистить молчащих. Словарь растёт с
каждым новым ключом, а за доверенным прокси ключ приходит из заголовка, то есть
его выбирает сам клиент — без уборки это была бы утечка по заказу."""

FORGET_AFTER: Final[float] = 3600.0
"""Через сколько секунд молчания клиент забывается: за час ведро полно, и
помнить о нём больше нечего."""


@dataclass
class TokenBucket:
    """Классическое ведро с токенами: всплеск разрешён, поток ограничен."""

    burst: int
    refill: float
    _tokens: dict[str, float] = field(default_factory=dict)
    _seen: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, key: str, now: float | None = None) -> bool:
        """Списать токен. False — клиент исчерпал лимит."""
        moment = now if now is not None else time.monotonic()

        with self._lock:
            if len(self._seen) >= MAX_CLIENTS:
                self._forget_locked(FORGET_AFTER, moment)

            tokens = self._tokens.get(key, float(self.burst))
            elapsed = moment - self._seen.get(key, moment)
            tokens = min(float(self.burst), tokens + elapsed * self.refill)

            if tokens < 1:
                self._seen[key] = moment
                self._tokens[key] = tokens

                return False

            self._tokens[key] = tokens - 1
            self._seen[key] = moment

            return True

    def forget(self, older_than: float = 3600.0, now: float | None = None) -> None:
        """Забыть давно молчащих клиентов, чтобы словарь не рос без предела."""
        moment = now if now is not None else time.monotonic()

        with self._lock:
            self._forget_locked(older_than, moment)

    def _forget_locked(self, older_than: float, moment: float) -> None:
        stale = [key for key, seen in self._seen.items() if moment - seen > older_than]

        for key in stale:
            self._seen.pop(key, None)
            self._tokens.pop(key, None)

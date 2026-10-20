"""Ограничение частоты: скользящее окно по ключу и темп рассылки напоминаний.

Бот публичный: любой может написать ему сотню сообщений подряд. Каждое свободное
сообщение — вызов модели (деньги оператора), а каждая просьба позвать человека —
уведомление всем администраторам. Окно в памяти достаточно: после перезапуска счёт
начинается заново, и это не страшно — ограничение защищает от потока, а не ведёт учёт.
"""

import asyncio
import datetime as dt
import time
from collections import deque
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Final

_PRUNE_ABOVE: Final = 1000
_DAY: Final = dt.timedelta(days=1)
# Владельцу показывается не больше этого из текста; для сравнения повторов хватает того же.
_TEXT_KEPT: Final = 1000
_EDGE_NOISE: Final = ' \t\n.,!?…¿¡;:)("\'«»'


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

    def would_allow(self, key: str, now: dt.datetime) -> bool:
        """Уложилось бы событие в лимит — без учёта (для проверки нескольких лимитов разом).

        Args:
            key: Чей счётчик.
            now: Текущий момент.

        Returns:
            True, если `allow` сейчас разрешил бы событие.
        """
        hits = self._hits.get(key)
        return hits is None or sum(1 for hit in hits if now - hit < self._window) < self._limit

    def forget(self, key: str) -> None:
        """Обнуляет счётчик ключа."""
        self._hits.pop(key, None)

    def _prune(self, now: dt.datetime) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self._window]
        for key in stale:
            del self._hits[key]

    def __len__(self) -> int:
        return len(self._hits)


class Handoff(StrEnum):
    """Что делать с просьбой позвать человека."""

    FORWARD = 'forward'  # передать администраторам
    REPEAT = 'repeat'  # тот же текст уже передан: повторно владельца не будим, «уже передали» — правда
    LIMITED = 'limited'  # лимит исчерпан: текст не передан, и покупатель должен узнать это честно


def same_text(text: str) -> str:
    """Текст для сравнения повторов: без регистра, лишних пробелов и знаков по краям.

    «Есть парковка?» и «есть  парковка??» — один вопрос, присланный дважды, пока ответа нет.
    """
    return ' '.join(text.casefold().split()).strip(_EDGE_NOISE)[:_TEXT_KEPT]


class HandoffGate:
    """Решает, звать ли администратора: поток уведомлений — нет, потерянный вопрос — тоже нет.

    Повтор уже переданного текста (покупатель не дождался ответа и прислал вопрос ещё раз)
    владельца повторно не будит. Разные тексты передаются до лимита в коротком окне и за
    сутки; сверх лимита решение — `LIMITED`, и вызывающий честно говорит покупателю, что
    сообщение не передано. Ответ администратора покупателю (`release`) обнуляет счёт:
    живой диалог через бота лимит не обрывает.

    Args:
        burst: Сколько разных текстов передаётся за `burst_window`.
        burst_window: Короткое окно.
        daily: Сколько разных текстов передаётся за сутки.
        repeat_window: Сколько помнить переданный текст для распознавания повтора.
    """

    def __init__(self, *, burst: int, burst_window: dt.timedelta, daily: int, repeat_window: dt.timedelta) -> None:
        self._burst = RateLimiter(burst, burst_window)
        self._daily = RateLimiter(daily, _DAY)
        self._repeat_window = repeat_window
        self._sent: dict[str, deque[tuple[dt.datetime, str]]] = {}

    def decide(self, key: str, text: str, now: dt.datetime) -> Handoff:
        """Решение по одной просьбе; `FORWARD` сразу учитывается в лимитах.

        Args:
            key: Ключ чата покупателя.
            text: Текст покупателя.
            now: Текущий момент.

        Returns:
            Передать, «уже передали» или лимит исчерпан.
        """
        normalized = same_text(text)
        sent = self._sent.get(key)
        if sent is not None:
            while sent and now - sent[0][0] >= self._repeat_window:
                sent.popleft()
            if any(seen == normalized for _, seen in sent):
                return Handoff.REPEAT
        # Оба лимита проверяются до учёта: отказ суточного не должен съедать место в коротком окне.
        if not (self._burst.would_allow(key, now) and self._daily.would_allow(key, now)):
            return Handoff.LIMITED
        self._burst.allow(key, now)
        self._daily.allow(key, now)
        self._sent.setdefault(key, deque()).append((now, normalized))
        if len(self._sent) > _PRUNE_ABOVE:
            self._prune(now)
        return Handoff.FORWARD

    def release(self, key: str) -> None:
        """Администратор написал покупателю: разговор живой, счёт и память повторов — заново.

        Args:
            key: Ключ чата покупателя.
        """
        self._burst.forget(key)
        self._daily.forget(key)
        self._sent.pop(key, None)

    def _prune(self, now: dt.datetime) -> None:
        stale = [key for key, sent in self._sent.items() if not sent or now - sent[-1][0] >= self._repeat_window]
        for key in stale:
            del self._sent[key]

    def __len__(self) -> int:
        return len(self._sent)


class SendThrottle:
    """Темп рассылки: не больше `per_second` сообщений в секунду всего и одно в `chat_interval` в чат.

    Telegram просит не больше ~30 сообщений в секунду при рассылке и около одного в секунду в
    один чат, иначе отвечает 429 и на время замолкает для всех — в том числе для живых
    покупателей, которым бот отвечает в эту же секунду. Утренние напоминания салона — как раз
    такая рассылка, поэтому они идут с запасом: 25 в секунду, одно в секунду в чат.

    Args:
        per_second: Сообщений в секунду на всю рассылку.
        chat_interval: Пауза между сообщениями в один чат, секунды.
        monotonic: Часы для пауз (в тестах — свои).
        sleep: Ожидание (в тестах — без настоящего сна).
    """

    def __init__(
        self,
        *,
        per_second: float = 25.0,
        chat_interval: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if per_second <= 0 or chat_interval < 0:
            raise ValueError('per_second > 0, chat_interval >= 0')
        self._spacing = 1.0 / per_second
        self._chat_interval = chat_interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_any = 0.0
        self._next_chat: dict[str, float] = {}

    async def wait(self, chat: str) -> None:
        """Ждёт, пока можно отправить следующее сообщение в этот чат, и занимает это место.

        Args:
            chat: Ключ чата.
        """
        now = self._monotonic()
        ready = max(now, self._next_any, self._next_chat.get(chat, 0.0))
        if ready > now:
            await self._sleep(ready - now)
        self._next_any = ready + self._spacing
        self._next_chat[chat] = ready + self._chat_interval
        if len(self._next_chat) > _PRUNE_ABOVE:
            for key in [key for key, moment in self._next_chat.items() if moment <= ready]:
                del self._next_chat[key]

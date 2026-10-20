"""Модель с запасным выходом: при сбое провайдера работают эвристики пакета.

Бизнес клиента не должен вставать из-за лимита расходов (429
`enforced_spend_limit_reached` до 1-го числа), перегрузки провайдера или ответа не по
схеме. Обёртка переключает вызов на детерминированные эвристики пакета (те же, что
в демо), а оператору сообщает о деградации — не чаще раза в `alert_every`, чтобы
сотня писем подряд не превратилась в сотню сообщений.
"""

import datetime as dt
import logging
from collections.abc import Awaitable, Callable

import msgspec

from autostudio.clock import Clock, system_now
from autostudio.errors import AutoStudioError, LLMError
from autostudio.llm.base import LLMClient, LLMUsage

logger = logging.getLogger(__name__)

type FallbackHook = Callable[[LLMError], Awaitable[None]]


class ResilientLLM:
    """Реализация `LLMClient`: основной провайдер, при `LLMError` — запасной.

    Args:
        primary: Настоящая модель.
        fallback: Эвристики пакета (обычно `FakeLLM`).
        on_fallback: Что сделать при деградации (сигнал оператору) или None.
        clock: Часы — для ограничения частоты сигналов.
        alert_every: Минимальный интервал между сигналами.
    """

    def __init__(
        self,
        primary: LLMClient,
        fallback: LLMClient,
        on_fallback: FallbackHook | None = None,
        clock: Clock = system_now,
        alert_every: dt.timedelta = dt.timedelta(hours=1),
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._on_fallback = on_fallback
        self._clock = clock
        self._alert_every = alert_every
        self._last_alert: dt.datetime | None = None
        self.fallbacks = 0

    @property
    def model(self) -> str:
        return self._primary.model

    @property
    def usage(self) -> LLMUsage:
        return self._primary.usage

    async def extract[T: msgspec.Struct](self, *, system: str, prompt: str, schema: type[T]) -> T:
        try:
            return await self._primary.extract(system=system, prompt=prompt, schema=schema)
        except LLMError as exc:
            await self._degrade(exc)
        return await self._fallback.extract(system=system, prompt=prompt, schema=schema)

    async def write(self, *, system: str, prompt: str) -> str:
        try:
            return await self._primary.write(system=system, prompt=prompt)
        except LLMError as exc:
            await self._degrade(exc)
        return await self._fallback.write(system=system, prompt=prompt)

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()

    async def _degrade(self, exc: LLMError) -> None:
        self.fallbacks += 1
        logger.warning('модель %s недоступна, работают эвристики: %s', self._primary.model, exc)
        if self._on_fallback is None:
            return
        now = self._clock()
        if self._last_alert is not None and now - self._last_alert < self._alert_every:
            return
        self._last_alert = now
        try:
            await self._on_fallback(exc)
        except AutoStudioError as hook_exc:
            logger.error('сигнал о деградации модели не доставлен: %s', hook_exc)

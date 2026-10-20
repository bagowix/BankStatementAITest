"""Сигналы оператору пакета: о переходах, а не о каждом сбое.

Бот обрабатывает сотни сообщений в день; недоступный Google Calendar или отозванный
токен WhatsApp дали бы сотни одинаковых сообщений оператору, которые приучают их не
читать. Поэтому, как и `supervised_run` в ядре, сигнал уходит при переходе: первый сбой
вида (`raise_`), затем тишина, пока вид не восстановится (`clear` — «снова работает»).
Сбой, который не «проходит сам» (баг в коде), повторяется не чаще `repeat_every`.
"""

import datetime as dt
import logging
from typing import Final

from autostudio.clock import Clock, system_now
from autostudio.errors import NotifyError
from autostudio.notify import Notifier

logger = logging.getLogger(__name__)

REPEAT_EVERY: Final = dt.timedelta(hours=1)


class OperatorAlarm:
    """Сигналы оператору по видам сбоев.

    Args:
        operator: Канал оператора или None (тогда — только лог).
        prefix: Начало каждого сигнала, например `⚠️ Demo Barbers / booking`.
        clock: Часы.
        repeat_every: Не чаще этого для повторного `raise_` того же вида.
    """

    def __init__(
        self,
        operator: Notifier | None,
        prefix: str,
        *,
        clock: Clock = system_now,
        repeat_every: dt.timedelta = REPEAT_EVERY,
    ) -> None:
        self._operator = operator
        self._prefix = prefix
        self._clock = clock
        self._repeat_every = repeat_every
        self._raised: dict[str, dt.datetime] = {}

    def active(self, kind: str) -> bool:
        """Поднят ли сейчас сигнал этого вида."""
        return kind in self._raised

    async def raise_(self, kind: str, text: str, *, repeat: bool = False) -> bool:
        """Сообщает о сбое вида `kind`, если о нём ещё не сообщали.

        Args:
            kind: Вид сбоя (`calendar`, `whatsapp_auth`, имя исключения).
            text: Что случилось — без секретов.
            repeat: Повторять не чаще `repeat_every`, пока вид не снят (для багов,
                которые не восстанавливаются сами и о которых нельзя забыть).

        Returns:
            True, если сигнал отправлен (или был бы, будь канал настроен).
        """
        now = self._clock()
        last = self._raised.get(kind)
        if last is not None and (not repeat or now - last < self._repeat_every):
            return False
        self._raised[kind] = now
        await self._send(f'⚠️ {self._prefix}: {text}')
        return True

    async def clear(self, kind: str, text: str) -> bool:
        """Снимает сигнал вида `kind` и сообщает о восстановлении, если сигнал был.

        Args:
            kind: Вид сбоя.
            text: Что восстановилось.

        Returns:
            True, если сигнал был поднят и снят.
        """
        if self._raised.pop(kind, None) is None:
            return False
        await self._send(f'✅ {self._prefix}: {text}')
        return True

    async def _send(self, text: str) -> None:
        if self._operator is None:
            logger.warning('сигнал оператору (канал не настроен): %s', text)
            return
        try:
            await self._operator.send(text)
        except NotifyError as exc:
            logger.error('оператор не уведомлён: %s', exc)

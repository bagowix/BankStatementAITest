"""Общий путь канала: входящее → движок → доставка ответов → сигнал оператору о сбое.

Telegram и WhatsApp отличаются разметкой и транспортом, но граница адаптера у них
одна: любой сбой движка (в том числе баг) — извинение покупателю и сигнал оператору,
а не молчание бота; администратор, до которого не доходят уведомления, — тоже сигнал.
"""

import logging
from collections.abc import Callable, Sequence

from autostudio.booking.alarm import OperatorAlarm
from autostudio.booking.engine import Engine
from autostudio.booking.messages import Incoming, Outgoing, Sender, deliver
from autostudio.booking.texts import Texts
from autostudio.notify import Notifier

logger = logging.getLogger(__name__)

type Shape = Callable[[list[Outgoing]], list[Outgoing]]


class Relay:
    """Прогон события через движок и доставка ответов адресатам.

    Args:
        engine: Движок разговора.
        sender: Отправка (обычно `RoutingSender` по всем каналам).
        texts: Тексты (извинение при сбое).
        admins: Ключи чатов администраторов.
        notifier: Дополнительный канал владельца `[notify]` или None.
        alarm: Сигналы оператору.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        sender: Sender,
        texts: Texts,
        admins: Sequence[str],
        notifier: Notifier | None,
        alarm: OperatorAlarm,
    ) -> None:
        self._engine = engine
        self._sender = sender
        self._texts = texts
        self._admins = list(admins)
        self._notifier = notifier
        self._alarm = alarm

    @property
    def notifier(self) -> Notifier | None:
        return self._notifier

    async def process(self, incoming: Incoming, *, shape: Shape | None = None) -> list[str]:
        """Обрабатывает событие и доставляет ответы.

        Args:
            incoming: Событие канала.
            shape: Преобразование ответов перед доставкой (WhatsApp склеивает пары сообщений).

        Returns:
            Адресаты, которым доставить не удалось.
        """
        try:
            replies = await self._engine.handle(incoming)
        except Exception as exc:
            # Граница адаптера: любой сбой (в том числе баг) — извинение покупателю и сигнал
            # оператору, а не молчание бота. Трассировка — в лог.
            logger.exception('сбой обработки сообщения от %s', incoming.user)
            language = self._texts.resolve_language(incoming.language)
            replies = [Outgoing(text=self._texts.get(language, 'error'))]
            kind = type(exc).__name__
            await self._alarm.raise_(kind, f'сбой обработки сообщения: {kind}', repeat=True)
        if shape is not None:
            replies = shape(replies)
        failed = await deliver(self._sender, incoming.user, replies, admins=self._admins, notifier=self._notifier)
        unreachable = sorted(set(failed) & set(self._admins))
        if unreachable:
            # Владелец, не нажавший /start или заблокировавший бота, не узнает о записях —
            # об этом должен узнать оператор, а не только лог.
            await self._alarm.raise_(
                'admin_unreachable',
                f'администратор {", ".join(unreachable)} не получает сообщения бота '
                '(не нажал /start или заблокировал бота)',
                repeat=True,
            )
        return failed

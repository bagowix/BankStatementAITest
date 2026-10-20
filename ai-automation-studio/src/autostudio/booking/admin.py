"""Служебные команды владельца и сотрудников: расписание, отмена записи, ответ покупателю.

Работают в том же боте, что и запись: владельцу не нужен второй инструмент. Кто
администратор, решает адаптер канала по `admin_chat_ids`; сюда попадают только
их сообщения. Отмена — в два шага (команда, затем кнопка), потому что после неё
покупатель сразу получает сообщение.

Сообщения покупателю от имени бизнеса (ответ `/reply`, извинение за отмену) уходят с
квитанцией: администратор видит «доставлено» только после настоящей доставки, а при
сбое — что сделать (позвонить). Расписание длиннее лимита канала режется по дням.
"""

import datetime as dt
from collections.abc import Sequence
from typing import Final

from autostudio.booking import callbacks as cb
from autostudio.booking.config import BookingConfig
from autostudio.booking.domain import Booking, BookingError, day_bounds, format_phone, local_today
from autostudio.booking.messages import Button, Incoming, Outgoing, Receipt, split_text
from autostudio.booking.service import BookingService
from autostudio.booking.texts import Texts

ADMIN_COMMANDS: Final[frozenset[str]] = frozenset({'today', 'tomorrow', 'week', 'cancel_booking', 'reply', 'admin'})
_WEEK_DAYS: Final = 7


def render_agenda(
    config: BookingConfig, texts: Texts, bookings: Sequence[Booking], first_day: dt.date, days: int, language: str
) -> str:
    """Расписание по дням: время, мастер, услуга, клиент, код записи.

    Args:
        config: Конфиг клиента.
        texts: Тексты.
        bookings: Активные записи на эти дни.
        first_day: Первый местный день.
        days: Сколько дней.
        language: Язык владельца.

    Returns:
        Текст для консоли (целиком, без деления на сообщения).
    """
    return '\n\n'.join(agenda_blocks(config, texts, bookings, first_day, days, language))


def agenda_blocks(
    config: BookingConfig, texts: Texts, bookings: Sequence[Booking], first_day: dt.date, days: int, language: str
) -> list[str]:
    """Расписание блоками по дням (см. `render_agenda`): из них собираются сообщения под лимит канала."""
    zone = config.client.zone
    blocks: list[str] = []
    for offset in range(days):
        day = first_day + dt.timedelta(days=offset)
        lines = [texts.get(language, 'admin_agenda_day', day=texts.date(day, language))]
        todays = [booking for booking in bookings if booking.start.astimezone(zone).date() == day]
        lines += [
            texts.get(
                language,
                'admin_agenda_line',
                start=texts.time(booking.start),
                end=texts.time(booking.end),
                staff=texts.staff_name(booking.staff_id),
                service=texts.service_name(booking.service_id, language),
                name=booking.name,
                phone=format_phone(booking.phone),
                id=booking.id,
            )
            for booking in sorted(todays, key=lambda item: (item.start, item.staff_id))
        ] or [texts.get(language, 'admin_agenda_empty')]
        blocks.append('\n'.join(lines))
    return blocks


class AdminDesk:
    """Команды `/today`, `/tomorrow`, `/week`, `/cancel_booking <id>`, `/reply <user> <текст>`, `/admin`.

    Args:
        config: Конфиг клиента.
        texts: Тексты.
        service: Действия с записями.
    """

    def __init__(self, *, config: BookingConfig, texts: Texts, service: BookingService) -> None:
        self._config = config
        self._texts = texts
        self._service = service

    @property
    def _language(self) -> str:
        return self._config.owner_language

    @staticmethod
    def parse_command(text: str) -> tuple[str, str]:
        """`/today@DemoBot args` → (`today`, `args`): в группах Telegram добавляет имя бота."""
        # Не `aiogram.filters.Command.extract_command`: разбор общий для консоли и WhatsApp, а движок не
        # знает про Telegram. На случаях из тестов результаты совпадают, кроме регистра «/Today»
        # (var/review_booking/compare_libraries.py).
        head, _, rest = text.strip().partition(' ')
        return head.removeprefix('/').split('@', 1)[0].lower(), rest.strip()

    def owns(self, message: Incoming) -> bool:
        """Это служебная команда или кнопка администратора."""
        if message.data is not None:
            return cb.split(message.data)[0] == cb.ADMIN_CANCEL
        text = (message.text or '').strip()
        return text.startswith('/') and self.parse_command(text)[0] in ADMIN_COMMANDS

    async def handle(self, message: Incoming, now: dt.datetime) -> list[Outgoing] | None:
        """Обрабатывает служебную команду администратора.

        Args:
            message: Сообщение администратора.
            now: Текущий момент.

        Returns:
            Ответы или None, если это не служебная команда (тогда — обычный разговор).
        """
        if not self.owns(message):
            return None
        if message.data is not None:
            _, args = cb.split(message.data)
            return await self._cancel(args[0] if args else '', now)
        command, rest = self.parse_command(message.text or '')
        today = local_today(self._config, now)
        match command:
            case 'today':
                return self.agenda(today, 1)
            case 'tomorrow':
                return self.agenda(today + dt.timedelta(days=1), 1)
            case 'week':
                return self.agenda(today, _WEEK_DAYS)
            case 'cancel_booking':
                return [self._ask_cancel(rest)]
            case 'reply':
                return self._reply(rest)
            case _:
                return [Outgoing(text=self._texts.get(self._language, 'admin_help'))]

    def agenda(self, first_day: dt.date, days: int) -> list[Outgoing]:
        """Расписание на дни из базы (с уже прошедшими записями этих дней).

        Неделя загруженного салона — это тысячи символов: она делится на сообщения по
        границам дней (а большой день — по строкам), чтобы канал не обрезал хвост.
        """
        zone = self._config.client.zone
        start = day_bounds(first_day, zone)[0]
        end = day_bounds(first_day + dt.timedelta(days=days - 1), zone)[1]
        bookings = self._service.store.upcoming(start, end)
        blocks = agenda_blocks(self._config, self._texts, bookings, first_day, days, self._language)
        return [Outgoing(text=text) for text in split_text(blocks)]

    def _ask_cancel(self, raw_id: str) -> Outgoing:
        t, lang = self._texts, self._language
        booking_id = raw_id.strip().upper()
        if not booking_id:
            return Outgoing(text=t.get(lang, 'admin_usage_cancel'))
        booking = self._service.store.get(booking_id)
        if booking is None or not booking.active:
            return Outgoing(text=t.get(lang, 'admin_not_found', id=booking_id))
        return Outgoing(
            text=t.get(
                lang,
                'admin_cancel_confirm',
                id=booking.id,
                service=t.service_name(booking.service_id, lang),
                when=t.when(booking.start, lang),
                staff=t.staff_name(booking.staff_id),
                name=booking.name,
                phone=format_phone(booking.phone),
            ),
            buttons=[[Button(t.get(lang, 'btn_admin_cancel', id=booking.id), cb.join(cb.ADMIN_CANCEL, booking.id))]],
        )

    async def _cancel(self, booking_id: str, now: dt.datetime) -> list[Outgoing]:
        t, lang = self._texts, self._language
        try:
            booking = await self._service.cancel(booking_id, now=now, by_admin=True)
        except BookingError:
            return [Outgoing(text=t.get(lang, 'admin_not_found', id=booking_id))]
        done = Outgoing(text=t.get(lang, 'admin_cancel_done', id=booking.id))
        if booking.source == 'admin':
            # Гость «с улицы»: чат записи — администратора, извиняться в Telegram некому.
            return [done]
        customer_lang = booking.language
        return [
            done,
            Outgoing(
                to=booking.customer,
                text=t.get(
                    customer_lang,
                    'cancelled_by_business',
                    business=self._config.client.name,
                    id=booking.id,
                    service=t.service_name(booking.service_id, customer_lang),
                    when=t.when(booking.start, customer_lang),
                ),
                buttons=[[Button(t.get(customer_lang, 'btn_book_again'), cb.join(cb.SERVICE, booking.service_id))]],
                receipt=Receipt(
                    delivered=t.get(lang, 'admin_cancel_notified'),
                    failed=t.get(lang, 'admin_cancel_not_notified', phone=format_phone(booking.phone)),
                ),
            ),
        ]

    def _reply(self, rest: str) -> list[Outgoing]:
        t, lang = self._texts, self._language
        ref, _, text = rest.partition(' ')
        if not ref or not text.strip():
            return [Outgoing(text=t.get(lang, 'admin_usage_reply'))]
        store = self._service.store
        # Ключ набирают руками: «12345» без префикса канала понимаем, если такой покупатель один;
        # незнакомый ключ без префикса — почти всегда опечатка, и молча «отправлять» его нельзя.
        user = store.find_customer(ref) or (ref if ':' in ref else None)
        if user is None:
            return [Outgoing(text=t.get(lang, 'admin_reply_unknown', user=ref))]
        profile = store.customer(user)
        customer_lang = (profile.language if profile else None) or self._config.booking.default_language
        relay = t.get(customer_lang, 'admin_message', business=self._config.client.name, text=text.strip())
        receipt = Receipt(
            delivered=t.get(lang, 'admin_reply_sent', user=user), failed=t.get(lang, 'admin_reply_failed', user=user)
        )
        return [Outgoing(to=user, text=relay, receipt=receipt)]

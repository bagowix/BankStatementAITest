"""Экраны бота: текст и кнопки для каждого шага, без логики переходов.

Всё, что видит покупатель, собирается здесь из текстов (`texts`) и данных кнопок
(`callbacks`); движок решает, какой экран показать. Так тексты и раскладка кнопок
меняются в одном месте, а лимит 64 байта на данные кнопки проверяет конструктор `Button`.
"""

import datetime as dt
from collections.abc import Callable, Sequence
from typing import Final

from autostudio.booking import callbacks as cb
from autostudio.booking.callbacks import SlotRef
from autostudio.booking.config import BookingConfig, Service
from autostudio.booking.domain import Booking, Customer, Slot, Source
from autostudio.booking.messages import ADMINS, Button, Outgoing
from autostudio.booking.texts import Texts

TIMES_PER_PAGE: Final = 24
DAYS_PER_PAGE: Final = 14
SUGGESTIONS: Final = 8

type Rows = list[list[Button]]


def chunk(buttons: Sequence[Button], size: int) -> Rows:
    """Раскладывает кнопки по рядам."""
    return [list(buttons[index : index + size]) for index in range(0, len(buttons), size)]


class Views:
    """Сборка экранов.

    Args:
        config: Конфиг клиента.
        texts: Тексты.
    """

    def __init__(self, config: BookingConfig, texts: Texts) -> None:
        self._config = config
        self._texts = texts

    @property
    def texts(self) -> Texts:
        return self._texts

    def _t(self, language: str, key: str, **values: str | int) -> str:
        return self._texts.get(language, key, **values)

    def menu_button(self, language: str) -> Button:
        return Button(self._t(language, 'btn_menu'), cb.MENU)

    def menu(self, language: str, text: str | None = None) -> Outgoing:
        """Главное меню (с выбором языка, если их больше одного)."""
        t = self._t
        rows: Rows = [
            [Button(t(language, 'btn_book'), cb.BOOK)],
            [Button(t(language, 'btn_my'), cb.MY), Button(t(language, 'btn_prices'), cb.PRICES)],
            [Button(t(language, 'btn_ask'), cb.ASK), Button(t(language, 'btn_human'), cb.HUMAN)],
        ]
        others = [lang for lang in self._config.booking.languages if lang != language]
        if others:
            rows.append(
                [
                    Button(t(language, 'btn_language', name=t(lang, 'language_name')), cb.join(cb.LANGUAGE, lang))
                    for lang in others
                ]
            )
        return Outgoing(text=text or t(language, 'menu_prompt'), buttons=rows)

    def greeting(self, language: str, name: str) -> list[Outgoing]:
        """Приветствие и меню."""
        suffix = f', {name}' if name else ''
        text = self._t(language, 'greeting', name=suffix, business=self._config.client.name)
        return [Outgoing(text=text), self.menu(language)]

    def notice_and_menu(self, language: str, key: str, **values: str | int) -> list[Outgoing]:
        """Короткое сообщение и меню следом."""
        return [Outgoing(text=self._t(language, key, **values)), self.menu(language)]

    def services(self, language: str, intro: str | None = None) -> Outgoing:
        """Выбор услуги."""
        texts = self._texts
        rows = [
            [
                Button(
                    self._t(
                        language,
                        'service_option',
                        name=texts.pick(service.name, language),
                        price=texts.money(service.price, service.currency),
                    ),
                    cb.join(cb.SERVICE, service.id),
                )
            ]
            for service in self._config.booking.services
        ]
        text = self._t(language, 'choose_service')
        return Outgoing(text=f'{intro}\n\n{text}' if intro else text, buttons=[*rows, [self.menu_button(language)]])

    def prices(self, language: str) -> Outgoing:
        """Услуги, цены и длительность."""
        texts = self._texts
        lines = [self._t(language, 'prices_title')] + [
            self._t(
                language,
                'price_line',
                name=texts.pick(service.name, language),
                price=texts.money(service.price, service.currency),
                duration=texts.duration(service.duration_min, language),
            )
            for service in self._config.booking.services
        ]
        rows = [[Button(self._t(language, 'btn_book'), cb.BOOK)], [self.menu_button(language)]]
        return Outgoing(text='\n'.join(lines), buttons=rows)

    def staff_picker(self, service: Service, language: str) -> Outgoing:
        """Выбор мастера (показывается, только если их больше одного)."""
        staff = self._config.booking.eligible_staff(service)
        buttons = [Button(member.name, cb.join(cb.STAFF, service.id, member.id)) for member in staff]
        any_staff = Button(self._t(language, 'btn_any_staff'), cb.join(cb.STAFF, service.id, cb.ANY_STAFF))
        return Outgoing(
            text=self._t(language, 'choose_staff', service=self._texts.pick(service.name, language)),
            buttons=[*chunk(buttons, 2), [any_staff], [self.menu_button(language)]],
        )

    def no_days(self, service_id: str, language: str) -> list[Outgoing]:
        """Нет свободного времени на весь горизонт."""
        text = self._t(
            language,
            'no_days',
            service=self._texts.service_name(service_id, language),
            days=self._texts.days(self._config.booking.horizon_days, language),
        )
        return [Outgoing(text=self._with_phone(text, language)), self.menu(language)]

    def _with_phone(self, text: str, language: str) -> str:
        phone = self._config.booking.contact_phone
        return f'{text}\n{self._t(language, "call_us", phone=phone)}' if phone else text

    def day_picker(
        self,
        *,
        text: str,
        days: Sequence[dt.date],
        today: dt.date,
        language: str,
        day_data: Callable[[dt.date], str],
        more: str | None,
        back: Button,
    ) -> Outgoing:
        """Выбор дня.

        Args:
            text: Заголовок.
            days: Дни со свободным временем (не больше страницы).
            today: Сегодня (для «Сегодня»/«Завтра»).
            language: Язык покупателя.
            day_data: Данные кнопки дня.
            more: Данные кнопки «Позже» или None.
            back: Кнопка возврата.

        Returns:
            Экран.
        """
        buttons = [Button(self._texts.day_label(day, language, today), day_data(day)) for day in days]
        rows = chunk(buttons, 3)
        if more is not None:
            rows.append([Button(self._t(language, 'btn_later'), more)])
        rows.append([back])
        return Outgoing(text=text, buttons=rows)

    def time_picker(
        self,
        *,
        text: str,
        slots: Sequence[Slot],
        language: str,
        slot_data: Callable[[Slot], str],
        more: str | None,
        back: Sequence[Button],
    ) -> Outgoing:
        """Выбор времени в пределах дня (кнопки `HH:MM`, по 4 в ряд)."""
        buttons = [Button(self._texts.time(slot.start), slot_data(slot)) for slot in slots]
        rows = chunk(buttons, 4)
        if more is not None:
            rows.append([Button(self._t(language, 'btn_later'), more)])
        rows.append(list(back))
        return Outgoing(text=text, buttons=rows)

    def suggestion_rows(
        self, slots: Sequence[Slot], language: str, today: dt.date, slot_data: Callable[[Slot], str]
    ) -> Rows:
        """Кнопки слотов разных дней: «Завтра 17:00» (ответ на свободный текст и альтернативы)."""
        texts = self._texts
        buttons = [
            Button(
                f'{texts.day_label(texts.local(slot.start).date(), language, today)} {texts.time(slot.start)}',
                slot_data(slot),
            )
            for slot in slots
        ]
        return chunk(buttons, 2)

    def slot_ref_data(self, action: str, source: Source, staff_id: str | None) -> Callable[[Slot], str]:
        """Фабрика данных кнопок слота: мастер `None` остаётся «любым» до подтверждения."""

        def build(slot: Slot) -> str:
            ref = SlotRef(source=source, service_id=slot.service_id, staff_id=staff_id, start=slot.start)
            return cb.slot_data(action, ref)

        return build

    def _name_buttons(self, display_name: str | None, language: str) -> Rows:
        rows: Rows = []
        if display_name:
            rows.append([Button(self._t(language, 'btn_use_name', name=display_name), cb.USE_NAME)])
        rows.append([Button(self._t(language, 'btn_abort'), cb.ABORT)])
        return rows

    def ask_name(self, slot: Slot, display_name: str | None, language: str) -> Outgoing:
        """Шаг «на какое имя записать» (с кнопкой «записать как <имя из Telegram>»)."""
        text = self._t(language, 'ask_name', slot=self._texts.when(slot.start, language))
        return Outgoing(text=text, buttons=self._name_buttons(display_name, language))

    def bad_name(self, display_name: str | None, language: str) -> Outgoing:
        """Повтор шага имени с подсказкой."""
        return Outgoing(text=self._t(language, 'bad_name'), buttons=self._name_buttons(display_name, language))

    def ask_phone(self, name: str, language: str) -> Outgoing:
        """Шаг «телефон»: клавиатура с кнопкой «Отправить мой номер»."""
        return Outgoing(
            text=self._t(language, 'ask_phone', name=name),
            request_contact=self._t(language, 'btn_share_phone'),
            keyboard=[self._t(language, 'btn_abort')],
        )

    def confirm(self, ref: SlotRef, slot: Slot, customer: Customer, language: str) -> Outgoing:
        """Проверка перед записью: что, когда, к кому, на кого."""
        texts = self._texts
        service = self._config.booking.service(slot.service_id)
        price = '' if service is None else texts.money(service.price, service.currency)
        text = self._t(
            language,
            'confirm',
            service=texts.service_name(slot.service_id, language),
            price=price,
            when=texts.when(slot.start, language),
            staff=texts.staff_name(slot.staff_id),
            name=customer.name or '',
            phone=customer.phone or '',
        )
        rows = [
            [Button(self._t(language, 'btn_confirm'), cb.slot_data(cb.CONFIRM, ref))],
            [Button(self._t(language, 'btn_edit'), cb.slot_data(cb.EDIT, ref))],
            [Button(self._t(language, 'btn_abort'), cb.ABORT)],
        ]
        return Outgoing(text=text, buttons=rows)

    def booked(self, booking: Booking, language: str) -> Outgoing:
        """Запись создана."""
        texts = self._texts
        text = self._t(
            language,
            'booked',
            service=texts.service_name(booking.service_id, language),
            when=texts.when(booking.start, language),
            staff=texts.staff_name(booking.staff_id),
            id=booking.id,
        )
        rows = [[Button(self._t(language, 'btn_my'), cb.MY), self.menu_button(language)]]
        return Outgoing(text=text, buttons=rows)

    def my_bookings(self, bookings: Sequence[Booking], language: str, intro_key: str = 'my_list') -> list[Outgoing]:
        """Список предстоящих записей покупателя (только его собственных)."""
        if not bookings:
            return self.notice_and_menu(language, 'my_none')
        texts = self._texts
        rows = [
            [
                Button(
                    self._t(
                        language,
                        'booking_line',
                        when=texts.when(booking.start, language),
                        service=texts.service_name(booking.service_id, language),
                    ),
                    cb.join(cb.BOOKING, booking.id),
                )
            ]
            for booking in bookings
        ]
        return [Outgoing(text=self._t(language, intro_key), buttons=[*rows, [self.menu_button(language)]])]

    def booking_detail(self, booking: Booking, language: str) -> Outgoing:
        """Карточка записи: отменить, перенести, назад."""
        texts = self._texts
        service = self._config.booking.service(booking.service_id)
        price = '' if service is None else texts.money(service.price, service.currency)
        text = self._t(
            language,
            'booking_detail',
            id=booking.id,
            service=texts.service_name(booking.service_id, language),
            price=price,
            when=texts.when(booking.start, language),
            staff=texts.staff_name(booking.staff_id),
        )
        rows = [
            [Button(self._t(language, 'btn_cancel_booking'), cb.join(cb.CANCEL_ASK, booking.id))],
            [Button(self._t(language, 'btn_reschedule'), cb.join(cb.MOVE, booking.id))],
            [Button(self._t(language, 'btn_back'), cb.MY)],
        ]
        return Outgoing(text=text, buttons=rows)

    def admin_new(self, booking: Booking) -> Outgoing:
        """Уведомление владельцу о новой записи."""
        lang, texts = self._config.owner_language, self._texts
        return Outgoing(
            to=ADMINS,
            text=self._t(
                lang,
                'admin_new',
                id=booking.id,
                service=texts.service_name(booking.service_id, lang),
                when=texts.when(booking.start, lang),
                staff=texts.staff_name(booking.staff_id),
                name=booking.name,
                phone=booking.phone,
                source=self._t(lang, f'source_{booking.source}'),
            ),
        )

    def admin_cancelled(self, booking: Booking, *, by_admin: bool) -> Outgoing:
        """Уведомление владельцу об отмене."""
        lang, texts = self._config.owner_language, self._texts
        return Outgoing(
            to=ADMINS,
            text=self._t(
                lang,
                'admin_cancelled',
                id=booking.id,
                actor=self._t(lang, 'actor_admin' if by_admin else 'actor_customer'),
                service=texts.service_name(booking.service_id, lang),
                when=texts.when(booking.start, lang),
                staff=texts.staff_name(booking.staff_id),
                name=booking.name,
                phone=booking.phone,
            ),
        )

    def admin_rescheduled(self, old: Booking, new: Booking, *, by_admin: bool) -> Outgoing:
        """Уведомление владельцу о переносе."""
        lang, texts = self._config.owner_language, self._texts
        return Outgoing(
            to=ADMINS,
            text=self._t(
                lang,
                'admin_rescheduled',
                id=new.id,
                actor=self._t(lang, 'actor_admin' if by_admin else 'actor_customer'),
                old=texts.when(old.start, lang),
                new=texts.when(new.start, lang),
                service=texts.service_name(new.service_id, lang),
                staff=texts.staff_name(new.staff_id),
                name=new.name,
                phone=new.phone,
            ),
        )

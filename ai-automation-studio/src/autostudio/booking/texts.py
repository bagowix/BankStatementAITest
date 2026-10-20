"""Все тексты бота на английском и русском; конфиг клиента может переопределить любой.

Тексты для покупателя короткие и вежливые: человек пишет с телефона и хочет
записаться за минуту. Тексты для владельца (уведомления, расписание) — на языке
клиента из `[client].language`.

Шаблоны — `str.format`: подстановки проверяются при загрузке конфига, чтобы
переопределение с опечаткой (`{nmae}`) остановило `check`, а не бота посреди записи.
"""

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from autostudio.booking.config import SUPPORTED_LANGUAGES, BookingSection, pick_text, placeholders
from autostudio.errors import ConfigError

_EN: Final[dict[str, str]] = {
    # Покупатель: меню и выбор
    'greeting': (
        "Hi{name}! I'm the booking assistant of {business}. I can book you in, show services and prices, "
        'or answer a question — tap a button or just type your message.'
    ),
    'menu_prompt': 'What would you like to do?',
    'btn_book': '📅 Book a visit',
    'btn_my': '🗂 My bookings',
    'btn_prices': '💈 Services & prices',
    'btn_ask': '❓ Ask a question',
    'btn_human': '🙋 Talk to a person',
    'btn_menu': '⬅️ Menu',
    'btn_language': '🌐 {name}',
    'language_name': 'English',
    'choose_service': 'Which service would you like?',
    'service_option': '{name} · {price}',
    'choose_staff': '{service}: who would you like to see?',
    'btn_any_staff': 'Anyone available',
    'choose_day': '{service}: pick a day.',
    'no_days': 'Sorry, there are no free times for {service} in the next {days}.',
    'call_us': 'You can also call us: {phone}',
    'choose_time': '{service}, {day}: pick a time.',
    'btn_other_day': '📅 Another day',
    'btn_later': 'Later ➡️',
    'day_today': 'Today',
    'day_tomorrow': 'Tomorrow',
    # Покупатель: контакты и подтверждение
    'ask_name': '{slot} — great choice. What name should we put the booking under?',
    'btn_use_name': 'Use “{name}”',
    'bad_name': 'Please send just your name, for example: Anna.',
    'ask_phone': "Thanks, {name}! What's your phone number? Tap the button below or type it.",
    'btn_share_phone': '📱 Share my number',
    'bad_phone': "That doesn't look like a phone number. Example: +995 555 12 34 56",
    'got_contact': 'Got it ✅',
    'confirm': 'Please check your booking:\n• {service} — {price}\n• {when}\n• With {staff}\n• {name}, {phone}',
    'btn_confirm': '✅ Confirm',
    'btn_edit': '✏️ Change name or phone',
    'btn_abort': '✖️ Cancel',
    'booked': (
        "You're booked! ✅\n{service}, {when}, with {staff}.\nBooking #{id}. We'll send you a reminder. See you soon!"
    ),
    'already_booked': "You're already booked for this time: #{id}, {when}.",
    'slot_taken': 'Sorry, that time has just been taken. Nearest free times:',
    'slot_taken_none': "Sorry, that time is no longer available, and there's nothing else free soon.",
    'stale': "That button is out of date — here's the menu.",
    'aborted': 'OK, cancelled.',
    'ok_short': 'OK',
    # Покупатель: мои записи, отмена, перенос
    'my_none': 'You have no upcoming bookings.',
    'my_list': 'Your upcoming bookings — tap one to cancel or reschedule:',
    'booking_line': '{when} · {service}',
    'booking_detail': 'Booking #{id}\n• {service} — {price}\n• {when}\n• With {staff}',
    'btn_cancel_booking': '❌ Cancel booking',
    'btn_reschedule': '🔁 Reschedule',
    'btn_back': '⬅️ Back',
    'confirm_cancel': 'Cancel booking #{id} on {when}?',
    'btn_yes_cancel': 'Yes, cancel it',
    'btn_keep': 'No, keep it',
    'cancelled': 'Booking #{id} is cancelled. Hope to see you another time!',
    'too_late': "Your visit is less than {limit} away, so it can't be changed here. Please contact us directly.",
    'not_active': 'Booking #{id} is no longer active.',
    'reschedule_day': 'Moving booking #{id} ({when}). Pick a new day:',
    'reschedule_time': 'Moving booking #{id}. {day}: pick a new time.',
    'reschedule_confirm': 'Move booking #{id} from {old} to {new}?',
    'btn_yes_move': '✅ Yes, move it',
    'rescheduled': 'Done! Booking #{id} is now {when}, with {staff}.',
    'reminder': '⏰ Reminder: {service} {when}, with {staff}. Booking #{id}.\nSee you at {business}!',
    'btn_manage': 'Change or cancel',
    # Покупатель: вопросы и человек
    'ask_question': 'Sure — type your question.',
    'handoff': "Good question! I've passed it to our team — a person will reply here soon.",
    'human_prompt': "Write your message and I'll pass it to our team.",
    'human_sent': 'Thanks! Your message has been passed on — a person will reply here soon.',
    'not_understood': "Sorry, I didn't quite get that. Please use the buttons below.",
    'prices_title': 'Our services:',
    'price_line': '• {name} — {price} ({duration})',
    'ai_slots': '{service}: free times that suit you:',
    'ai_nearest': '{service}: nothing free at that time. Nearest options:',
    'ai_beyond_horizon': 'We take bookings up to {days} ahead.',
    'ai_past_date': 'That date has already passed.',
    'ai_my_bookings': 'Here are your bookings. Nothing changes until you tap a button.',
    'error': 'Sorry, something went wrong on our side. Please try again in a minute.',
    'cancelled_by_business': (
        'Sorry — {business} had to cancel your booking #{id} ({service}, {when}). Tap below to pick another time.'
    ),
    'btn_book_again': '📅 Pick another time',
    'admin_message': '💬 {business}: {text}',
    'cmd_start': 'Main menu',
    'cmd_book': 'Book a visit',
    'cmd_my': 'My bookings',
    'cmd_cancel': 'Cancel the current step',
    # Владелец и сотрудники
    'admin_new': '🆕 New booking #{id}\n{service} — {when}\nWith {staff}\nCustomer: {name}, {phone}\nVia: {source}',
    'admin_cancelled': '❌ Booking #{id} cancelled by {actor}\n{service} — {when}, {staff}\nCustomer: {name}, {phone}',
    'admin_rescheduled': (
        '🔁 Booking #{id} moved by {actor}\n{old} → {new}\n{service}, {staff}\nCustomer: {name}, {phone}'
    ),
    'admin_question': "❓ A question the bot couldn't answer\nFrom: {customer}\n«{text}»\nReply: /reply {user} <text>",
    'admin_human': '🙋 {customer} wants to talk to a person:\n«{text}»\nReply: /reply {user} <text>',
    'admin_agenda_day': '📋 {day}',
    'admin_agenda_empty': 'No bookings.',
    'admin_agenda_line': '{start}–{end} {staff} · {service} · {name}, {phone} · #{id}',
    'admin_cancel_confirm': (
        'Cancel booking #{id}?\n{service} — {when}, {staff}\nCustomer: {name}, {phone}\nThe customer will be notified.'
    ),
    'btn_admin_cancel': 'Yes, cancel #{id}',
    'admin_cancel_done': 'Booking #{id} cancelled; the customer has been notified.',
    'admin_not_found': 'Booking #{id} not found or already cancelled.',
    'admin_help': (
        'Admin commands:\n/today, /tomorrow, /week — schedule\n'
        '/cancel_booking <id> — cancel a booking (the customer is notified)\n/reply <user> <text> — reply to a customer'
    ),
    'admin_reply_sent': 'Sent to {user}.',
    'admin_usage_cancel': 'Usage: /cancel_booking <id>, e.g. /cancel_booking K7Q2MX',
    'admin_usage_reply': 'Usage: /reply <user> <text>',
    'actor_customer': 'the customer',
    'actor_admin': 'staff',
    'source_buttons': 'buttons',
    'source_ai': 'free-text message (AI)',
    'action_created': 'created',
    'action_cancelled': 'cancelled',
    'action_rescheduled': 'rescheduled',
}

_RU: Final[dict[str, str]] = {
    'greeting': (
        'Здравствуйте{name}! Я помощник {business} по записи. Могу записать вас, показать услуги и цены '
        'или ответить на вопрос — нажмите кнопку или просто напишите.'
    ),
    'menu_prompt': 'Чем могу помочь?',
    'btn_book': '📅 Записаться',
    'btn_my': '🗂 Мои записи',
    'btn_prices': '💈 Услуги и цены',
    'btn_ask': '❓ Задать вопрос',
    'btn_human': '🙋 Связаться с администратором',
    'btn_menu': '⬅️ Меню',
    'btn_language': '🌐 {name}',
    'language_name': 'Русский',
    'choose_service': 'На какую услугу записать?',
    'service_option': '{name} · {price}',
    'choose_staff': '{service}: к кому записать?',
    'btn_any_staff': 'К любому свободному',
    'choose_day': '{service}: выберите день.',
    'no_days': 'Увы, на «{service}» нет свободного времени в ближайшие {days}.',
    'call_us': 'Можно также позвонить: {phone}',
    'choose_time': '{service}, {day}: выберите время.',
    'btn_other_day': '📅 Другой день',
    'btn_later': 'Позже ➡️',
    'day_today': 'Сегодня',
    'day_tomorrow': 'Завтра',
    'ask_name': '{slot} — отличный выбор. На какое имя записать?',
    'btn_use_name': 'Записать как «{name}»',
    'bad_name': 'Пожалуйста, напишите только имя, например: Анна.',
    'ask_phone': 'Спасибо, {name}! Ваш номер телефона? Нажмите кнопку ниже или напишите его.',
    'btn_share_phone': '📱 Отправить мой номер',
    'bad_phone': 'Не похоже на номер телефона. Пример: +995 555 12 34 56',
    'got_contact': 'Принято ✅',
    'confirm': 'Проверьте, пожалуйста:\n• {service} — {price}\n• {when}\n• Специалист: {staff}\n• {name}, {phone}',
    'btn_confirm': '✅ Подтвердить',
    'btn_edit': '✏️ Изменить имя или телефон',
    'btn_abort': '✖️ Отмена',
    'booked': (
        'Вы записаны! ✅\n{service}, {when}, специалист: {staff}.\nЗапись №{id}. Мы пришлём напоминание. До встречи!'
    ),
    'already_booked': 'Вы уже записаны на это время: №{id}, {when}.',
    'slot_taken': 'Увы, это время только что заняли. Ближайшее свободное:',
    'slot_taken_none': 'Увы, это время уже занято, а другого свободного в ближайшие дни нет.',
    'stale': 'Эта кнопка устарела — вот меню.',
    'aborted': 'Хорошо, отменили.',
    'ok_short': 'Хорошо',
    'my_none': 'У вас нет предстоящих записей.',
    'my_list': 'Ваши предстоящие записи — нажмите, чтобы отменить или перенести:',
    'booking_line': '{when} · {service}',
    'booking_detail': 'Запись №{id}\n• {service} — {price}\n• {when}\n• Специалист: {staff}',
    'btn_cancel_booking': '❌ Отменить запись',
    'btn_reschedule': '🔁 Перенести',
    'btn_back': '⬅️ Назад',
    'confirm_cancel': 'Отменить запись №{id} на {when}?',
    'btn_yes_cancel': 'Да, отменить',
    'btn_keep': 'Нет, оставить',
    'cancelled': 'Запись №{id} отменена. Будем рады видеть вас в другой раз!',
    'too_late': 'До визита меньше {limit}, поэтому изменить запись здесь уже нельзя. Пожалуйста, свяжитесь с нами.',
    'not_active': 'Запись №{id} уже не активна.',
    'reschedule_day': 'Переносим запись №{id} ({when}). Выберите новый день:',
    'reschedule_time': 'Переносим запись №{id}. {day}: выберите новое время.',
    'reschedule_confirm': 'Перенести запись №{id} с {old} на {new}?',
    'btn_yes_move': '✅ Да, перенести',
    'rescheduled': 'Готово! Запись №{id} перенесена на {when}, специалист: {staff}.',
    'reminder': '⏰ Напоминаем: {service} {when}, специалист: {staff}. Запись №{id}.\nЖдём вас в {business}!',
    'btn_manage': 'Изменить или отменить',
    'ask_question': 'Конечно — напишите ваш вопрос.',
    'handoff': 'Хороший вопрос! Передаём его администратору — вам ответят здесь в ближайшее время.',
    'human_prompt': 'Напишите сообщение — мы передадим его администратору.',
    'human_sent': 'Спасибо! Сообщение передано — вам ответят здесь в ближайшее время.',
    'not_understood': 'Извините, не удалось разобрать сообщение. Воспользуйтесь, пожалуйста, кнопками ниже.',
    'prices_title': 'Наши услуги:',
    'price_line': '• {name} — {price} ({duration})',
    'ai_slots': '{service}: подходящее свободное время:',
    'ai_nearest': '{service}: на это время мест нет. Ближайшие варианты:',
    'ai_beyond_horizon': 'Запись открыта на {days} вперёд.',
    'ai_past_date': 'Эта дата уже прошла.',
    'ai_my_bookings': 'Вот ваши записи. Ничего не изменится, пока вы не нажмёте кнопку.',
    'error': 'Извините, у нас что-то пошло не так. Попробуйте ещё раз через минуту.',
    'cancelled_by_business': (
        'Извините — {business} пришлось отменить вашу запись №{id} ({service}, {when}). '
        'Нажмите ниже, чтобы выбрать другое время.'
    ),
    'btn_book_again': '📅 Выбрать другое время',
    'admin_message': '💬 {business}: {text}',
    'cmd_start': 'Главное меню',
    'cmd_book': 'Записаться',
    'cmd_my': 'Мои записи',
    'cmd_cancel': 'Отменить текущий шаг',
    'admin_new': (
        '🆕 Новая запись №{id}\n{service} — {when}\nСпециалист: {staff}\nКлиент: {name}, {phone}\nКанал: {source}'
    ),
    'admin_cancelled': '❌ Запись №{id} отменена ({actor})\n{service} — {when}, {staff}\nКлиент: {name}, {phone}',
    'admin_rescheduled': (
        '🔁 Запись №{id} перенесена ({actor})\n{old} → {new}\n{service}, {staff}\nКлиент: {name}, {phone}'
    ),
    'admin_question': '❓ Вопрос, на который бот не ответил\nОт: {customer}\n«{text}»\nОтветить: /reply {user} <текст>',
    'admin_human': '🙋 {customer} просит связаться с человеком:\n«{text}»\nОтветить: /reply {user} <текст>',
    'admin_agenda_day': '📋 {day}',
    'admin_agenda_empty': 'Записей нет.',
    'admin_agenda_line': '{start}–{end} {staff} · {service} · {name}, {phone} · №{id}',
    'admin_cancel_confirm': (
        'Отменить запись №{id}?\n{service} — {when}, {staff}\nКлиент: {name}, {phone}\nКлиент получит уведомление.'
    ),
    'btn_admin_cancel': 'Да, отменить №{id}',
    'admin_cancel_done': 'Запись №{id} отменена, клиент уведомлён.',
    'admin_not_found': 'Запись №{id} не найдена или уже отменена.',
    'admin_help': (
        'Команды администратора:\n/today, /tomorrow, /week — расписание\n'
        '/cancel_booking <id> — отменить запись (клиент получит уведомление)\n/reply <user> <текст> — ответить клиенту'
    ),
    'admin_reply_sent': 'Отправлено: {user}.',
    'admin_usage_cancel': 'Формат: /cancel_booking <id>, например /cancel_booking K7Q2MX',
    'admin_usage_reply': 'Формат: /reply <user> <текст>',
    'actor_customer': 'клиентом',
    'actor_admin': 'администратором',
    'source_buttons': 'кнопки',
    'source_ai': 'сообщение в свободной форме (ИИ)',
    'action_created': 'создана',
    'action_cancelled': 'отменена',
    'action_rescheduled': 'перенесена',
}

DEFAULT_TEXTS: Final[Mapping[str, Mapping[str, str]]] = {'en': _EN, 'ru': _RU}

# Заголовки вкладок таблицы владельца: на языке владельца, не переопределяются.
BOOKINGS_HEADER: Final[Mapping[str, list[str]]] = {
    'en': [
        'Logged at',
        'Action',
        'Booking',
        'Date',
        'Time',
        'Service',
        'Staff',
        'Customer',
        'Phone',
        'Price',
        'Source',
    ],
    'ru': [
        'Когда',
        'Действие',
        'Запись',
        'Дата',
        'Время',
        'Услуга',
        'Специалист',
        'Клиент',
        'Телефон',
        'Цена',
        'Канал',
    ],
}
SCHEDULE_HEADER: Final[Mapping[str, list[str]]] = {
    'en': ['Date', 'Start', 'End', 'Staff', 'Service', 'Customer', 'Phone', 'Booking'],
    'ru': ['Дата', 'Начало', 'Конец', 'Специалист', 'Услуга', 'Клиент', 'Телефон', 'Запись'],
}

_WEEKDAYS_SHORT: Final[Mapping[str, tuple[str, ...]]] = {
    'en': ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'),
    'ru': ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'),
}
WEEKDAYS_FULL: Final[Mapping[str, tuple[str, ...]]] = {
    'en': ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'),
    'ru': ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'),
}
_MONTHS: Final[Mapping[str, tuple[str, ...]]] = {
    'en': ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'),
    'ru': ('янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'),
}


def plural_ru(count: int, forms: tuple[str, str, str]) -> str:
    """Русская форма слова после числа: 1 день, 2 дня, 5 дней, 11 дней, 21 день."""
    tail = count % 100
    if 11 <= tail <= 14:
        return forms[2]
    if count % 10 == 1:
        return forms[0]
    if 2 <= count % 10 <= 4:
        return forms[1]
    return forms[2]


def validate_overrides(overrides: Mapping[str, Mapping[str, str]]) -> None:
    """Проверяет переопределения текстов из конфига.

    Args:
        overrides: `{язык: {ключ: шаблон}}`. Язык может не входить в языки покупателей:
            тексты владельца (уведомления) идут на языке `[client].language`.

    Raises:
        ConfigError: Неизвестный язык или ключ, битый шаблон или чужая подстановка.
    """
    for language, table in overrides.items():
        if language not in SUPPORTED_LANGUAGES:
            raise ConfigError(f'booking.texts.{language}: язык не поддерживается')
        defaults = DEFAULT_TEXTS[language]
        for key, template in table.items():
            if key not in defaults:
                raise ConfigError(f'booking.texts.{language}.{key}: нет такого текста')
            try:
                extra = placeholders(template) - placeholders(defaults[key])
            except ValueError as exc:
                raise ConfigError(f'booking.texts.{language}.{key}: битый шаблон: {exc}') from None
            if extra:
                allowed = ', '.join(sorted(placeholders(defaults[key]))) or 'без подстановок'
                raise ConfigError(
                    f'booking.texts.{language}.{key}: подстановка {", ".join(sorted(extra))} недоступна ({allowed})'
                )


class Texts:
    """Тексты и форматирование дат, денег и длительностей на языке получателя.

    Args:
        section: Секция `[booking]` (языки и переопределения).
        zone: Часовой пояс клиента: всё время показывается в нём.
    """

    def __init__(self, section: BookingSection, zone: ZoneInfo) -> None:
        self._section = section
        self._zone = zone
        self._tables: dict[str, dict[str, str]] = {
            language: {**DEFAULT_TEXTS[language], **section.texts.get(language, {})} for language in SUPPORTED_LANGUAGES
        }

    @property
    def zone(self) -> ZoneInfo:
        return self._zone

    def get(self, language: str, key: str, **values: str | int) -> str:
        """Текст по ключу с подстановками.

        Args:
            language: Язык получателя (неизвестный — английский).
            key: Ключ текста.
            **values: Подстановки шаблона.

        Returns:
            Готовый текст.
        """
        table = self._tables.get(language) or self._tables['en']
        return table[key].format(**values)

    def resolve_language(self, code: str | None) -> str:
        """Язык покупателя по коду клиента Telegram (`ru`, `en-US`); иначе язык по умолчанию."""
        if code:
            short = code.split('-', 1)[0].lower()
            if short in self._section.languages:
                return short
        return self._section.default_language

    def pick(self, texts: Mapping[str, str], language: str) -> str:
        """Перевод из конфига (название услуги, ответ FAQ) на языке покупателя."""
        return pick_text(texts, language, self._section.default_language)

    def service_name(self, service_id: str, language: str) -> str:
        """Название услуги или её id, если услугу уже убрали из конфига."""
        service = self._section.service(service_id)
        return service_id if service is None else self.pick(service.name, language)

    def staff_name(self, staff_id: str) -> str:
        """Имя мастера или его id, если мастера уже убрали из конфига."""
        member = self._section.staff_member(staff_id)
        return staff_id if member is None else member.name

    @staticmethod
    def money(amount: Decimal, currency: str) -> str:
        """`35 GEL`, `35.50 GEL`: копейки показываются, только если они есть."""
        text = f'{amount:.2f}'
        return f'{text.removesuffix(".00")} {currency}'

    @staticmethod
    def duration(minutes: int, language: str) -> str:
        """`45 min`, `1 h 30 min` / `45 мин`, `1 ч 30 мин`."""
        hour, minute = ('ч', 'мин') if language == 'ru' else ('h', 'min')
        hours, rest = divmod(minutes, 60)
        parts = ([f'{hours} {hour}'] if hours else []) + ([f'{rest} {minute}'] if rest or not hours else [])
        return ' '.join(parts)

    @staticmethod
    def days(count: int, language: str) -> str:
        """`14 days` / `14 дней`."""
        if language == 'ru':
            return f'{count} {plural_ru(count, ("день", "дня", "дней"))}'
        return f'{count} day' if count == 1 else f'{count} days'

    def local(self, moment: dt.datetime) -> dt.datetime:
        """Момент в часовом поясе клиента."""
        return moment.astimezone(self._zone)

    def date(self, day: dt.date, language: str) -> str:
        """`Wed 21 Oct` / `ср, 21 окт`."""
        lang = language if language in _WEEKDAYS_SHORT else 'en'
        weekday = _WEEKDAYS_SHORT[lang][day.weekday()]
        month = _MONTHS[lang][day.month - 1]
        return f'{weekday}, {day.day} {month}' if lang == 'ru' else f'{weekday} {day.day} {month}'

    def day_label(self, day: dt.date, language: str, today: dt.date) -> str:
        """`Today` / `Tomorrow` / `Wed 21 Oct` — подпись кнопки дня."""
        if day == today:
            return self.get(language, 'day_today')
        if day == today + dt.timedelta(days=1):
            return self.get(language, 'day_tomorrow')
        return self.date(day, language)

    def time(self, moment: dt.datetime) -> str:
        """Местное время `17:00`."""
        return f'{self.local(moment):%H:%M}'

    def when(self, moment: dt.datetime, language: str) -> str:
        """`Wed 21 Oct, 17:00` / `ср, 21 окт, 17:00`."""
        local = self.local(moment)
        return f'{self.date(local.date(), language)}, {local:%H:%M}'

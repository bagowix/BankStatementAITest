"""Конфиг клиента пакета booking: услуги, мастера, часы работы, FAQ и тексты.

Всё, что отличает одну парикмахерскую от другой, живёт здесь, а не в коде. Конфиг
проверяется целиком при загрузке: опечатка в часах работы или ссылка на
несуществующего мастера должны остановить `check`, а не всплыть у покупателя в чате.

Решения, которые фиксирует схема:

- Длительность услуги не обязана быть кратной шагу сетки: начала слотов выровнены по
  сетке от полуночи (`slot_step_min`), а конец — это начало плюс длительность.
- Часы мастера (`staff.hours`) пересекаются с часами бизнеса: мастер не принимает,
  когда салон закрыт, а день, не указанный у мастера, — его выходной.
- Соседние интервалы одного дня (`10:00-14:00`, `14:00-18:00`) склеиваются, а
  пересекающиеся — ошибка: это почти всегда опечатка.
"""

import datetime as dt
import functools
import re
import string
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

import msgspec
import phonenumbers

from autostudio.clientconf import ClientInfo, NotifyConfig, SinkConfig, load_client_config
from autostudio.errors import ConfigError

# Языки, на которых у пакета есть полный набор текстов для покупателей (см. texts.py).
SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ('en', 'ru')
WEEKDAYS: Final[tuple[str, ...]] = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
MINUTES_PER_DAY: Final = 24 * 60
MAX_DURATION_MIN: Final = 12 * 60
MAX_HORIZON_DAYS: Final = 90
MAX_ACTIVE_PER_CUSTOMER: Final = 50

# Длины id ограничены ради лимита Telegram на callback_data (64 байта): в кнопке
# выбора времени лежат id услуги, id мастера и момент начала.
SERVICE_ID_MAX: Final = 24
STAFF_ID_MAX: Final = 16
_SERVICE_ID: Final = re.compile(rf'^[a-z0-9][a-z0-9-]{{0,{SERVICE_ID_MAX - 1}}}$')
_STAFF_ID: Final = re.compile(rf'^[a-z0-9][a-z0-9-]{{0,{STAFF_ID_MAX - 1}}}$')
_FAQ_ID: Final = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
_INTERVAL: Final = re.compile(r'^(\d{2}):(\d{2})-(\d{2}):(\d{2})$')
_CURRENCY: Final = re.compile(r'^[A-Z]{3}$')
_ENV_NAME: Final = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
# Имя шаблона WhatsApp — строчные латинские буквы, цифры и подчёркивание (так их создаёт WhatsApp Manager).
_TEMPLATE_NAME: Final = re.compile(r'^[a-z0-9_]{1,512}$')
# Код языка шаблона: `en`, `ru`, `en_US`, `pt_BR`.
_TEMPLATE_LANGUAGE: Final = re.compile(r'^[a-z]{2,3}(_[A-Z]{2})?$')
_GRAPH_VERSION: Final = re.compile(r'^v\d{1,3}\.\d$')
_PHONE_NUMBER_ID: Final = re.compile(r'^\d{5,32}$')
# Календарь Google: e-mail владельца или `...@group.calendar.google.com`; пробелов и управляющих
# символов в id не бывает, а слэш сломал бы путь REST-запроса.
_CALENDAR_ID: Final = re.compile(r'^[^\s/]{3,256}$')
CALENDAR_TTL_RANGE: Final = (10, 900)
DEFAULT_GRAPH_VERSION: Final = 'v25.0'

type Interval = tuple[int, int]
"""Интервал работы в минутах от локальной полуночи: [начало, конец)."""


def pick_text(texts: Mapping[str, str], language: str, fallback: str) -> str:
    """Текст на языке покупателя, иначе на языке по умолчанию, иначе любой.

    Args:
        texts: Переводы `{язык: текст}`.
        language: Желаемый язык.
        fallback: Язык по умолчанию клиента.

    Returns:
        Лучший доступный перевод (пустая строка, если переводов нет).
    """
    return texts.get(language) or texts.get(fallback) or next(iter(texts.values()), '')


@functools.lru_cache(maxsize=512)
def parse_intervals(raw: tuple[str, ...]) -> tuple[Interval, ...]:
    """Разбирает интервалы одного дня `HH:MM-HH:MM`.

    Args:
        raw: Строки интервалов, как в конфиге.

    Returns:
        Отсортированные интервалы в минутах; соседние склеены.

    Raises:
        ConfigError: Неверный формат, начало не раньше конца или интервалы пересекаются.
    """
    parsed: list[Interval] = []
    for item in raw:
        match = _INTERVAL.match(item.strip())
        if match is None:
            raise ConfigError(f'интервал {item!r}: ожидается HH:MM-HH:MM, например 10:00-19:00')
        h1, m1, h2, m2 = (int(part) for part in match.groups())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if h1 > 23 or m1 > 59 or m2 > 59 or end > MINUTES_PER_DAY:
            raise ConfigError(f'интервал {item!r}: неверное время (конец дня — 24:00)')
        if start >= end:
            raise ConfigError(f'интервал {item!r}: начало должно быть раньше конца (через полночь нельзя)')
        parsed.append((start, end))
    parsed.sort()
    merged: list[Interval] = []
    for start, end in parsed:
        if merged and start < merged[-1][1]:
            raise ConfigError(f'интервалы {", ".join(raw)} пересекаются')
        if merged and start == merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return tuple(merged)


class WeeklyHours(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Часы работы по дням недели; пустой список — выходной.

    Attributes:
        mon: Интервалы понедельника, например `["10:00-14:00", "15:00-20:00"]`.
        tue: Вторник.
        wed: Среда.
        thu: Четверг.
        fri: Пятница.
        sat: Суббота.
        sun: Воскресенье.
    """

    mon: list[str] = []
    tue: list[str] = []
    wed: list[str] = []
    thu: list[str] = []
    fri: list[str] = []
    sat: list[str] = []
    sun: list[str] = []

    def __post_init__(self) -> None:
        for name, raw in zip(WEEKDAYS, self._days(), strict=True):
            try:
                parse_intervals(tuple(raw))
            except ConfigError as exc:
                raise ConfigError(f'{name}: {exc}') from None

    def _days(self) -> tuple[list[str], ...]:
        return (self.mon, self.tue, self.wed, self.thu, self.fri, self.sat, self.sun)

    def for_weekday(self, weekday: int) -> tuple[Interval, ...]:
        """Интервалы дня недели (0 — понедельник), как `date.weekday()`."""
        return parse_intervals(tuple(self._days()[weekday]))

    @property
    def is_empty(self) -> bool:
        return not any(self._days())


class Service(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Услуга, на которую можно записаться.

    Attributes:
        id: Короткий slug (`haircut`), до 24 символов: он едет в кнопках Telegram.
        name: Название по языкам: `{en = "Haircut", ru = "Стрижка"}`.
        duration_min: Длительность в минутах (не обязана быть кратной шагу сетки).
        price: Цена; в TOML — строка `"35.50"` или число, внутри всегда `Decimal`.
        currency: Код валюты ISO 4217.
        staff_ids: Кто оказывает услугу; пусто — любой мастер.
    """

    id: str
    name: dict[str, str]
    duration_min: int
    price: Decimal
    currency: str = 'GEL'
    staff_ids: list[str] = []

    def __post_init__(self) -> None:
        where = f'booking.services[{self.id!r}]'
        if not _SERVICE_ID.match(self.id):
            raise ConfigError(f'{where}: id — a-z, 0-9 и дефис, до {SERVICE_ID_MAX} символов')
        if not self.name or not all(value.strip() for value in self.name.values()):
            raise ConfigError(f'{where}: name — непустые названия по языкам, например {{en = "Haircut"}}')
        if not 0 < self.duration_min <= MAX_DURATION_MIN:
            raise ConfigError(f'{where}: duration_min — от 1 до {MAX_DURATION_MIN} минут')
        exponent = self.price.as_tuple().exponent
        if not self.price.is_finite() or self.price < 0 or (isinstance(exponent, int) and exponent < -2):
            raise ConfigError(f'{where}: price — неотрицательная сумма, не больше двух знаков после точки')
        if not _CURRENCY.match(self.currency):
            raise ConfigError(f'{where}: currency — код из трёх заглавных букв, например GEL')


class Staff(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Мастер (специалист, кабинет, подъёмник) — то, что нельзя занять дважды.

    Attributes:
        id: Короткий slug (`giorgi`), до 16 символов.
        name: Имя, как его видят покупатели.
        hours: Собственные часы; пересекаются с часами бизнеса. None — работает все часы бизнеса.
    """

    id: str
    name: str
    hours: WeeklyHours | None = None

    def __post_init__(self) -> None:
        if not _STAFF_ID.match(self.id):
            raise ConfigError(f'booking.staff[{self.id!r}]: id — a-z, 0-9 и дефис, до {STAFF_ID_MAX} символов')
        if not self.name.strip():
            raise ConfigError(f'booking.staff[{self.id!r}]: пустое имя')


class FaqItem(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Вопрос и ответ, на которые бот отвечает сам.

    Attributes:
        id: Короткий slug (`parking`): модель ссылается на него, код проверяет ссылку.
        question: Вопрос по языкам.
        answer: Ответ по языкам — единственный источник фактов для модели.
    """

    id: str
    question: dict[str, str]
    answer: dict[str, str]

    def __post_init__(self) -> None:
        if not _FAQ_ID.match(self.id):
            raise ConfigError(f'booking.faq[{self.id!r}]: id — a-z, 0-9 и дефис, до 32 символов')
        if not self.question or not self.answer:
            raise ConfigError(f'booking.faq[{self.id!r}]: нужны question и answer хотя бы на одном языке')


def _require_env_name(where: str, value: str) -> None:
    if not _ENV_NAME.match(value):
        raise ConfigError(f'{where}={value!r}: ожидается имя переменной окружения, а не её значение')


class CalendarSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[booking.calendar]`: занятость и события в Google Calendar клиента.

    Клиент «Поделиться» своим календарём с e-mail сервисного аккаунта с правом «Изменять
    события». Бот читает занятость (freeBusy) и пишет события записей без гостей.

    Attributes:
        credentials_env: Переменная с путём к JSON-ключу сервисного аккаунта (тот же ключ,
            что у Google Sheets, если проект один).
        calendar_id: Календарь бизнеса: его занятость закрывает время всем мастерам, у
            которых нет своего календаря в `staff`, и туда пишутся их записи.
        staff: Календари мастеров `{id мастера = "id календаря"}`.
        cache_ttl_s: Сколько секунд помнить занятость дня календаря (квоты Google и скорость ответа).
        write_events: Писать ли записи событиями в календарь (False — только читать занятость).
    """

    credentials_env: str = 'GOOGLE_APPLICATION_CREDENTIALS'
    calendar_id: str | None = None
    staff: dict[str, str] = {}
    cache_ttl_s: int = 60
    write_events: bool = True

    def __post_init__(self) -> None:
        _require_env_name('booking.calendar.credentials_env', self.credentials_env)
        if self.calendar_id is None and not self.staff:
            raise ConfigError('booking.calendar: нужен calendar_id бизнеса или календари мастеров в staff')
        for calendar in [self.calendar_id, *self.staff.values()]:
            if calendar is not None and not _CALENDAR_ID.match(calendar):
                raise ConfigError(f'booking.calendar: {calendar!r} не похоже на id календаря Google')
        low, high = CALENDAR_TTL_RANGE
        if not low <= self.cache_ttl_s <= high:
            raise ConfigError(f'booking.calendar.cache_ttl_s: от {low} до {high} секунд')

    def calendar_for(self, staff_id: str) -> str | None:
        """Календарь мастера: свой из `staff`, иначе календарь бизнеса, иначе None."""
        return self.staff.get(staff_id) or self.calendar_id

    @property
    def calendars(self) -> list[str]:
        """Все календари без повторов, в порядке конфига."""
        ordered = [*self.staff.values(), *([self.calendar_id] if self.calendar_id else [])]
        return list(dict.fromkeys(ordered))


class WhatsAppSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[booking.whatsapp]`: второй канал записи через WhatsApp Cloud API.

    Номер и WABA принадлежат клиенту; оператор — администратор или system user в его
    Business portfolio. Секретов здесь нет — только имена переменных окружения.

    Attributes:
        phone_number_id: ID номера из WhatsApp Manager (не сам номер телефона).
        graph_version: Версия Graph API, например `v25.0`.
        token_env: Переменная с токеном system user клиента.
        app_secret_env: Переменная с App Secret приложения Meta (подпись вебхуков).
        verify_token_env: Переменная со строкой проверки вебхука (её же вводят в кабинете Meta).
        reminder_template: Одобренный шаблон напоминания (категория Utility) с параметрами
            `{{1}}` — услуга, `{{2}}` — время; None — напоминания только внутри 24-часового окна.
        template_languages: Язык покупателя → код языка шаблона (`{en = "en_US"}`); без
            записи — сам код языка покупателя.
    """

    phone_number_id: str
    graph_version: str = DEFAULT_GRAPH_VERSION
    token_env: str = 'WHATSAPP_TOKEN'  # noqa: S105 - имя переменной, а не секрет
    app_secret_env: str = 'WHATSAPP_APP_SECRET'  # noqa: S105 - имя переменной, а не секрет
    verify_token_env: str = 'WHATSAPP_VERIFY_TOKEN'  # noqa: S105 - имя переменной, а не секрет
    reminder_template: str | None = None
    template_languages: dict[str, str] = {}

    def __post_init__(self) -> None:
        where = 'booking.whatsapp'
        if not _PHONE_NUMBER_ID.match(self.phone_number_id):
            raise ConfigError(f'{where}.phone_number_id: числовой ID номера из WhatsApp Manager, не сам номер')
        if not _GRAPH_VERSION.match(self.graph_version):
            raise ConfigError(f'{where}.graph_version={self.graph_version!r}: ожидается вида {DEFAULT_GRAPH_VERSION}')
        for name, value in (
            ('token_env', self.token_env),
            ('app_secret_env', self.app_secret_env),
            ('verify_token_env', self.verify_token_env),
        ):
            _require_env_name(f'{where}.{name}', value)
        if self.reminder_template is not None and not _TEMPLATE_NAME.match(self.reminder_template):
            raise ConfigError(f'{where}.reminder_template: имя шаблона — a-z, 0-9 и подчёркивание')
        for language, code in self.template_languages.items():
            if language not in SUPPORTED_LANGUAGES or not _TEMPLATE_LANGUAGE.match(code):
                raise ConfigError(f'{where}.template_languages: {language} = {code!r} — ожидается en = "en_US"')

    def template_language(self, language: str) -> str:
        """Код языка шаблона для языка покупателя."""
        return self.template_languages.get(language, language)


class TelegramSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[booking.telegram]`: как бот получает апдейты.

    Attributes:
        mode: `polling` (по умолчанию: не нужен ни домен, ни HTTPS) или `webhook` (нужен
            `[booking.server] public_url` за HTTPS-прокси).
        webhook_secret_env: Переменная с секретом вебхука: Telegram присылает его в заголовке
            `X-Telegram-Bot-Api-Secret-Token`, чужие запросы отбрасываются.
    """

    mode: Literal['polling', 'webhook'] = 'polling'
    webhook_secret_env: str = 'BOOKING_TELEGRAM_WEBHOOK_SECRET'  # noqa: S105 - имя переменной, а не секрет

    def __post_init__(self) -> None:
        _require_env_name('booking.telegram.webhook_secret_env', self.webhook_secret_env)


class ServerSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[booking.server]`: HTTP-сервер вебхуков (WhatsApp и Telegram в режиме webhook).

    Сам сервер слушает обычный HTTP; HTTPS (сертификат, порт 443) даёт обратный прокси
    (Caddy, nginx, Traefik) — и Meta, и Telegram шлют вебхуки только на HTTPS.

    Attributes:
        public_url: Внешний адрес за прокси, например `https://bot.example.ge`.
        host: Адрес, на котором слушать внутри контейнера.
        port: Порт; переменная окружения `port_env`, если задана, важнее.
        port_env: Переменная окружения с портом (платформы вроде Render задают `PORT`).
    """

    public_url: str | None = None
    host: str = '0.0.0.0'  # noqa: S104 - в контейнере слушаем все интерфейсы, наружу выпускает прокси
    port: int = 8080
    port_env: str = 'BOOKING_WEBHOOK_PORT'

    def __post_init__(self) -> None:
        if self.public_url is not None and not (
            self.public_url.startswith('https://') and len(self.public_url) > len('https://')
        ):
            raise ConfigError('booking.server.public_url: только https://… — вебхуки Meta и Telegram требуют HTTPS')
        if not 1 <= self.port <= 65535:
            raise ConfigError('booking.server.port: от 1 до 65535')
        _require_env_name('booking.server.port_env', self.port_env)

    @property
    def base_url(self) -> str | None:
        """`public_url` без завершающего слэша."""
        return self.public_url.rstrip('/') if self.public_url else None


def _default_languages() -> list[str]:
    return list(SUPPORTED_LANGUAGES)


def _default_reminders() -> list[int]:
    return [1440, 120]


class BookingSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[booking]`.

    Attributes:
        bot_token_env: Переменная окружения с токеном бота от @BotFather.
        admin_chat_ids: Telegram id владельца и сотрудников (или id их группы): уведомления и
            служебные команды `/today`, `/week`, `/cancel_booking`, `/reply`.
        languages: Языки покупателей; первый — язык по умолчанию.
        services: Услуги.
        staff: Мастера (хотя бы один: у соло-специалиста это он сам).
        hours: Часы работы бизнеса по дням недели.
        closed_dates: Праздники и выходные дни, когда запись закрыта.
        slot_step_min: Шаг сетки начала слотов; делит сутки нацело (5–120 минут).
        min_lead_min: Не раньше чем через столько минут от «сейчас».
        horizon_days: На сколько календарных дней вперёд открыта запись (сегодня — первый).
        buffer_min: Пауза между записями одного мастера (уборка, переход).
        reminders_min_before: За сколько минут до визита напоминать.
        cancellation_min_before: Позже этого покупатель не отменяет и не переносит сам (админ — может).
        faq: Вопросы и ответы.
        contact_phone: Телефон для тех, кто хочет позвонить, или None.
        phone_region: Страна (ISO 3166, `GE`, `RU`, `DE`) для номеров, набранных без кода страны.
        texts: Переопределения текстов: `{ru = {booked = "..."}}`.
        state_ttl_min: Сколько минут бот помнит незаконченный разговор.
        max_active_per_customer: Сколько предстоящих записей может держать один покупатель
            (защита от «занять все слоты» с одного аккаунта; администраторов не касается).
        calendar: Google Calendar клиента (занятость и события) или None.
        whatsapp: Второй канал — WhatsApp Cloud API — или None.
        telegram: Режим получения апдейтов Telegram.
        server: HTTP-сервер вебхуков (нужен WhatsApp и Telegram в режиме webhook).
    """

    bot_token_env: str = 'BOOKING_BOT_TOKEN'  # noqa: S105 - имя переменной, а не секрет
    admin_chat_ids: list[int] = []
    languages: list[str] = msgspec.field(default_factory=_default_languages)
    services: list[Service]
    staff: list[Staff]
    hours: WeeklyHours
    closed_dates: list[dt.date] = []
    slot_step_min: int = 15
    min_lead_min: int = 60
    horizon_days: int = 14
    buffer_min: int = 0
    reminders_min_before: list[int] = msgspec.field(default_factory=_default_reminders)
    cancellation_min_before: int = 120
    faq: list[FaqItem] = []
    contact_phone: str | None = None
    phone_region: str = 'GE'
    texts: dict[str, dict[str, str]] = {}
    state_ttl_min: int = 30
    max_active_per_customer: int = 3
    calendar: CalendarSection | None = None
    whatsapp: WhatsAppSection | None = None
    telegram: TelegramSection = TelegramSection()
    server: ServerSection = ServerSection()

    def __post_init__(self) -> None:
        self._check_languages()
        self._check_catalog()
        self._check_numbers()
        self._check_channels()
        if self.hours.is_empty:
            raise ConfigError('booking.hours: ни одного рабочего интервала — записаться будет некуда')
        if self.phone_region not in phonenumbers.SUPPORTED_REGIONS:
            raise ConfigError(f'booking.phone_region={self.phone_region!r}: ожидается код страны, например GE или RU')
        if self.contact_phone is not None and not _valid_phone(self.contact_phone, self.phone_region):
            # Этот номер бот показывает покупателю «позвоните нам»: «call me» или опечатка там
            # хуже, чем отсутствие номера.
            raise ConfigError(
                f'booking.contact_phone={self.contact_phone!r}: не номер телефона '
                f'(с кодом страны или в формате {self.phone_region})'
            )
        # Импорт здесь: texts зависит от констант этого модуля.
        from autostudio.booking.texts import validate_overrides

        validate_overrides(self.texts)

    def _check_languages(self) -> None:
        if not self.languages:
            raise ConfigError('booking.languages: нужен хотя бы один язык')
        unknown = [lang for lang in self.languages if lang not in SUPPORTED_LANGUAGES]
        if unknown:
            raise ConfigError(
                f'booking.languages: {", ".join(unknown)} не поддерживается; доступны {", ".join(SUPPORTED_LANGUAGES)}'
            )
        if len(set(self.languages)) != len(self.languages):
            raise ConfigError('booking.languages: языки повторяются')

    def _check_catalog(self) -> None:
        default = self.languages[0]
        if not self.services:
            raise ConfigError('booking.services: нужна хотя бы одна услуга')
        if not self.staff:
            raise ConfigError('booking.staff: нужен хотя бы один мастер (у соло-специалиста — он сам)')
        _require_unique('booking.services', [service.id for service in self.services])
        _require_unique('booking.staff', [member.id for member in self.staff])
        _require_unique('booking.faq', [item.id for item in self.faq])
        known_staff = {member.id for member in self.staff}
        for service in self.services:
            unknown = [staff_id for staff_id in service.staff_ids if staff_id not in known_staff]
            if unknown:
                raise ConfigError(f'booking.services[{service.id!r}]: неизвестный мастер {", ".join(unknown)}')
            if default not in service.name:
                raise ConfigError(f'booking.services[{service.id!r}]: нет названия на языке по умолчанию {default!r}')
        for item in self.faq:
            if default not in item.question or default not in item.answer:
                raise ConfigError(f'booking.faq[{item.id!r}]: нет вопроса или ответа на языке {default!r}')

    def _check_numbers(self) -> None:
        if not 5 <= self.slot_step_min <= 120 or MINUTES_PER_DAY % self.slot_step_min:
            raise ConfigError('booking.slot_step_min: 5–120 минут и делит сутки нацело (5, 10, 15, 20, 30, 60 …)')
        if not 0 <= self.min_lead_min <= 7 * MINUTES_PER_DAY:
            raise ConfigError('booking.min_lead_min: от 0 до недели')
        if not 1 <= self.horizon_days <= MAX_HORIZON_DAYS:
            raise ConfigError(f'booking.horizon_days: от 1 до {MAX_HORIZON_DAYS}')
        if not 0 <= self.buffer_min <= 240:
            raise ConfigError('booking.buffer_min: от 0 до 240 минут')
        if not 0 <= self.cancellation_min_before <= 7 * MINUTES_PER_DAY:
            raise ConfigError('booking.cancellation_min_before: от 0 до недели')
        if not 5 <= self.state_ttl_min <= MINUTES_PER_DAY:
            raise ConfigError('booking.state_ttl_min: от 5 минут до суток')
        reminders = self.reminders_min_before
        if any(not 0 < value <= 7 * MINUTES_PER_DAY for value in reminders) or len(set(reminders)) != len(reminders):
            raise ConfigError('booking.reminders_min_before: разные значения от 1 минуты до недели')
        if not 1 <= self.max_active_per_customer <= MAX_ACTIVE_PER_CUSTOMER:
            raise ConfigError(f'booking.max_active_per_customer: от 1 до {MAX_ACTIVE_PER_CUSTOMER}')

    def _check_channels(self) -> None:
        known_staff = {member.id for member in self.staff}
        if self.calendar is not None:
            unknown = sorted(set(self.calendar.staff) - known_staff)
            if unknown:
                raise ConfigError(f'booking.calendar.staff: неизвестный мастер {", ".join(unknown)}')
        if self.telegram.mode == 'webhook' and self.server.base_url is None:
            raise ConfigError(
                'booking.telegram.mode = "webhook" требует [booking.server] public_url (https://…), '
                'иначе Telegram некуда слать апдейты; без своего домена оставьте polling'
            )

    @property
    def needs_server(self) -> bool:
        """Нужен ли HTTP-сервер вебхуков (WhatsApp или Telegram в режиме webhook)."""
        return self.whatsapp is not None or self.telegram.mode == 'webhook'

    @property
    def default_language(self) -> str:
        return self.languages[0]

    def service(self, service_id: str) -> Service | None:
        """Услуга по id или None (id из кнопки или от модели может быть любым)."""
        return next((service for service in self.services if service.id == service_id), None)

    def staff_member(self, staff_id: str) -> Staff | None:
        """Мастер по id или None."""
        return next((member for member in self.staff if member.id == staff_id), None)

    def eligible_staff(self, service: Service) -> list[Staff]:
        """Мастера, которые оказывают услугу, в порядке конфига."""
        if not service.staff_ids:
            return list(self.staff)
        return [member for member in self.staff if member.id in service.staff_ids]

    def faq_item(self, faq_id: str) -> FaqItem | None:
        """Пункт FAQ по id или None."""
        return next((item for item in self.faq if item.id == faq_id), None)


def _valid_phone(text: str, region: str) -> bool:
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return False
    return phonenumbers.is_valid_number(number)


def _require_unique(where: str, ids: Sequence[str]) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            raise ConfigError(f'{where}: id {item!r} повторяется')
        seen.add(item)


class BookingConfig(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Конфиг клиента пакета booking (Package Contract).

    Attributes:
        client: Кто клиент, часовой пояс, язык отчёта и уведомлений владельцу.
        notify: Дополнительный канал уведомлений владельцу (вебхук, другой бот).
        sink: Таблица владельца: вкладки `Bookings` (журнал) и `Schedule` (расписание).
        booking: Секция пакета.
    """

    client: ClientInfo
    notify: NotifyConfig = NotifyConfig()
    sink: SinkConfig = SinkConfig()
    booking: BookingSection

    @property
    def owner_language(self) -> str:
        """Язык уведомлений и таблицы владельца: язык клиента, если тексты на нём есть."""
        return self.client.language if self.client.language in SUPPORTED_LANGUAGES else 'en'

    @property
    def has_owner_channel(self) -> bool:
        """Есть ли живой адресат у уведомлений: администраторы в боте или канал `[notify]`.

        Консоль (`[notify] console`) не в счёт: в боевом режиме её никто не читает, а бот
        обещает покупателю «вам ответят здесь» — значит, вопрос должен до кого-то дойти.
        """
        notify = self.notify
        return bool(self.booking.admin_chat_ids or notify.telegram_chat_id or notify.webhook_url_env)


def load_booking_config(path: Path) -> BookingConfig:
    """Читает и проверяет конфиг клиента пакета.

    Разбор TOML — ядра (`load_client_config`): дробные числа там сразу `Decimal`, так что
    цена `35.50` ни на одном шаге не проходит через float.

    Args:
        path: Путь к TOML-файлу.

    Returns:
        Проверенный конфиг.

    Raises:
        ConfigError: Файла нет, TOML битый или конфиг не проходит проверки.
    """
    return load_client_config(path, BookingConfig)


def placeholders(template: str) -> set[str]:
    """Имена подстановок `{name}` в шаблоне текста."""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}

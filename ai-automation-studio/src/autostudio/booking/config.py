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
import tomllib
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final

import msgspec

from autostudio.clientconf import ClientInfo, NotifyConfig, SinkConfig
from autostudio.errors import ConfigError

# Языки, на которых у пакета есть полный набор текстов для покупателей (см. texts.py).
SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ('en', 'ru')
WEEKDAYS: Final[tuple[str, ...]] = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
MINUTES_PER_DAY: Final = 24 * 60
MAX_DURATION_MIN: Final = 12 * 60
MAX_HORIZON_DAYS: Final = 90

# Длины id ограничены ради лимита Telegram на callback_data (64 байта): в кнопке
# выбора времени лежат id услуги, id мастера и момент начала.
SERVICE_ID_MAX: Final = 24
STAFF_ID_MAX: Final = 16
_SERVICE_ID: Final = re.compile(rf'^[a-z0-9][a-z0-9-]{{0,{SERVICE_ID_MAX - 1}}}$')
_STAFF_ID: Final = re.compile(rf'^[a-z0-9][a-z0-9-]{{0,{STAFF_ID_MAX - 1}}}$')
_FAQ_ID: Final = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
_INTERVAL: Final = re.compile(r'^(\d{2}):(\d{2})-(\d{2}):(\d{2})$')
_CURRENCY: Final = re.compile(r'^[A-Z]{3}$')

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
        texts: Переопределения текстов: `{ru = {booked = "..."}}`.
        state_ttl_min: Сколько минут бот помнит незаконченный разговор.
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
    texts: dict[str, dict[str, str]] = {}
    state_ttl_min: int = 30

    def __post_init__(self) -> None:
        self._check_languages()
        self._check_catalog()
        self._check_numbers()
        if self.hours.is_empty:
            raise ConfigError('booking.hours: ни одного рабочего интервала — записаться будет некуда')
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


def _decode_hook(kind: type, value: object) -> object:
    # TOML отдаёт пути строками, а msgspec сам Path не строит.
    if kind is Path and isinstance(value, str):
        return Path(value)
    raise NotImplementedError


def load_booking_config(path: Path) -> BookingConfig:
    """Читает и проверяет конфиг; дробные числа TOML сразу становятся `Decimal`.

    Ядро (`load_client_config`) читает TOML с float: для цены `35.50` это безвредно, но
    деньги в пакете не должны проходить через float ни на одном шаге, поэтому здесь
    свой разбор с `parse_float=Decimal`.

    Args:
        path: Путь к TOML-файлу.

    Returns:
        Проверенный конфиг.

    Raises:
        ConfigError: Файла нет, TOML битый или конфиг не проходит проверки.
    """
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'), parse_float=Decimal)
    except FileNotFoundError:
        raise ConfigError(f'конфиг не найден: {path}') from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'{path}: битый TOML: {exc}') from None
    try:
        return msgspec.convert(data, type=BookingConfig, dec_hook=_decode_hook)
    except msgspec.ValidationError as exc:
        raise ConfigError(f'{path}: {exc}') from None


def placeholders(template: str) -> set[str]:
    """Имена подстановок `{name}` в шаблоне текста."""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}

"""Данные кнопок: короткая грамматика `действие:арг:арг`, укладывающаяся в 64 байта Telegram.

Каждая кнопка самодостаточна: в ней лежит всё, что нужно для шага (услуга, мастер,
время), кроме имени и телефона покупателя — они в его профиле. Поэтому кнопка из
старого сообщения или после перезапуска бота работает, а не ломает разговор, — а
движок всё равно перепроверяет по базе, свободен ли ещё слот и чья это запись.
Данные кнопки может подделать модифицированный клиент: здесь они только разбираются,
доверия к ним нет.
"""

import datetime as dt
from collections.abc import Sequence
from typing import Final

import msgspec

from autostudio.booking.domain import Source

ANY_STAFF: Final = '_'
_TS_FORMAT: Final = '%Y%m%d%H%M'
_DAY_FORMAT: Final = '%Y%m%d'
_SOURCES: Final[dict[str, Source]] = {'b': 'buttons', 'a': 'ai'}
_SOURCE_CODES: Final[dict[Source, str]] = {'buttons': 'b', 'ai': 'a'}

# Коды действий. Короткие, потому что лимит 64 байта делят id услуги (до 24) и мастера (до 16).
MENU: Final = 'm'
BOOK: Final = 'b'
MY: Final = 'my'
PRICES: Final = 'pr'
ASK: Final = 'q'
HUMAN: Final = 'h'
LANGUAGE: Final = 'l'
ABORT: Final = 'x'
USE_NAME: Final = 'nm'
SERVICE: Final = 'sv'
STAFF: Final = 'st'
DAY: Final = 'd'
DAYS_FROM: Final = 'dd'
TIME: Final = 't'
CONFIRM: Final = 'ok'
EDIT: Final = 'ed'
BOOKING: Final = 'bk'
CANCEL_ASK: Final = 'cx'
CANCEL_YES: Final = 'cy'
MOVE: Final = 'rs'
MOVE_DAY: Final = 'rd'
MOVE_TIME: Final = 'rt'
MOVE_YES: Final = 'ry'
ADMIN_CANCEL: Final = 'ac'


class SlotRef(msgspec.Struct, frozen=True, kw_only=True):
    """Слот в кнопке: какую услугу, к кому и когда выбрал покупатель.

    Attributes:
        source: `buttons` или `ai` — для метрики «записи из свободного текста».
        service_id: Услуга.
        staff_id: Мастер или None — «любой свободный» (назначится при подтверждении).
        start: Начало, UTC.
    """

    source: Source
    service_id: str
    staff_id: str | None
    start: dt.datetime


def encode_ts(moment: dt.datetime) -> str:
    """Момент UTC в 12 знаков: `202610211300`."""
    return moment.astimezone(dt.UTC).strftime(_TS_FORMAT)


def decode_ts(text: str) -> dt.datetime | None:
    """Обратное к `encode_ts`; None для мусора."""
    if len(text) != len('YYYYMMDDHHMM') or not text.isdigit():
        return None
    try:
        return dt.datetime.strptime(text, _TS_FORMAT).replace(tzinfo=dt.UTC)
    except ValueError:
        return None


def encode_day(day: dt.date) -> str:
    """Дата в 8 знаков: `20261021`."""
    return day.strftime(_DAY_FORMAT)


def decode_day(text: str) -> dt.date | None:
    """Обратное к `encode_day`; None для мусора."""
    if len(text) != len('YYYYMMDD') or not text.isdigit():
        return None
    try:
        return dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None


def staff_code(staff_id: str | None) -> str:
    """id мастера или `_` для «любой свободный»."""
    return staff_id or ANY_STAFF


def staff_from_code(code: str) -> str | None:
    """Обратное к `staff_code`."""
    return None if code == ANY_STAFF else code


def join(action: str, *args: str) -> str:
    """Склеивает данные кнопки."""
    return ':'.join((action, *args))


def split(data: str) -> tuple[str, list[str]]:
    """Разбирает данные кнопки на действие и аргументы."""
    action, *args = data.split(':')
    return action, args


def slot_data(action: str, ref: SlotRef) -> str:
    """Данные кнопки шага со слотом (`t`, `ok`, `ed`)."""
    return join(action, _SOURCE_CODES[ref.source], ref.service_id, staff_code(ref.staff_id), encode_ts(ref.start))


def parse_slot(args: Sequence[str]) -> SlotRef | None:
    """Слот из аргументов кнопки; None — данные битые."""
    if len(args) != 4 or args[0] not in _SOURCES or not args[1] or not args[2]:
        return None
    start = decode_ts(args[3])
    if start is None:
        return None
    return SlotRef(source=_SOURCES[args[0]], service_id=args[1], staff_id=staff_from_code(args[2]), start=start)


def day_data(service_id: str, staff_id: str | None, day: dt.date, page: int = 0) -> str:
    """Кнопка дня (и страницы времени в нём)."""
    parts = [service_id, staff_code(staff_id), encode_day(day)]
    return join(DAY, *parts, str(page)) if page else join(DAY, *parts)

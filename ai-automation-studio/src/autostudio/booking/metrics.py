"""Что бот записи сделал за месяц — строки и «главное» для отчёта клиенту.

Минуты на действие — честная оценка ручной работы администратора: принять запись
по телефону или в мессенджере (уточнить услугу, найти окно, записать, подтвердить) —
около 5 минут; отмена — 2; перенос — 4 (найти новое окно и согласовать); напоминание —
1 (сообщение или звонок); ответ на типовой вопрос — 3. Передача вопроса человеку
экономии не даёт и считается с нулём: её видно, но она не раздувает итог.

Главный довод продлить поддержку — доля записей, пришедших, пока бизнес был закрыт:
это клиенты, которых без бота взяли бы конкуренты или автоответчик.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Final

from autostudio.booking.config import WEEKDAYS
from autostudio.booking.texts import WEEKDAYS_FULL, plural_ru
from autostudio.events import Event
from autostudio.report import MetricSpec

METRICS: Final[list[MetricSpec]] = [
    MetricSpec(kind='booking.created', label={'en': 'Bookings made', 'ru': 'Записей создано'}, minutes_saved=5),
    MetricSpec(kind='booking.cancelled', label={'en': 'Cancellations', 'ru': 'Отмен'}, minutes_saved=2),
    MetricSpec(kind='booking.rescheduled', label={'en': 'Reschedules', 'ru': 'Переносов'}, minutes_saved=4),
    MetricSpec(
        kind='booking.reminded', label={'en': 'Reminders sent', 'ru': 'Напоминаний отправлено'}, minutes_saved=1
    ),
    MetricSpec(
        kind='booking.question_answered',
        label={'en': 'Questions answered', 'ru': 'Ответов на вопросы'},
        minutes_saved=3,
    ),
    MetricSpec(
        kind='booking.handoff',
        label={'en': 'Passed to a person', 'ru': 'Передано администратору'},
        minutes_saved=0,
    ),
]

_TEXT: Final[dict[str, dict[str, str]]] = {
    'en': {
        'busiest': 'Busiest day: {day} — {count} of {total} {word}.',
        'top': 'Most booked: {service} ({count}).',
        'after_hours': '{pct}% of bookings came in while you were closed ({count} of {total}).',
        'questions': '{answered} customer {word} answered automatically; {handoff} passed to you.',
    },
    'ru': {
        'busiest': 'Самый загруженный день — {day}: {count} из {total} {word}.',
        'top': 'Самая популярная услуга — {service} ({count}).',
        'after_hours': '{pct}% записей пришли, когда вы были закрыты ({count} из {total}).',
        'questions': '{answered} {word} клиентов бот закрыл сам, {handoff} передано вам.',
    },
}


def highlights(events: Sequence[Event], language: str) -> list[str]:
    """«Главное за месяц» для отчёта клиенту.

    Args:
        events: События пакета booking за месяц.
        language: Язык отчёта (`en` или `ru`, иначе английский).

    Returns:
        Строки отчёта; пусто, если записей не было.
    """
    lang = language if language in _TEXT else 'en'
    t = _TEXT[lang]
    out: list[str] = []
    created = [event for event in events if event.kind == 'booking.created']
    total = len(created)
    if total:
        weekdays = Counter(event.payload.get('weekday', '') for event in created if event.payload.get('weekday'))
        if weekdays:
            day, count = min(weekdays.items(), key=lambda item: (-item[1], WEEKDAYS.index(item[0])))
            name = WEEKDAYS_FULL[lang][WEEKDAYS.index(day)]
            out.append(t['busiest'].format(day=name, count=count, total=total, word=_bookings_word(total, lang)))
        services = Counter(
            event.payload.get('service_name') or event.payload.get('service', '')
            for event in created
            if event.payload.get('service_name') or event.payload.get('service')
        )
        if services:
            service, count = min(services.items(), key=lambda item: (-item[1], item[0]))
            out.append(t['top'].format(service=service, count=count))
        after = sum(1 for event in created if event.payload.get('after_hours') == 'yes')
        if after:
            out.append(t['after_hours'].format(pct=round(100 * after / total), count=after, total=total))
    answered = sum(1 for event in events if event.kind == 'booking.question_answered')
    handoff = sum(1 for event in events if event.kind == 'booking.handoff')
    if answered:
        word = (
            plural_ru(answered, ('вопрос', 'вопроса', 'вопросов')) if lang == 'ru' else _plural_en(answered, 'question')
        )
        out.append(t['questions'].format(answered=answered, handoff=handoff, word=word))
    return out


def _plural_en(count: int, word: str) -> str:
    return word if count == 1 else f'{word}s'


def _bookings_word(total: int, language: str) -> str:
    # После «из N» в русском — родительный падеж: «из 1 записи», «из 21 записи», «из 5 записей».
    if language == 'ru':
        return 'записи' if total % 10 == 1 and total % 100 != 11 else 'записей'
    return _plural_en(total, 'booking')

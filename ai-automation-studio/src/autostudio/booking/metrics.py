"""Что бот записи сделал за месяц — строки и «главное» для отчёта клиенту.

Минуты на действие — честная оценка ручной работы администратора: принять запись
по телефону или в мессенджере (уточнить услугу, найти окно, записать, подтвердить) —
около 5 минут; отмена — 2; перенос — 4 (найти новое окно и согласовать); напоминание —
1 (сообщение или звонок); ответ на типовой вопрос — 3. Передача вопроса человеку
экономии не даёт и считается с нулём: её видно, но она не раздувает итог. Так же с нулём —
записи «с улицы» и по телефону, которые администратор вносит через бота: это его работа, —
и сообщения, не переданные из-за лимита уведомлений: владелец должен их видеть, чтобы
решить, не поднять ли лимит.

Главный довод продлить поддержку — доля записей, пришедших, пока бизнес был закрыт:
это клиенты, которых без бота взяли бы конкуренты или автоответчик.

Откуда приходят записи: канал (Telegram, WhatsApp) и метка ссылки, по которой покупатель
пришёл впервые (`t.me/<bot>?start=instagram`, реклама Click-to-WhatsApp), — владелец видит,
какая реклама и какое место на сайте приводят клиентов. Сообщения WhatsApp считаются
отдельно (обычные и шаблоны): за них клиент платит Meta, и объём должен быть в отчёте.
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
    MetricSpec(
        kind='booking.handoff_throttled',
        label={'en': 'Messages not passed on (limit reached)', 'ru': 'Сообщений не передано (сработал лимит)'},
        minutes_saved=0,
    ),
    MetricSpec(
        kind='booking.walk_in',
        label={'en': 'Walk-in and phone bookings entered by staff', 'ru': 'Записей внесено администратором'},
        minutes_saved=0,
    ),
    MetricSpec(
        kind='booking.link_opened',
        label={'en': 'Bot opened from a tracked link', 'ru': 'Переходов в бота по ссылке с меткой'},
        minutes_saved=0,
    ),
    MetricSpec(
        kind='booking.whatsapp_message',
        label={'en': 'WhatsApp messages sent (billed by Meta)', 'ru': 'Сообщений WhatsApp (оплачивает Meta)'},
        minutes_saved=0,
    ),
]

_CHANNEL_NAMES: Final[dict[str, str]] = {'telegram': 'Telegram', 'whatsapp': 'WhatsApp'}
_TOP_SOURCES: Final = 5

_TEXT: Final[dict[str, dict[str, str]]] = {
    'en': {
        'busiest': 'Busiest day: {day} — {count} of {total} {word}.',
        'top': 'Most booked: {service} ({count}).',
        'after_hours': '{pct}% of bookings came in while you were closed ({count} of {total}).',
        'questions': '{answered} customer {word} answered automatically; {handoff} passed to you.',
        'channels': 'Bookings by channel: {items}.',
        'sources': 'Bookings from tracked links: {items} (of {total}).',
        'whatsapp': 'WhatsApp: {total} {word} sent, {templates} of them template reminders outside the 24-hour window.',
    },
    'ru': {
        'busiest': 'Самый загруженный день — {day}: {count} из {total} {word}.',
        'top': 'Самая популярная услуга — {service} ({count}).',
        'after_hours': '{pct}% записей пришли, когда вы были закрыты ({count} из {total}).',
        'questions': '{answered} {word} клиентов бот закрыл сам, {handoff} передано вам.',
        'channels': 'Записи по каналам: {items}.',
        'sources': 'Записи по ссылкам с меткой: {items} (из {total}).',
        'whatsapp': 'WhatsApp: отправлено {total} {word}, из них {templates} — напоминания шаблоном вне окна 24 ч.',
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
        out += _origin_lines(created, t)
    answered = sum(1 for event in events if event.kind == 'booking.question_answered')
    handoff = sum(1 for event in events if event.kind == 'booking.handoff')
    if answered:
        word = (
            plural_ru(answered, ('вопрос', 'вопроса', 'вопросов')) if lang == 'ru' else _plural_en(answered, 'question')
        )
        out.append(t['questions'].format(answered=answered, handoff=handoff, word=word))
    sent = [event for event in events if event.kind == 'booking.whatsapp_message']
    if sent:
        templates = sum(1 for event in sent if event.payload.get('kind') == 'template')
        count = len(sent)
        word = (
            plural_ru(count, ('сообщение', 'сообщения', 'сообщений')) if lang == 'ru' else _plural_en(count, 'message')
        )
        out.append(t['whatsapp'].format(total=count, templates=templates, word=word))
    return out


def _origin_lines(created: Sequence[Event], t: dict[str, str]) -> list[str]:
    out: list[str] = []
    channels = Counter(event.payload.get('channel', '') for event in created if event.payload.get('channel'))
    # Канал показывается, только когда их больше одного: «Telegram 12» без WhatsApp ничего не говорит.
    if len(channels) > 1:
        items = ', '.join(
            f'{_CHANNEL_NAMES.get(name, name)} {count}'
            for name, count in sorted(channels.items(), key=lambda item: (-item[1], item[0]))
        )
        out.append(t['channels'].format(items=items))
    sources = Counter(event.payload.get('came_from', '') for event in created if event.payload.get('came_from'))
    if sources:
        top = sorted(sources.items(), key=lambda item: (-item[1], item[0]))[:_TOP_SOURCES]
        items = ', '.join(f'{name} {count}' for name, count in top)
        out.append(t['sources'].format(items=items, total=len(created)))
    return out


def _plural_en(count: int, word: str) -> str:
    return word if count == 1 else f'{word}s'


def _bookings_word(total: int, language: str) -> str:
    # После «из N» в русском — родительный падеж: «из 1 записи», «из 21 записи», «из 5 записей».
    if language == 'ru':
        return 'записи' if total % 10 == 1 and total % 100 != 11 else 'записей'
    return _plural_en(total, 'booking')

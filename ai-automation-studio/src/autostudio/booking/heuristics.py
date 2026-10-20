"""Эвристики `FakeLLM` для бота записи: демо и тесты без ключа и без сети.

Эвристика видит ровно то же, что настоящая модель: системный промпт (услуги, FAQ,
«сегодня») и реплику покупателя в теге. Поэтому демо проходит весь путь — промпт,
JSON, схема, проверка кодом — и отличается от боевого режима только качеством
понимания. Правила простые и предсказуемые: ключевые слова и регулярные выражения
на английском и русском.
"""

import datetime as dt
import re
from collections.abc import Iterable
from typing import Final

from autostudio.booking.prompts import SOURCE_HOURS, SOURCE_PRICES, BookingIntent, FaqAnswer, IntentKind
from autostudio.llm import FakeLLM

_MESSAGE: Final = re.compile(r'<customer_message>\n(.*)\n</customer_message>', re.DOTALL)
_TODAY: Final = re.compile(r'^TODAY: (\d{4}-\d{2}-\d{2})', re.MULTILINE)
_SERVICE_LINE: Final = re.compile(r'^- ([a-z0-9][a-z0-9-]*): (.+?) \| (.+?) \|', re.MULTILINE)
_FAQ_LINE: Final = re.compile(r'^- \[([a-z0-9-]+)\] Q: (.*?) \| A: (.*)$', re.MULTILINE)
_HOURS_LINE: Final = re.compile(r'^- (mon|tue|wed|thu|fri|sat|sun): (.+)$', re.MULTILINE)
_WORD: Final = re.compile(r'[a-zа-яё0-9]+')

_HUMAN: Final = (
    'human', 'person', 'operator', 'manager', 'administrator', 'someone real',
    'оператор', 'человек', 'администратор', 'менеджер', 'живой',
)  # fmt: skip
_RESCHEDULE: Final = ('reschedule', 'move my', 'change my booking', 'перенес', 'перенест', 'перенос')
_CANCEL: Final = ('cancel', 'отмен')
_POLICY: Final = ('can i', 'how late', 'policy', 'free of charge', 'можно ли', 'за сколько', 'до скольки', 'бесплатн')
_BOOKING: Final = (
    'book', 'appointment', 'slot', 'available', 'free time', 'can i come', 'come in',
    'запис', 'запиш', 'можно на', 'хочу на', 'свободн', 'окошк', 'окно',
)  # fmt: skip
_PRICE: Final = ('how much', 'price', 'cost', 'сколько стоит', 'цена', 'цены', 'стоимост', 'прайс')
_HOURS: Final = ('open', 'hours', 'working', 'работаете', 'часы работы', 'открыты', 'график')
_INFO: Final = (
    *_PRICE, *_HOURS, 'where', 'address', 'parking', 'card', 'cash', 'pay',
    'где', 'адрес', 'парков', 'карт', 'налич', 'оплат',
)  # fmt: skip
_QUESTION: Final = (
    'where', 'how', 'what', 'when', 'do you', 'is there', 'are you',
    'где', 'как', 'сколько', 'есть ли', 'принимаете', 'какие', 'когда',
)  # fmt: skip
_GREETING: Final = ('hi', 'hello', 'hey', 'good morning', 'good evening', 'привет', 'здравствуй', 'добрый', 'салам')

_WEEKDAYS_EN: Final = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
_WEEKDAYS_RU: Final = ('понедельник', 'вторник', 'сред', 'четверг', 'пятниц', 'суббот', 'воскресен')
_MONTHS_EN: Final = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')
_MONTHS_RU: Final = (
    'январ',
    'феврал',
    'март',
    'апрел',
    'ма',
    'июн',
    'июл',
    'август',
    'сентябр',
    'октябр',
    'ноябр',
    'декабр',
)
_STOP: Final = frozenset(
    {
        'the', 'and', 'you', 'your', 'are', 'for', 'with', 'have', 'has', 'can', 'could', 'would', 'will',
        'what', 'how', 'does', 'take', 'there', 'this', 'that', 'from', 'about', 'any', 'some', 'please', 'tell',
        'get', 'is', 'it', 'to', 'of', 'in', 'on', 'at', 'a', 'an', 'i', 'we', 'my', 'our', 'or', 'be', 'do',
        'me', 'need', 'want',
        'и', 'в', 'на', 'у', 'вы', 'вас', 'ли', 'а', 'как', 'что', 'это', 'мне', 'можно', 'есть', 'с', 'по',
        'для', 'до', 'за', 'или', 'не', 'я', 'мы', 'ваш', 'вам', 'ну',
    }
)  # fmt: skip


def _message(prompt: str) -> str:
    match = _MESSAGE.search(prompt)
    return (match.group(1) if match else prompt).lower()


def _has(text: str, words: Iterable[str]) -> bool:
    return any(re.search(rf'(?<![a-zа-яё]){re.escape(word)}', text) for word in words)


def _stems(text: str) -> list[str]:
    return [word[: max(4, len(word) - 2)] for word in _WORD.findall(text.lower()) if len(word) >= 4]


def _keywords(text: str) -> set[str]:
    # Грубая основа из 4 букв: «card»/«cards», «карт»/«картой», «парк»/«парковка».
    words = [word for word in _WORD.findall(text.lower()) if word not in _STOP and not word.isdigit()]
    return {word[:4] for word in words if len(word) >= 3}


def _match_service(text: str, system: str) -> str | None:
    words = _WORD.findall(text)
    best: tuple[int, float, str] | None = None
    for service_id, names, _price in _SERVICE_LINE.findall(system):
        for name in names.split(' / '):
            stems = _stems(name)
            matched = sum(1 for stem in stems if any(word.startswith(stem) for word in words))
            if matched and stems:
                score = (matched, matched / len(stems), service_id)
                if best is None or score[:2] > best[:2]:
                    best = score
    return None if best is None else best[2]


def _resolve_day(text: str, today: dt.date) -> dt.date | None:
    if _has(text, ('послезавтра', 'day after tomorrow')):
        return today + dt.timedelta(days=2)
    if _has(text, ('завтра', 'tomorrow')):
        return today + dt.timedelta(days=1)
    if _has(text, ('сегодня', 'today', 'tonight', 'this morning', 'this afternoon', 'this evening')):
        return today
    for index, (en, ru) in enumerate(zip(_WEEKDAYS_EN, _WEEKDAYS_RU, strict=True)):
        if re.search(rf'\b({en}|{en[:3]})\b', text) or ru in text:
            # «В пятницу», сказанное в пятницу, — почти всегда следующая пятница.
            return today + dt.timedelta(days=(index - today.weekday() - 1) % 7 + 1)
    return _explicit_date(text, today)


def _explicit_date(text: str, today: dt.date) -> dt.date | None:
    candidates: list[tuple[int, int, int | None]] = []
    if iso := re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', text):
        candidates.append((int(iso.group(3)), int(iso.group(2)), int(iso.group(1))))
    if full := re.search(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b', text):
        candidates.append((int(full.group(1)), int(full.group(2)), int(full.group(3))))
    if dotted := re.search(r'(?<![\d:])(\d{1,2})[./](\d{1,2})(?![\d./:])', text):
        candidates.append((int(dotted.group(1)), int(dotted.group(2)), None))
    for number, name in re.findall(r'\b(\d{1,2})\s+([a-zа-яё]+)', text):
        candidates.extend((int(number), month, None) for month in _month_numbers(name))
    for name, number in re.findall(r'\b([a-z]+)\s+(\d{1,2})\b', text):
        candidates.extend((int(number), month, None) for month in _month_numbers(name))
    for day, month, year in candidates:
        try:
            found = dt.date(year or today.year, month, day)
        except ValueError:
            continue
        if year is None and found < today:
            found = found.replace(year=today.year + 1)
        return found
    return None


def _month_numbers(name: str) -> list[int]:
    for index, (en, ru) in enumerate(zip(_MONTHS_EN, _MONTHS_RU, strict=True)):
        if name.startswith(en) or (name.startswith(ru) and (len(ru) > 2 or name in ('мая', 'май'))):
            return [index + 1]
    return []


def _hour(raw: str, minute: str | None, suffix: str | None) -> str | None:
    hour = int(raw)
    if suffix == 'pm' and hour < 12:
        hour += 12
    elif suffix == 'am' and hour == 12:
        hour = 0
    elif suffix is None and 1 <= hour <= 8:
        # Дневной бизнес: «после 5» — это 17:00, а не пять утра (то же правило — в промпте модели).
        hour += 12
    if hour > 23:
        return None
    return f'{hour:02d}:{int(minute or 0):02d}'


def _resolve_times(text: str) -> tuple[str | None, str | None]:
    edge = r'(?<![a-zа-яё0-9])'
    clock = r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?'
    if found := re.search(rf'{edge}(?:between|from|с)\s+{clock}\s*(?:and|to|до|-)\s*{clock}', text):
        groups = found.groups()
        return _hour(*groups[:3]), _hour(*groups[3:])
    if found := re.search(rf'{edge}(?:after|from|после|с)\s+{clock}', text):
        return _hour(*found.groups()), None
    if found := re.search(rf'{edge}(?:before|by|until|до)\s+{clock}', text):
        return None, _hour(*found.groups())
    exact = re.search(rf'{edge}(?:at|в|на)\s+{clock}', text)
    if exact and (exact.group(2) or exact.group(3) or 'час' in text or ' at ' in f' {text}'):
        value = _hour(*exact.groups())
        return value, value
    if found := re.search(r'(?<![\d.])(\d{1,2}):(\d{2})(?!\d)', text):
        value = _hour(found.group(1), found.group(2), None)
        return value, value
    if found := re.search(r'(?<![\d.])(\d{1,2})\s*(am|pm)\b', text):
        value = _hour(found.group(1), None, found.group(2))
        return value, value
    if _has(text, ('morning', 'утром', 'с утра')):
        return None, '12:00'
    if _has(text, ('afternoon', 'днём', 'днем')):
        return '12:00', '17:00'
    if _has(text, ('evening', 'tonight', 'вечером', 'вечер')):
        return '17:00', None
    return None, None


def _classify(text: str, *, has_details: bool) -> IntentKind:
    if _has(text, _HUMAN):
        return 'human'
    if _has(text, _RESCHEDULE):
        return 'reschedule'
    if _has(text, _CANCEL):
        return 'question' if '?' in text and _has(text, _POLICY) else 'cancel'
    if _has(text, _INFO) and not _has(text, _BOOKING):
        return 'question'
    if has_details or _has(text, _BOOKING):
        return 'book'
    if '?' in text or _has(text, _QUESTION):
        return 'question'
    if _has(text, _GREETING):
        return 'greeting'
    return 'other'


def guess_intent(system: str, prompt: str) -> BookingIntent:
    """Эвристика `BookingIntent`: ключевые слова, даты и время на английском и русском.

    Args:
        system: Системный промпт (из него берутся услуги и «сегодня»).
        prompt: Реплика покупателя в теге `<customer_message>`.

    Returns:
        Намерение — как его вернула бы модель.
    """
    text = _message(prompt)
    today_match = _TODAY.search(system)
    today = dt.date.fromisoformat(today_match.group(1)) if today_match else dt.date(2000, 1, 1)
    service_id = _match_service(text, system)
    day = _resolve_day(text, today)
    time_from, time_to = _resolve_times(text)
    intent = _classify(text, has_details=bool(service_id or day or time_from or time_to))
    booking = intent == 'book'
    return BookingIntent(
        intent=intent,
        service_id=service_id if booking else None,
        date=day.isoformat() if booking and day else None,
        time_from=time_from if booking else None,
        time_to=time_to if booking else None,
    )


def answer_faq(system: str, prompt: str) -> FaqAnswer:
    """Эвристика `FaqAnswer`: пересечение основ слов вопроса с пунктами FAQ из промпта.

    Args:
        system: Системный промпт с FAQ, ценами и часами.
        prompt: Вопрос покупателя в теге `<customer_message>`.

    Returns:
        Ответ из текстов FAQ или «не могу ответить».
    """
    text = _message(prompt)
    asked = _keywords(text)
    scored: list[tuple[int, str, str]] = []
    for faq_id, question, answer in _FAQ_LINE.findall(system):
        overlap = len(asked & _keywords(f'{question} {answer}'))
        if overlap:
            scored.append((overlap, faq_id, answer.strip()))
    scored.sort(key=lambda item: -item[0])
    ids = [faq_id for _, faq_id, _ in scored[:2]]
    parts = [answer for _, _, answer in scored[:2]]
    if _has(text, _PRICE):
        lines = [f'{names.split(" / ")[0]} — {price}' for _, names, price in _SERVICE_LINE.findall(system)]
        ids.append(SOURCE_PRICES)
        parts.append('\n'.join(lines))
    if _has(text, _HOURS):
        ids.append(SOURCE_HOURS)
        parts.append('\n'.join(f'{day}: {hours}' for day, hours in _HOURS_LINE.findall(system)))
    if not ids:
        return FaqAnswer(answerable=False, answer=None, faq_ids=[])
    return FaqAnswer(answerable=True, answer='\n'.join(parts), faq_ids=ids)


def booking_fake_llm() -> FakeLLM:
    """`FakeLLM` с эвристиками бота записи."""
    return FakeLLM({BookingIntent: guess_intent, FaqAnswer: answer_faq})

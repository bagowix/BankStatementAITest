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

import msgspec

from autostudio.booking.config import WEEKDAYS
from autostudio.booking.prompts import SOURCE_HOURS, SOURCE_PRICES, BookingIntent, FaqAnswer, IntentKind
from autostudio.booking.texts import DEFAULT_TEXTS, WEEKDAYS_FULL, Texts
from autostudio.llm import FakeLLM

_MESSAGE: Final = re.compile(r'<customer_message>\n(.*)\n</customer_message>', re.DOTALL)
_TODAY: Final = re.compile(r'^TODAY: (\d{4}-\d{2}-\d{2})', re.MULTILINE)
_SERVICE_LINE: Final = re.compile(r'^- ([a-z0-9][a-z0-9-]*): (.+?) \| (.+?) \|', re.MULTILINE)
_PRICE_LINE: Final = re.compile(r'^- [a-z0-9][a-z0-9-]*: (.+?) \| (.+?) \| (\d+) min \|', re.MULTILINE)
_JSON_STRING: Final = r'"(?:[^"\\]|\\.)*"'
_FAQ_LINE: Final = re.compile(rf'^- \[([a-z0-9-]+)\] Q: ({_JSON_STRING}) \| A: ({_JSON_STRING})$', re.MULTILINE)
_HOURS_LINE: Final = re.compile(r'^- (mon|tue|wed|thu|fri|sat|sun): (.+)$', re.MULTILINE)
_LANGUAGE: Final = re.compile(r'in language "([a-z]{2})"')
_WORD: Final = re.compile(r'[a-zа-яё0-9]+')

# Только явные просьбы о человеке: «2 persons», «на 2 человек» — это запись, а не жалоба.
_HUMAN: Final = (
    'human', 'real person', 'live person', 'a person', 'talk to someone', 'speak to someone', 'operator', 'manager',
    'administrator', 'someone real',
    'оператор', 'живой человек', 'живого человека', 'с человеком', 'позовите человека', 'администратор', 'менеджер',
    'живой',
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

_THANKS: Final = ('thank', 'thx', 'cheers', 'спасиб', 'благодар')
# Короткое «ок» — только отдельным словом: «около 5» и «окно» — не согласие.
_ACK: Final = re.compile(r'(?<![a-zа-яё])(ok|okay|ок|окей|great|perfect|отлично|супер|ясно|понятно)(?![a-zа-яё])')
_WEEKDAYS_EN: Final = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
# Падежи дней недели целиком: просто «сред» нашёлся бы в «среди недели».
_WEEKDAYS_RU: Final = (
    r'понедельник\w*',
    r'вторник\w*',
    r'сред[ауеы]',
    r'четверг\w*',
    r'пятниц[аеуы]',
    r'суббот[аеуы]',
    r'воскресень[еяю]',
)
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
        'accept', 'offer', 'know', 'like', 'also', 'here', 'just', 'still', 'really', 'possible',
        'и', 'в', 'на', 'у', 'вы', 'вас', 'ли', 'а', 'как', 'что', 'это', 'мне', 'можно', 'есть', 'с', 'по',
        'для', 'до', 'за', 'или', 'не', 'я', 'мы', 'ваш', 'вам', 'ну',
        'принимаете', 'принимают', 'берете', 'берёте', 'подскажите', 'скажите', 'пожалуйста', 'нужно', 'хочу',
        'там', 'тут', 'здесь', 'вообще', 'ещё', 'еще', 'также', 'тоже',
    }
)  # fmt: skip
# Слова, которыми спрашивают о ценах и часах: вопрос из них и названий услуг закрывается
# прайсом или графиком из конфига. Слово вне FAQ и этих разделов («подарочный сертификат»)
# значит, что правила ответа не знают, — вопрос уходит человеку.
_PRICE_WORDS: Final = (
    'how much price prices pricing cost costs сколько стоит стоят стоимость цена цены цену прайс почём почем'
)
_HOURS_WORDS: Final = (
    'when open opening opened hours working work until till close closes closing time schedule today tomorrow '
    'weekend weekends ' + ' '.join(_WEEKDAYS_EN) + ' '
    'когда работаете работает работы часы открыты открыто закрыты закрыто график режим сегодня завтра выходные '
    'скольки понедельник вторник среда среду четверг пятница пятницу суббота субботу воскресенье'
)


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
        if re.search(rf'\b({en}|{en[:3]})\b', text) or re.search(rf'(?<![a-zа-яё]){ru}(?![a-zа-яё])', text):
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
            if year is None and found < today:
                # Прошедшая в этом году дата без года — это следующий год. 29 февраля в
                # невисокосный следующий год не существует: такой кандидат пропускается.
                found = dt.date(today.year + 1, month, day)
        except ValueError:
            continue
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
    elif suffix is None and minute is None and not raw.startswith('0') and 1 <= hour <= 8:
        # Дневной бизнес: «после 5» — это 17:00, а не пять утра (то же правило — в промпте модели).
        # Время с минутами («8:30», «08:30») уже записано в 24-часовом формате.
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
    if _has(text, _THANKS) or (_ACK.search(text) and len(text.split()) <= 4):
        return 'thanks'
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
    """Эвристика `FaqAnswer`: точность важнее полноты.

    Пункт FAQ подходит, если основа слова вопроса есть в его *вопросе* (ответ «Later than
    that, please call us» не делает пункт об отмене ответом на «можно оплатить позже?»).
    А весь вопрос должен быть покрыт подошедшими пунктами, словами о ценах (с названиями
    услуг) и о часах работы. Осталось непокрытое слово («gift card») — правила не знают
    ответа, и вопрос уходит человеку: уверенный неверный ответ хуже передачи владельцу.

    Args:
        system: Системный промпт с FAQ, ценами и часами.
        prompt: Вопрос покупателя в теге `<customer_message>`.

    Returns:
        Ответ из текстов FAQ и конфига на языке покупателя или «не могу ответить».
    """
    text = _message(prompt)
    asked = _keywords(text)
    unanswerable = FaqAnswer(answerable=False, answer=None, faq_ids=[])
    if not asked:
        return unanswerable
    language_match = _LANGUAGE.search(system)
    language = language_match.group(1) if language_match and language_match.group(1) in DEFAULT_TEXTS else 'en'
    covered: set[str] = set()
    scored: list[tuple[int, str, str]] = []
    for faq_id, raw_question, raw_answer in _FAQ_LINE.findall(system):
        question, answer = _unquote(raw_question), _unquote(raw_answer)
        if asked & _keywords(question):
            known = asked & _keywords(f'{question} {answer}')
            covered |= known
            scored.append((len(known), faq_id, answer.strip()))
    scored.sort(key=lambda item: -item[0])
    ids = [faq_id for _, faq_id, _ in scored[:2]]
    parts = [answer for _, _, answer in scored[:2]]
    prices = _PRICE_LINE.findall(system)
    if _has(text, _PRICE):
        covered |= asked & _keywords(' '.join([_PRICE_WORDS, *(names for names, _, _ in prices)]))
        ids.append(SOURCE_PRICES)
        parts.append(_price_list(prices, language))
    if _has(text, _HOURS):
        covered |= asked & _keywords(_HOURS_WORDS)
        ids.append(SOURCE_HOURS)
        parts.append(_hours_list(_HOURS_LINE.findall(system), language))
    if not ids or asked - covered:
        return unanswerable
    return FaqAnswer(answerable=True, answer='\n'.join(parts), faq_ids=ids)


def _unquote(raw: str) -> str:
    try:
        return msgspec.json.decode(raw, type=str)
    except msgspec.DecodeError:
        return raw.strip('"')


def _price_list(lines: list[tuple[str, str, str]], language: str) -> str:
    return '\n'.join(
        f'• {names.split(" / ")[0]} — {price} ({Texts.duration(int(minutes), language)})'
        for names, price, minutes in lines
    )


def _hours_list(lines: list[tuple[str, str]], language: str) -> str:
    closed = DEFAULT_TEXTS[language]['hours_closed']
    out = []
    for code, hours in lines:
        name = WEEKDAYS_FULL[language][WEEKDAYS.index(code)]
        shown = closed if hours.strip() == 'closed' else hours.replace('-', '–')
        out.append(f'{name[:1].upper()}{name[1:]}: {shown}')
    return '\n'.join(out)


def booking_fake_llm() -> FakeLLM:
    """`FakeLLM` с эвристиками бота записи."""
    return FakeLLM({BookingIntent: guess_intent, FaqAnswer: answer_faq})

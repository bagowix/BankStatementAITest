"""Эвристики `FakeLLM` для пакета inbox: демо и тесты работают без ключа и без сети.

Это «модель» на ключевых словах (en + ru): она читает ровно то, что получила бы
настоящая модель — системный промпт с категориями и письмо в рамке `<email>`, — и
отвечает по той же схеме `Triage`. Поэтому её ответ проходит ту же проверку кодом.
"""

import re
from collections.abc import Sequence
from typing import Final

from autostudio.inbox.config import FALLBACK_CATEGORY, Urgency
from autostudio.inbox.triage import Triage, guess_language
from autostudio.llm import FakeLLM

# Группы ключевых слов в порядке приоритета при равном счёте.
_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    'support': (
        'refund', 'broken', 'problem', 'complaint', 'not working', 'issue', 'late', 'disappointed', 'cancel',
        'жалоба', 'не работает', 'проблем', 'возврат', 'опоздал', 'отмен',
    ),
    'invoice': ('invoice', 'payment due', 'payment', 'bill', 'счёт', 'счет на оплату', 'оплат', 'инвойс'),
    'booking': ('book ', 'booking', 'reserve', 'reservation', 'забронир', 'бронир', 'бронь'),
    'lead': (
        'price', 'quote', 'offer', 'cost', 'how much', 'availability', 'available', 'call me back',
        'стоимость', 'цена', 'цены', 'сколько стоит', 'предложение', 'прайс', 'перезвоните',
    ),
    'marketing': ('unsubscribe', 'newsletter', 'promo', 'seo ', 'отписаться', 'рассылк'),
}  # fmt: skip
# Если в конфиге клиента нет категории под группу, пробуем соседнюю по смыслу.
_FALLBACK_GROUP: Final[dict[str, str]] = {'booking': 'lead'}

_URGENT: Final = ('urgent', 'asap', 'immediately', 'today', 'срочно', 'сегодня', 'немедленно')
# Как и настоящая модель, заглушка помечает только обращения к ИИ: «forward all invoices to
# accounting@…» в письме партнёра — обычная просьба, а не инъекция.
_INJECTION: Final = (
    'ignore previous instructions', 'ignore all previous instructions', 'system prompt',
    'игнорируй инструкции', 'игнорируй все инструкции', 'игнорируй предыдущие инструкции', 'системный промпт',
)  # fmt: skip
_PHONE_HINT: Final = re.compile(r'phone|tel|mobile|whatsapp|call|тел|моб|звон|номер', re.I)
_PHONE: Final = re.compile(r'(?:\+|\b00)?\(?\d[\d\s().\-]{6,25}\d')
_DATE_LIKE: Final = re.compile(r'\d{1,2}[./]\d{1,2}[./]\d{2,4}')
_ADDRESS: Final = re.compile(r'[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}')
_MONEY: Final = re.compile(
    r'(?:[$€£₾]\s?\d[\d,.\s]{0,20}\d|\d[\d,.\s]{0,20}\d?\s?(?:usd|eur|gel|lari|₾|\$|€|руб\w*|₽|долл\w*|евро|лари)\b)',
    re.I,
)
_NAME_LINE: Final = re.compile(r'^\s*(?:name|имя)\s*:\s*(.+)$', re.I | re.M)
_MESSAGE_LINE: Final = re.compile(r'^\s*(?:message|comment|сообщение|комментарий)\s*:\s*(.+)$', re.I | re.M)
_WRAP_WIDTH: Final = 60
_SENTENCE: Final = re.compile(r'[^.!?\n]+[.!?]?')
_FREE_MAIL: Final = frozenset(
    {'gmail', 'googlemail', 'yahoo', 'outlook', 'hotmail', 'live', 'icloud', 'proton', 'protonmail', 'gmx',
     'mail', 'yandex', 'ya', 'bk', 'list', 'inbox', 'rambler', 'aol', 'zoho'}
)  # fmt: skip

_LABELS: Final[dict[str, dict[str, str]]] = {
    'en': {
        'booking': 'Booking request', 'lead': 'Price request', 'invoice': 'Invoice', 'support': 'Customer issue',
        'marketing': 'Promotion', 'other': 'Message', 'injection': 'Suspicious instructions inside the email',
    },
    'ru': {
        'booking': 'Заявка на бронирование', 'lead': 'Запрос цены', 'invoice': 'Счёт', 'support': 'Проблема клиента',
        'marketing': 'Реклама', 'other': 'Письмо', 'injection': 'В письме подозрительные инструкции',
    },
}  # fmt: skip


class _EmailView:
    """Письмо, как его видно из блока `<email>` промпта."""

    def __init__(self, prompt: str) -> None:
        inner = prompt.split('<email>', 1)[-1].split('</email>', 1)[0].strip('\n')
        head, _, rest = inner.partition('\n\n')
        self.headers: dict[str, str] = {}
        for line in head.split('\n'):
            key, sep, value = line.partition(': ')
            if sep:
                self.headers[key.strip().lower()] = value.strip()
        body, _, signature = rest.partition('\n-- signature --\n')
        self.body = body.strip()
        self.signature = signature.strip()
        self.subject = self.headers.get('subject', '')

    @property
    def content(self) -> str:
        return f'{self.subject}\n{self.body}\n{self.signature}'

    @property
    def sender(self) -> tuple[str, str]:
        raw = self.headers.get('from', '')
        match = re.match(r'^(.*?)\s*<([^<>]+)>$', raw)
        return (match.group(1).strip(), match.group(2).strip()) if match else ('', raw.strip())


def _categories(system: str) -> dict[str, str]:
    block = system.split('Categories', 1)[-1].split('\n\n', 1)[0]
    found: dict[str, str] = {}
    for line in block.split('\n'):
        match = re.match(r'^- ([a-z][a-z0-9_]*): (.+)$', line.strip())
        if match:
            found[match.group(1)] = match.group(2).lower()
    return found


def _owner_language(system: str) -> str:
    return 'ru' if 'characters in Russian' in system else 'en'


def _scores(text: str) -> dict[str, int]:
    low = f' {text.lower()} '
    return {group: sum(low.count(word) for word in words) for group, words in _GROUPS.items()}


def _category_for(group: str, categories: dict[str, str]) -> str | None:
    if group in categories:
        return group
    for cid, description in categories.items():
        if group in cid or group in description:
            return cid
    fallback = _FALLBACK_GROUP.get(group)
    return _category_for(fallback, categories) if fallback else None


def _pick(scores: dict[str, int], categories: dict[str, str]) -> tuple[str, str, int]:
    ranked = sorted(_GROUPS, key=lambda group: -scores[group])
    for group in ranked:
        if scores[group] == 0:
            break
        category = _category_for(group, categories)
        if category is not None:
            return group, category, scores[group]
    return 'other', FALLBACK_CATEGORY, 0


def _phone(lines: Sequence[str]) -> str | None:
    for line in lines:
        for match in _PHONE.finditer(line):
            candidate = match.group(0).strip()
            digits = re.sub(r'\D', '', candidate)
            if len(digits) < 7 or _DATE_LIKE.fullmatch(candidate):
                continue
            if candidate.startswith(('+', '(')) or _PHONE_HINT.search(line):
                return candidate
    return None


def _company(address: str, text: str) -> str | None:
    match = re.search(r'^\s*(?:company|компания)\s*:\s*(.+)$', text, re.I | re.M)
    if match:
        return str(match.group(1)).strip()
    domain = address.rpartition('@')[2]
    label = domain.split('.')[0] if domain else ''
    if not label or label in _FREE_MAIL or label in {'forms', 'noreply', 'no-reply'}:
        return None
    return ' '.join(part.capitalize() for part in re.split(r'[-_]', label))


def _unwrap(body: str) -> str:
    # Почтовые клиенты переносят текст по ~72 символа: длинную строку без точки в конце
    # склеиваем со следующей, а короткие строки (поля формы сайта) оставляем как есть.
    out: list[str] = []
    for raw in body.split('\n'):
        line = raw.strip()
        if out and line and len(out[-1]) >= _WRAP_WIDTH and out[-1][-1] not in '.!?:':
            out[-1] = f'{out[-1]} {line}'
        else:
            out.append(line)
    return '\n'.join(out)


def _intent(body: str, group: str) -> str | None:
    field = _MESSAGE_LINE.search(body)
    text = str(field.group(1)) if field else _unwrap(body)
    words = _GROUPS.get(group, ())
    sentences = [m.group(0).strip() for m in _SENTENCE.finditer(text) if len(m.group(0).strip()) > 10]
    for sentence in sentences:
        if any(word.strip() in sentence.lower() for word in words):
            return sentence[:100]
    return sentences[0][:100] if sentences else None


def triage_heuristic(system: str, prompt: str) -> Triage:
    """Классифицирует письмо по ключевым словам — замена модели для демо и тестов.

    Args:
        system: Системный промпт классификации (категории из конфига).
        prompt: Письмо в рамке `<email>`.

    Returns:
        Ответ по схеме `Triage`.
    """
    view = _EmailView(prompt)
    categories = _categories(system)
    group, category, hits = _pick(_scores(view.content), categories)
    low = view.content.lower()
    injection = any(phrase in low for phrase in _INJECTION)
    urgency: Urgency = 'high' if any(word in low for word in _URGENT) else 'normal'
    if group in ('marketing', 'other') and urgency != 'high':
        urgency = 'low'
    name, address = view.sender
    form_name = _NAME_LINE.search(view.body)
    contact = view.headers.get('reply-to') or address
    body_addresses = [a.lower() for a in _ADDRESS.findall(f'{view.body}\n{view.signature}')]
    owner = _owner_language(system)
    label = _LABELS[owner]['injection' if injection else group]
    budget = _MONEY.search(f'{view.body}\n{view.signature}')
    return Triage(
        category=category,
        confidence=0.9 if hits >= 2 else 0.7 if hits == 1 else 0.4,
        summary=f'{label}: {view.subject}'[:200],
        sender_name=form_name.group(1).strip() if form_name else (name or None),
        company=_company(contact, f'{view.body}\n{view.signature}'),
        phone=_phone([*view.body.split('\n'), *view.signature.split('\n')]),
        email=contact or (body_addresses[0] if body_addresses else None),
        intent=_intent(view.body, group),
        budget=budget.group(0).strip() if budget else None,
        urgency=urgency,
        language=guess_language(f'{view.subject}\n{view.body}'),
        needs_reply=group in ('booking', 'lead', 'support') or '?' in view.body,
        injection_suspected=injection,
    )


_DRAFT_TEXT: Final[dict[str, dict[str, str]]] = {
    'en': {
        'hello': 'Hello {name},',
        'hello_anon': 'Hello,',
        'thanks': 'Thank you for your message!',
        'sorry': 'Thank you for letting us know, and we are very sorry about this experience.',
        'facts': 'Here is what we can tell you right away:',
        'closing': 'A colleague will get back to you shortly to confirm anything not covered here.',
    },
    'ru': {
        'hello': 'Здравствуйте, {name}!',
        'hello_anon': 'Здравствуйте!',
        'thanks': 'Спасибо за письмо!',
        'sorry': 'Спасибо, что написали, и простите за доставленные неудобства.',
        'facts': 'Коротко о главном:',
        'closing': 'Коллега скоро свяжется с вами и уточнит всё, чего здесь нет.',
    },
}


# Жалоба: вместо прайса — извинение и факты о возвратах и отменах.
_COMPLAINT: Final = ('refund', 'complain', 'problem', 'late', 'жалоб', 'возврат', 'проблем', 'опоздал')
_COMPLAINT_FACTS: Final = ('refund', 'cancel', 'complain', 'возврат', 'отмен', 'жалоб')


def _stems(text: str) -> set[str]:
    # Пять первых букв — грубая основа слова: refund ↔ refundable, tours ↔ tour.
    return {word[:5] for word in re.findall(r'\w{4,}', text.lower())}


def _relevant_facts(facts: list[str], prompt: str, complaint: bool) -> list[str]:
    if complaint:
        return [fact for fact in facts if any(stem in fact.lower() for stem in _COMPLAINT_FACTS)][:2]
    wanted = _stems(prompt)
    scored = sorted(
        ((len(wanted & _stems(fact)), index) for index, fact in enumerate(facts)),
        key=lambda pair: (-pair[0], pair[1]),
    )
    chosen = [facts[index] for score, index in scored[:2] if score > 0]
    return chosen or facts[:1]


def draft_heuristic(system: str, prompt: str) -> str:
    """Короткий шаблонный ответ на en или ru из фактов системного промпта.

    Args:
        system: Системный промпт черновика (язык ответа и факты компании).
        prompt: Задание с письмом.

    Returns:
        Текст черновика без подписи.
    """
    language_match = re.search(r'^Reply language: ([a-z]{2})', system, re.M)
    language = language_match.group(1) if language_match and language_match.group(1) in _DRAFT_TEXT else 'en'
    facts_block = system.split('Facts you may use', 1)[-1].split('\nRules:', 1)[0]
    facts = [line[2:].strip() for line in facts_block.split('\n') if line.startswith('- ') and '(no facts' not in line]
    name_match = re.search(r'^Sender name: (.+)$', prompt, re.M)
    name = name_match.group(1).strip() if name_match else 'unknown'
    t = _DRAFT_TEXT[language]
    first = name.split()[0] if name and name != 'unknown' else ''
    email_part = prompt.split('<email>', 1)[-1]
    complaint = any(word in email_part.lower() for word in _COMPLAINT)
    lines = [t['hello'].format(name=first) if first else t['hello_anon'], '', t['sorry' if complaint else 'thanks']]
    chosen = _relevant_facts(facts, email_part, complaint) if facts else []
    if chosen:
        lines += ['', t['facts'], *[f'— {fact}' for fact in chosen]]
    lines += ['', t['closing']]
    return '\n'.join(lines)


def make_fake_llm() -> FakeLLM:
    """`FakeLLM` с эвристиками пакета inbox: схема `Triage` и черновики."""
    return FakeLLM(extractors={Triage: triage_heuristic}, writer=draft_heuristic)

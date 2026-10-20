"""Договор клиенту из шаблонов `docs/legal`: реквизиты оператора, поля клиента и ни одного пустого места.

Шаблоны размечены двумя способами (см. `docs/legal/README.md`):
- `{{КЛЮЧ}}` — поля оператора, одни для всех клиентов: имя, ID ИП, адрес, e-mail,
  Telegram, банк — из `operator.toml`; сервер, хостинг, копии — из его
  `[contract_fields]`;
- `[…]` — поля клиента и варианты на выбор (`[None / Care / Pro / Business]`), а
  также служебные пометки `[LAWYER: …]`, `[ACCOUNTANT: …]`, `[VERIFY: …]` (и русские
  `[ЮРИСТ: …]`, `[БУХГАЛТЕР: …]`, `[ПРОВЕРИТЬ: …]`), которые в готовом договоре
  остаться не должны.

Каждая скобка заполняется по точному тексту внутри неё; пустая строка убирает скобку
целиком. Всё, что осталось незаполненным, — ошибка со списком мест: договор с
пробелами или с пометкой юриста клиенту не уходит. Незакрытая `[` в шаблоне — тоже
ошибка: иначе пометка без `]` уехала бы клиенту как есть. Скобку с суммой (`[amount]`,
`[сумма]`), которая встречается несколько раз, одним значением не заполнить: предоплата,
остаток и абонентская плата — разные суммы, каждую задают `"amount#1"`, `"amount#2"`, ….
Пометки юриста и бухгалтера можно снять одним флагом (`--drop-review-notes`) после
проверки; пометки VERIFY — только по одной, осознанно: они отмечают обещания, которые
должны быть правдой.
"""

import datetime as dt
import hashlib
import html
import re
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

import msgspec

from autostudio.errors import ConfigError
from autostudio.office.markdown import to_html
from autostudio.office.money import Language
from autostudio.office.operator import OperatorProfile

type TemplateName = Literal['service-agreement', 'support-sla', 'dpa']
type PlaceholderKind = Literal['field', 'review', 'verify', 'unclosed']
type ClientKey = Literal[
    'name', 'legal_form', 'country', 'registration_number', 'address', 'representative', 'business_name'
]
type AgreementKey = Literal['number', 'date']

TEMPLATES: Final[tuple[TemplateName, ...]] = ('service-agreement', 'support-sla', 'dpa')

_REVIEW_WORDS: Final = frozenset({'LAWYER', 'ACCOUNTANT', 'ЮРИСТ', 'БУХГАЛТЕР'})
_VERIFY_WORDS: Final = frozenset({'VERIFY', 'ПРОВЕРИТЬ'})
_NOTE_PREFIX: Final = re.compile(r'^([A-ZА-ЯЁ]+(?:/[A-ZА-ЯЁ]+)*)\s*:')
_OPERATOR_FIELD: Final = re.compile(r'\{\{\s*([^{}]*?)\s*\}\}')
_BOX_MARKERS: Final = ('Template', 'Шаблон')
_BOX_SEARCH_LINES: Final = 15
# После пустой подстановки знак препинания прилипает к предыдущему слову: «Court\n.» → «Court.».
_GLUE_BEFORE: Final = '.,;:)'
_SLUG: Final = re.compile(r'[^A-Za-z0-9._-]+')
_SHOWN_KEY: Final = 90
_QUOTE_CONTINUATION: Final = re.compile(r'\n[ \t]*>[ \t]?')
_CHECKBOX: Final = frozenset({'', 'x', 'X'})
# Скобки с суммой: одна сумма на все места — почти всегда ошибка в условиях оплаты.
PER_PLACE_KEYS: Final = frozenset({'amount', 'сумма', 'price', 'цена', 'fee'})
_HASH_CHARS: Final = 8

# Какие скобки — это сведения о клиенте и договоре, по шаблонам и языкам. Ключ — точный
# текст в скобках. Одна и та же скобка значит разное в разных шаблонах: `[address]` в
# договоре — адрес клиента, а в DPA — адрес почтового ящика в тексте уведомления.
_SEMANTIC_TEXT: Final[Mapping[Language, Mapping[ClientKey | AgreementKey, tuple[str, ...]]]] = {
    'en': {
        'name': ("Client's legal name",),
        'legal_form': ('legal form',),
        'country': ('country',),
        'registration_number': ('registration number',),
        'address': ('address',),
        'representative': ('name, position',),
        'business_name': ('business name',),
        'number': ('number',),
        'date': ('date',),
    },
    'ru': {
        'name': ('полное наименование Заказчика', 'наименование Заказчика'),
        'legal_form': ('организационно-правовая форма',),
        'country': ('страна',),
        'registration_number': ('регистрационный номер',),
        'address': ('адрес',),
        'representative': ('имя, должность',),
        'business_name': ('название бизнеса',),
        'number': ('номер',),
        'date': ('дата',),
    },
}
_SEMANTIC_BY_TEMPLATE: Final[Mapping[TemplateName, tuple[ClientKey | AgreementKey, ...]]] = {
    'service-agreement': (
        'name',
        'legal_form',
        'country',
        'registration_number',
        'address',
        'representative',
        'number',
        'date',
    ),
    'support-sla': ('name', 'number', 'date'),
    'dpa': ('name', 'representative', 'business_name', 'number', 'date'),
}
_OPERATOR_HINTS: Final[Mapping[str, str]] = {
    'NAME': 'owner_name (или legal_name)',
    'IE_ID': 'id_number',
    'ADDRESS': 'address',
    'EMAIL': 'email',
    'TELEGRAM': 'telegram = "username" (без @)',
    'BANK_DETAILS': 'bank_name, iban, swift',
}


class ContractIncompleteError(ConfigError):
    """В договоре остались пустые места или пометки — файл не записан.

    Args:
        message: Список мест для оператора.
        missing: Незаполненные ключи (скобки и `{{…}}`).
    """

    def __init__(self, message: str, *, missing: Sequence[str]) -> None:
        super().__init__(message)
        self.missing = list(missing)


class Placeholder(msgspec.Struct, frozen=True, kw_only=True):
    """Скобка `[…]` в шаблоне.

    Attributes:
        key: Текст внутри скобок без лишних пробелов и переводов строк.
        kind: `field` — поле или вариант, `review` — пометка юриста/бухгалтера, `verify` — VERIFY/ПРОВЕРИТЬ,
            `unclosed` — `[` без пары: заполнить нельзя, только исправить шаблон.
        line: Строка шаблона (с 1).
        occurrence: Номер вхождения этого текста в шаблоне (с 1).
        start: Смещение `[` в тексте.
        end: Смещение после `]`.
    """

    key: str
    kind: PlaceholderKind
    line: int
    occurrence: int
    start: int
    end: int


class ContractParty(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Сведения о клиенте для договора (секция `[client]`).

    Attributes:
        name: Полное наименование.
        legal_form: Организационно-правовая форма.
        country: Страна регистрации.
        registration_number: Регистрационный номер.
        address: Юридический адрес.
        representative: Кто подписывает: «имя, должность».
        business_name: Название бизнеса для текстов уведомлений в DPA; по умолчанию — `name`.
    """

    name: str | None = None
    legal_form: str | None = None
    country: str | None = None
    registration_number: str | None = None
    address: str | None = None
    representative: str | None = None
    business_name: str | None = None


class AgreementInfo(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Номер и дата договора (секция `[agreement]`)."""

    number: str | None = None
    date: str | None = None


class ContractRequest(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Всё, что вписывается в шаблон под конкретного клиента.

    Attributes:
        client: Сведения о клиенте.
        agreement: Номер и дата договора.
        fields: Прочие скобки по точному тексту: `"14 / 30" = "14"`, `"date#2" = "..."`, `"VERIFY: …" = ""`.
    """

    client: ContractParty = msgspec.field(default_factory=ContractParty)
    agreement: AgreementInfo = msgspec.field(default_factory=AgreementInfo)
    fields: dict[str, str] = msgspec.field(default_factory=dict)

    def semantic(self) -> dict[ClientKey | AgreementKey, str]:
        """Заданные сведения о клиенте и договоре по смысловым именам."""
        values: dict[ClientKey | AgreementKey, str | None] = {
            'name': self.client.name,
            'legal_form': self.client.legal_form,
            'country': self.client.country,
            'registration_number': self.client.registration_number,
            'address': self.client.address,
            'representative': self.client.representative,
            'business_name': self.client.business_name or self.client.name,
            'number': self.agreement.number,
            'date': self.agreement.date,
        }
        return {key: value for key, value in values.items() if value is not None}


class RenderedContract(msgspec.Struct, frozen=True, kw_only=True):
    """Собранный договор.

    Attributes:
        text: Markdown без единой незаполненной скобки.
        notices: Что стоит знать: одинаковое значение в нескольких местах, неиспользованные ключи.
        dropped_notes: Сколько пометок юриста/бухгалтера снято флагом.
    """

    text: str
    notices: list[str]
    dropped_notes: int


def normalize_key(text: str) -> str:
    """Текст скобки без переносов, маркеров цитаты `>` в начале строк и двойных пробелов.

    Так он сравнивается с ключами файла клиента: скобка, перенесённая на новую строку
    внутри цитаты, совпадает с ключом, записанным в одну строку.
    """
    return ' '.join(_QUOTE_CONTINUATION.sub('\n', text).split())


def classify(key: str) -> PlaceholderKind:
    """Пометка юриста/бухгалтера, VERIFY или обычное поле — по слову перед двоеточием."""
    match = _NOTE_PREFIX.match(key)
    if match is None:
        return 'field'
    words = set(match.group(1).split('/'))
    if words & _VERIFY_WORDS:
        return 'verify'
    if words & _REVIEW_WORDS:
        return 'review'
    return 'field'


def template_path(templates_dir: Path, name: TemplateName, language: Language) -> Path:
    """Файл шаблона: `docs/legal/<name>.<lang>.md`."""
    return templates_dir / f'{name}.{language}.md'


def load_template(path: Path) -> str:
    """Читает шаблон.

    Raises:
        ConfigError: Файла нет.
    """
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        raise ConfigError(
            f'шаблон не найден: {path} (укажите --templates, если шаблоны лежат не в docs/legal)'
        ) from None


def strip_template_box(text: str) -> str:
    """Убирает врезку «Template. Not legal advice» / «Шаблон…» под заголовком.

    Строки врезки заменяются пустыми, чтобы номера строк в ошибках совпадали с файлом шаблона.
    Если врезка не нашлась, текст не меняется — и её поля `{{…}}` дадут ошибку, а не тихий брак.
    """
    lines = text.split('\n')
    for index, line in enumerate(lines[:_BOX_SEARCH_LINES]):
        if line.lstrip().startswith('>') and any(marker in line for marker in _BOX_MARKERS):
            end = index
            while end < len(lines) and lines[end].lstrip().startswith('>'):
                lines[end] = ''
                end += 1
            break
    return '\n'.join(lines)


def _matching_bracket(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
            if depth == 0:
                return index
    return -1


def scan_placeholders(text: str) -> list[Placeholder]:
    """Находит скобки верхнего уровня: вложенные (`[… [12] …]`) — часть текста внешней.

    Пропускаются код в обратных кавычках, ссылки `[текст](url)` и флажки `[ ]`. Незакрытая
    `[` возвращается как `unclosed` (ключ — остаток строки), а поиск продолжается со следующего символа.

    Args:
        text: Шаблон (уже без врезки).

    Returns:
        Скобки по порядку в тексте.
    """
    found: list[Placeholder] = []
    seen: Counter[str] = Counter()
    index = 0
    while index < len(text):
        char = text[index]
        if char == '`':
            closing = text.find('`', index + 1)
            index = index + 1 if closing == -1 else closing + 1
            continue
        if char != '[':
            index += 1
            continue
        end = _matching_bracket(text, index)
        if end == -1:
            line_end = text.find('\n', index)
            found.append(
                Placeholder(
                    key=normalize_key(text[index + 1 : len(text) if line_end == -1 else line_end]),
                    kind='unclosed',
                    line=text.count('\n', 0, index) + 1,
                    occurrence=1,
                    start=index,
                    end=index + 1,
                )
            )
            index += 1
            continue
        inner = text[index + 1 : end]
        is_link = text.startswith('(', end + 1)
        if is_link or inner.strip() in _CHECKBOX:
            index = end + 1
            continue
        key = normalize_key(inner)
        seen[key] += 1
        found.append(
            Placeholder(
                key=key,
                kind=classify(key),
                line=text.count('\n', 0, index) + 1,
                occurrence=seen[key],
                start=index,
                end=end + 1,
            )
        )
        index = end + 1
    return found


def operator_fields(operator: OperatorProfile, language: Language) -> dict[str, str]:
    """Значения `{{…}}` из профиля оператора; `[contract_fields]` дополняет и может переопределить их."""
    bank_word = 'банк ' if language == 'ru' else ''
    fields = {
        'NAME': operator.person_name(language),
        'IE_ID': operator.id_number,
        'ADDRESS': operator.address,
        'EMAIL': operator.email,
        'BANK_DETAILS': (
            f'IBAN {operator.iban_grouped}, {bank_word}{operator.bank_name}, SWIFT/BIC {operator.swift_code}'
        ),
    }
    if operator.telegram:
        fields['TELEGRAM'] = operator.telegram
    fields.update(operator.contract_fields)
    return fields


def semantic_keys(template: TemplateName, language: Language) -> dict[str, ClientKey | AgreementKey]:
    """Текст скобки → смысловое имя поля клиента для этого шаблона и языка."""
    table = _SEMANTIC_TEXT[language]
    return {text: name for name in _SEMANTIC_BY_TEMPLATE[template] for text in table[name]}


def _glue(result: str, following: str) -> str:
    trimmed = result.rstrip(' \t')
    if following and following in _GLUE_BEFORE:
        trimmed = trimmed.rstrip()
    return trimmed


def _short(key: str) -> str:
    return key if len(key) <= _SHOWN_KEY else key[: _SHOWN_KEY - 1] + '…'


def _incomplete(
    template: TemplateName,
    language: Language,
    missing: Sequence[Placeholder],
    missing_operator: Sequence[str],
    template_label: str,
) -> ContractIncompleteError:
    groups: dict[PlaceholderKind, dict[str, list[int]]] = {'unclosed': {}, 'field': {}, 'review': {}, 'verify': {}}
    for item in missing:
        groups[item.kind].setdefault(item.key, []).append(item.line)
    titles: dict[PlaceholderKind, str] = {
        'unclosed': 'незакрытые скобки "[" в шаблоне — исправьте копию шаблона и соберите из неё (--templates)',
        'field': 'поля и варианты [...] — [client]/[agreement]/[fields] файла клиента или флаги',
        'review': 'пометки юриста/бухгалтера — снимите в [fields] ("" или исправленный текст) '
        'или флагом --drop-review-notes после проверки',
        'verify': 'пометки VERIFY/ПРОВЕРИТЬ — решите каждую в [fields]: проверенный текст или "", '
        'только когда обещание правда',
    }
    total = len(missing) + len(missing_operator)
    lines = [f'{template_label}: договор не собран, файл не записан — незаполненных мест: {total}']
    for kind, keys in groups.items():
        if not keys:
            continue
        lines.append(f'  {titles[kind]}:')
        for key, numbers in keys.items():
            lines.append(f'    стр. {", ".join(str(n) for n in numbers)}: [{_short(key)}]')
    if missing_operator:
        lines.append('  поля {{…}} — operator.toml:')
        for key in sorted(set(missing_operator)):
            hint = _OPERATOR_HINTS.get(key, f'[contract_fields] {key} = "..."')
            lines.append(f'    {{{{{key}}}}} → {hint}')
    lines.append(f'  точные ключи всех полей: autostudio office contract fields {template} --lang {language}')
    keys = [item.key for item in missing] + [f'{{{{{key}}}}}' for key in missing_operator]
    return ContractIncompleteError('\n'.join(lines), missing=keys)


def render_contract(
    template_text: str,
    *,
    template: TemplateName,
    language: Language,
    operator: OperatorProfile,
    request: ContractRequest,
    drop_review_notes: bool = False,
    template_label: str = '',
) -> RenderedContract:
    """Собирает договор или объясняет, чего не хватает.

    Порядок: сначала скобки клиента (их ключи — текст шаблона как есть, с `{{…}}`
    внутри), затем поля оператора — и в тексте шаблона, и во вписанных значениях.

    Args:
        template_text: Шаблон в Markdown.
        template: Какой это шаблон (от него зависит смысл скобок вроде `[address]`).
        language: Язык шаблона.
        operator: Профиль оператора.
        request: Поля клиента.
        drop_review_notes: Снять все пометки юриста и бухгалтера.
        template_label: Как назвать шаблон в сообщениях (путь к файлу).

    Returns:
        Договор в Markdown и замечания.

    Raises:
        ContractIncompleteError: Осталась хоть одна незаполненная скобка, пометка, поле `{{…}}`
            или незакрытая `[` в шаблоне.
        ConfigError: Сведение о клиенте задано пустой строкой, или одна сумма задана сразу на
            несколько мест (`PER_PLACE_KEYS` без `#N`).
    """
    semantic_values = request.semantic()
    for name, value in semantic_values.items():
        if not value.strip():
            raise ConfigError(f'{name}: пустое значение — заполните или уберите строку')
    by_text = semantic_keys(template, language)
    raw = {normalize_key(key): value for key, value in request.fields.items()}
    text = strip_template_box(template_text)
    placeholders = scan_placeholders(text)
    used: set[str] = set()
    # Где одно значение без `#N` легло в несколько мест: о каждом таком ключе — заметка.
    bare: dict[str, tuple[str, list[int]]] = {}
    missing: list[Placeholder] = []
    dropped = 0
    result = ''
    position = 0
    for item in placeholders:
        result += text[position : item.start]
        position = item.end
        exact = f'{item.key}#{item.occurrence}'
        value: str | None = None
        if item.kind == 'unclosed':
            pass
        elif exact in raw:
            value = raw[exact]
            used.add(exact)
        elif item.key in raw:
            value = raw[item.key]
            used.add(item.key)
            bare.setdefault(item.key, (value, []))[1].append(item.line)
        elif item.kind == 'field' and item.key in by_text and by_text[item.key] in semantic_values:
            value = semantic_values[by_text[item.key]]
            bare.setdefault(item.key, (value, []))[1].append(item.line)
        elif item.kind == 'review' and drop_review_notes:
            value = ''
            dropped += 1
        if value is None:
            missing.append(item)
            result += text[item.start : item.end]
        elif value:
            result += value
        else:
            result = _glue(result, text[item.end : item.end + 1])
    result += text[position:]
    _refuse_shared_sums(bare)

    operator_values = operator_fields(operator, language)
    missing_operator: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in operator_values:
            return operator_values[key]
        missing_operator.append(key)
        return match.group(0)

    result = _OPERATOR_FIELD.sub(substitute, result)
    if missing or missing_operator:
        raise _incomplete(template, language, missing, missing_operator, template_label or template)

    notices = [
        f'[{_short(key)}] ×{len(lines)} (стр. {", ".join(str(n) for n in lines)}): везде «{value}»; '
        f'другое значение для одного места — "{_short(key)}#2" = "…"'
        for key, (value, lines) in bare.items()
        if len(lines) > 1
    ]
    notices += [f'ключ [fields] не найден в шаблоне: "{_short(key)}"' for key in raw if key not in used]
    text_out = re.sub(r'\n{3,}', '\n\n', result).strip() + '\n'
    return RenderedContract(text=text_out, notices=notices, dropped_notes=dropped)


def _refuse_shared_sums(bare: Mapping[str, tuple[str, list[int]]]) -> None:
    shared = {key: lines for key, (_, lines) in bare.items() if key in PER_PLACE_KEYS and len(lines) > 1}
    if not shared:
        return
    details = '; '.join(
        f'[{key}] в {len(lines)} местах (стр. {", ".join(str(n) for n in lines)}) — задайте '
        + ', '.join(f'"{key}#{n}"' for n in range(1, len(lines) + 1))
        for key, lines in shared.items()
    )
    raise ConfigError(
        f'одна сумма на несколько мест не подставляется: предоплата, остаток и абонентская плата — разные суммы. '
        f'{details} (одинаковые — тоже явно, по каждому месту)'
    )


def _merge_party(base: ContractParty, top: ContractParty) -> ContractParty:
    return ContractParty(
        name=top.name if top.name is not None else base.name,
        legal_form=top.legal_form if top.legal_form is not None else base.legal_form,
        country=top.country if top.country is not None else base.country,
        registration_number=top.registration_number
        if top.registration_number is not None
        else base.registration_number,
        address=top.address if top.address is not None else base.address,
        representative=top.representative if top.representative is not None else base.representative,
        business_name=top.business_name if top.business_name is not None else base.business_name,
    )


def merge_requests(requests: Sequence[ContractRequest]) -> ContractRequest:
    """Сливает файлы полей: каждый следующий переопределяет заданное в предыдущих.

    Так общий файл решений по пометкам (`contract-defaults.toml`) живёт отдельно от файла клиента.
    """
    merged = ContractRequest()
    for request in requests:
        merged = ContractRequest(
            client=_merge_party(merged.client, request.client),
            agreement=AgreementInfo(
                number=request.agreement.number if request.agreement.number is not None else merged.agreement.number,
                date=request.agreement.date if request.agreement.date is not None else merged.agreement.date,
            ),
            fields={**merged.fields, **request.fields},
        )
    return merged


def load_contract_request(path: Path) -> ContractRequest:
    """Читает файл полей клиента.

    Raises:
        ConfigError: Файла нет, TOML битый или секции не по схеме.
    """
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'), parse_float=Decimal)
    except FileNotFoundError:
        raise ConfigError(f'файл полей договора не найден: {path}') from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'{path}: битый TOML: {exc}') from None
    # TOML отдаёт `date = 2026-10-20` объектом date, а в договор дата идёт строкой.
    agreement = data.get('agreement')
    if isinstance(agreement, dict) and isinstance(agreement.get('date'), dt.date):
        agreement['date'] = str(agreement['date'])
    try:
        return msgspec.convert(data, type=ContractRequest)
    except msgspec.ValidationError as exc:
        raise ConfigError(f'{path}: {exc}') from None


def output_name(request: ContractRequest, template: TemplateName, language: Language, extension: str) -> str:
    """Имя файла: `<номер договора или клиент>-<шаблон>.<язык>.<расширение>`.

    Из имени остаются латиница, цифры и `._-`. Если в имени были другие буквы (кириллица,
    грузинский), добавляется короткий хеш имени: «ООО Ромашка» и «ИП Иванов» иначе
    получили бы один и тот же файл `contract-…`.
    """
    base = request.agreement.number or request.client.name or 'contract'
    slug = _SLUG.sub('-', base).strip('-._') or 'contract'
    if not base.isascii():
        slug += '-' + hashlib.sha256(base.strip().encode()).hexdigest()[:_HASH_CHARS]
    return f'{slug}-{template}.{language}.{extension}'


_CONTRACT_CSS: Final = """
@page { size: A4; margin: 20mm 18mm 20mm; }
* { box-sizing: border-box; }
body { margin: 0; background: #eceff1; color: #1b1f23;
  font: 10.5pt/1.5 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif; }
.doc { width: 210mm; margin: 10mm auto; padding: 20mm 18mm; background: #fff; box-shadow: 0 1px 8px rgba(0,0,0,.15); }
h1 { font-size: 17pt; text-align: center; margin: 0 0 8mm; line-height: 1.25; }
h2 { font-size: 12.5pt; margin: 7mm 0 2mm; break-after: avoid; }
h3 { font-size: 11pt; margin: 5mm 0 2mm; break-after: avoid; }
p { margin: 0 0 2.5mm; text-align: justify; hyphens: auto; }
ul, ol { margin: 0 0 2.5mm; padding-left: 7mm; }
li { margin: .5mm 0; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 4mm; font-size: 9.5pt; }
th, td { border: 1px solid #b8c0c8; padding: 1.5mm 2mm; vertical-align: top; text-align: left; }
th { background: #f3f5f7; }
td.r, th.r { text-align: right; }
tr { break-inside: avoid; }
blockquote { margin: 2mm 0 4mm; padding: 2mm 4mm; border-left: 3px solid #b8c0c8; background: #f7f8f9; }
hr { border: 0; border-top: 1px solid #b8c0c8; margin: 6mm 0; }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 9pt; }
@media print {
  body { background: #fff; }
  .doc { width: auto; margin: 0; padding: 0; box-shadow: none; }
}
"""


def contract_html(markdown_text: str, *, language: Language) -> str:
    """Договор в самодостаточном HTML для печати в PDF: A4, без внешних шрифтов и скриптов."""
    title = next(
        (line.lstrip('#').strip() for line in markdown_text.splitlines() if line.startswith('# ')), 'Agreement'
    )
    return (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{html.escape(title, quote=True)}</title><style>{_CONTRACT_CSS}</style></head>'
        f'<body><main class="doc">{to_html(markdown_text)}</main></body></html>\n'
    )


def _toml_string(text: str) -> str:
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def fields_skeleton(template_text: str, *, template: TemplateName, language: Language, label: str) -> str:
    """Заготовка TOML со всеми полями шаблона — всё закомментировано, чтобы пустое не прошло молча.

    Args:
        template_text: Шаблон.
        template: Его имя.
        language: Его язык.
        label: Путь к шаблону для заголовка.

    Returns:
        TOML-текст для файла клиента.
    """
    text = strip_template_box(template_text)
    scanned = scan_placeholders(text)
    placeholders = [item for item in scanned if item.kind != 'unclosed']
    by_text = semantic_keys(template, language)
    counts = Counter(item.key for item in placeholders)
    lines = [
        f'# Поля шаблона {label} для `autostudio office contract --client-file <этот файл>`.',
        '# Раскомментируйте и заполните. Пустая строка "" в [fields] убирает скобку целиком',
        '# (вариант не выбран, пометка снята). Другое значение для одного места: "date#2" = "...".',
        '# Поля {{…}} оператора берутся из operator.toml, сюда их не пишут.',
        *(
            f'# ВНИМАНИЕ: стр. {item.line} — незакрытая "[" в шаблоне; договор не соберётся, пока её не исправить.'
            for item in scanned
            if item.kind == 'unclosed'
        ),
        '',
    ]
    semantic_present = {by_text[key]: key for key in counts if key in by_text}
    client_names: tuple[ClientKey, ...] = (
        'name',
        'legal_form',
        'country',
        'registration_number',
        'address',
        'representative',
        'business_name',
    )
    agreement_names: tuple[AgreementKey, ...] = ('number', 'date')
    lines.append('[client]')
    lines += [
        f'# {name} = ""  # [{semantic_present[name]}] ×{counts[semantic_present[name]]}'
        for name in client_names
        if name in semantic_present
    ]
    lines += ['', '[agreement]']
    lines += [
        f'# {name} = ""  # [{semantic_present[name]}] ×{counts[semantic_present[name]]}'
        for name in agreement_names
        if name in semantic_present
    ]
    lines += ['', '[fields]']
    titles: dict[PlaceholderKind, str] = {
        'field': '# --- поля и варианты на выбор ---',
        'review': '# --- пометки юриста/бухгалтера (или флаг --drop-review-notes после проверки) ---',
        'verify': '# --- пометки VERIFY/ПРОВЕРИТЬ: "" только когда обещание правда ---',
    }
    for kind, title in titles.items():
        seen: set[str] = set()
        block = [title]
        for item in placeholders:
            if item.kind != kind or item.key in by_text:
                continue
            if item.key in PER_PLACE_KEYS and counts[item.key] > 1:
                # Сумма в каждом месте своя: заготовка сразу по местам.
                block.append(f'# стр. {item.line}, место {item.occurrence} из {counts[item.key]}')
                block.append(f'# {_toml_string(f"{item.key}#{item.occurrence}")} = ""')
                continue
            if item.key in seen:
                continue
            seen.add(item.key)
            block.append(f'# стр. {item.line}' + (f' (×{counts[item.key]})' if counts[item.key] > 1 else ''))
            block.append(f'# {_toml_string(item.key)} = ""')
        if len(block) > 1:
            lines += [*block, '']
    return '\n'.join(lines).rstrip() + '\n'

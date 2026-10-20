"""Строки вкладок «Inbox» и «Invoices» в таблице клиента и маскирование персональных данных.

Формулы в ячейках (тема `=HYPERLINK(...)`) обезвреживает сам `TableSink` ядра;
здесь — только формат и маскирование, которое клиент включает в конфиге.

Суммы сверенного счёта пишутся числами (`Decimal`): клиент складывает и фильтрует их в
таблице без VALUE(). Суммы НЕ сверенного счёта пишутся текстом со знаком «⚠»: СУММ() по
колонке их не подхватит, и непроверенное число не попадёт в итог как проверенное.
"""

import datetime as dt
import re
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from autostudio.inbox import texts
from autostudio.inbox.invoices import InvoiceRecord
from autostudio.inbox.mail import ParsedEmail
from autostudio.inbox.triage import TriageResult
from autostudio.sinks import Cell

_DIGIT: Final = re.compile(r'\d')
_KEEP_DIGITS: Final = 2


def mask_email(address: str) -> str:
    """`sarah.mitchell@acme.com` → `s***@acme.com`: домен остаётся — по нему видно компанию."""
    local, at, domain = address.partition('@')
    if not at:
        return '***'
    return f'{local[:1]}***@{domain}'


def mask_phone(phone: str) -> str:
    """Оставляет только последние две цифры: `+995 599 12 34 56` → `+*** *** ** ** 56`."""
    total = len(_DIGIT.findall(phone))
    seen = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal seen
        seen += 1
        return match.group(0) if seen > total - _KEEP_DIGITS else '*'

    return _DIGIT.sub(repl, phone)


def mask_name(name: str) -> str:
    """`Sarah Mitchell` → `S. M.`."""
    return ' '.join(f'{part[0]}.' for part in name.split() if part)


def sender_cell(email: ParsedEmail, mask: bool) -> str:
    """Отправитель для таблицы: «Имя <адрес>», замаскированный, если клиент так решил."""
    address = mask_email(email.from_addr) if mask and email.from_addr else email.from_addr
    if not email.from_name:
        return address
    name = mask_name(email.from_name) if mask else email.from_name
    return f'{name} <{address}>'


def build_row(
    email: ParsedEmail,
    triage: TriageResult,
    *,
    zone: ZoneInfo,
    language: str,
    mask: bool,
    draft: str,
    notes: list[str],
) -> list[str]:
    """Строка таблицы для разобранного письма.

    Args:
        email: Письмо.
        triage: Проверенный разбор.
        zone: Часовой пояс клиента — время получения показывается в нём.
        language: Язык клиента (подписи «да/нет», срочность).
        mask: Маскировать адрес, имя и телефон.
        draft: Значение колонки «Черновик».
        notes: Отметки (инъекция, правила, удалённые поля).

    Returns:
        Значения ячеек в порядке `texts.sheet_header`.
    """
    phone = triage.phone or ''
    contact = triage.email or ''
    name = triage.sender_name or ''
    if mask:
        phone = mask_phone(phone) if phone else ''
        contact = mask_email(contact) if contact else ''
        name = mask_name(name) if name else ''
    return [
        email.date.astimezone(zone).strftime('%Y-%m-%d %H:%M'),
        sender_cell(email, mask),
        email.subject,
        triage.category,
        texts.text(language, f'urgency_{triage.urgency}'),
        triage.summary,
        name,
        triage.company or '',
        phone,
        contact,
        triage.intent or '',
        triage.budget or '',
        triage.language,
        draft,
        '; '.join(notes),
    ]


def failure_row(
    email: ParsedEmail | None, uid: int, *, now: dt.datetime, zone: ZoneInfo, language: str, mask: bool
) -> list[str]:
    """Строка для письма, которое не удалось разобрать после повторов: владелец увидит его и прочитает сам."""
    manual = texts.text(language, 'flag_manual')
    if email is None:
        summary = texts.text(language, 'unreadable', uid=str(uid))
        return [now.astimezone(zone).strftime('%Y-%m-%d %H:%M'), '', '', '', '', summary, *[''] * 7, '', manual]
    return [
        email.date.astimezone(zone).strftime('%Y-%m-%d %H:%M'),
        sender_cell(email, mask),
        email.subject,
        '',
        '',
        texts.text(language, 'manual'),
        *[''] * 7,
        texts.text(language, 'no'),
        manual,
    ]


_UNVERIFIED_MARK: Final = '⚠ '


def _money(value: Decimal | None, verified: bool) -> Cell:
    if value is None:
        return ''
    return value if verified else f'{_UNVERIFIED_MARK}{value}'


def invoice_row(record: InvoiceRecord, *, zone: ZoneInfo, language: str) -> list[Cell]:
    """Строка вкладки «Invoices».

    Args:
        record: Сверенный (или не сверенный) счёт.
        zone: Часовой пояс клиента.
        language: Язык клиента.

    Returns:
        Значения ячеек в порядке `texts.invoice_header`.
    """
    verified = record.status == 'verified'
    notes = texts.invoice_reasons(language, record.reasons)
    if record.tax_included:
        notes.append(texts.text(language, 'note_tax_included'))
    read = record.status != 'manual'
    return [
        record.received.astimezone(zone).strftime('%Y-%m-%d %H:%M'),
        record.sender,
        record.file,
        texts.text(language, f'invoice_{record.status}'),
        record.vendor or '',
        record.invoice_number or '',
        record.invoice_date.isoformat() if record.invoice_date else '',
        record.due_date.isoformat() if record.due_date else '',
        record.currency or '',
        _money(record.total, verified),
        _money(record.tax, verified),
        _money(record.lines_sum, verified),
        # Расхождение посчитал код, а не модель: это число, даже у не сверенного счёта.
        record.delta if record.delta is not None else '',
        record.line_count if read else '',
        '; '.join(notes),
    ]

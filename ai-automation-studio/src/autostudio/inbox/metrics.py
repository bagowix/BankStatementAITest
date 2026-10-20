"""Как работа разбора почты выглядит в месячном отчёте клиенту.

Минуты — честная оценка ручной работы на одно действие: прочитать письмо, понять,
что это, и занести в таблицу ≈ 2 минуты; набросать ответ по шаблону ≈ 4 минуты;
отметить лид ≈ 1 минута; смахнуть рассылку или автоответ ≈ 0,5 минуты; открыть PDF-счёт,
перепечатать поставщика, номер, даты, итог и строки в таблицу и сложить их ≈ 5 минут.
Заблокированные инъекции, сбои и НЕ сверенные счета времени не экономят — их минуты 0:
не сверенный счёт человек всё равно проверяет руками.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Final

from autostudio.events import Event
from autostudio.report import MetricSpec

METRICS: Final[list[MetricSpec]] = [
    MetricSpec(
        kind='inbox.triaged',
        label={'en': 'Emails sorted and logged to the sheet', 'ru': 'Писем разобрано и занесено в таблицу'},
        minutes_saved=2,
    ),
    MetricSpec(
        kind='inbox.filtered',
        label={'en': 'Newsletters and auto-replies filtered', 'ru': 'Рассылок и автоответов отфильтровано'},
        minutes_saved=0.5,
    ),
    MetricSpec(
        kind='inbox.draft_created',
        label={'en': 'Reply drafts prepared', 'ru': 'Черновиков ответа подготовлено'},
        minutes_saved=4,
    ),
    MetricSpec(
        kind='inbox.lead_found',
        label={'en': 'Leads found', 'ru': 'Лидов найдено'},
        minutes_saved=1,
    ),
    MetricSpec(
        kind='inbox.injection_blocked',
        label={'en': 'Suspicious emails blocked', 'ru': 'Подозрительных писем заблокировано'},
        minutes_saved=0,
    ),
    MetricSpec(
        kind='inbox.invoice_extracted',
        label={'en': 'Invoices extracted and reconciled', 'ru': 'Счетов извлечено и сверено'},
        minutes_saved=5,
    ),
    MetricSpec(
        kind='inbox.invoice_unverified',
        label={'en': 'Invoices flagged for a manual check', 'ru': 'Счетов отмечено на ручную сверку'},
        minutes_saved=0,
    ),
    MetricSpec(
        kind='inbox.failed',
        label={'en': 'Emails left for manual review', 'ru': 'Писем оставлено на ручную проверку'},
        minutes_saved=0,
    ),
]

# Английские строки — отдельно для одного и для многих: «1 emails were blocked» в отчёте,
# за который клиент платит, выглядит небрежно. Русские построены как «Название: число» —
# так склонение не нужно вовсе.
_TEXT: Final[dict[str, dict[str, str]]] = {
    'en': {
        'leads_one': '1 new lead found in the inbox{urgent}.',
        'leads_one_urgent': ' (urgent)',
        'leads': '{n} new leads found in the inbox, {high} of them urgent.',
        'drafts_one': 'A reply draft was ready for the one email that needed an answer (100%).',
        'drafts': 'Reply drafts were ready for {drafts} of {needed} emails that needed an answer ({share}%).',
        'hours': 'About {hours} h of manual email work saved (estimate).',
        'injections_one': '1 email with hidden instructions to the AI was blocked — no action was taken on it.',
        'injections': '{n} emails with hidden instructions to the AI were blocked — no action was taken on them.',
        'invoices': 'Invoices from PDF attachments: {ok} reconciled automatically, {check} flagged for a manual check.',
    },
    'ru': {
        'leads_one': 'Найдено новых лидов: 1{urgent}.',
        'leads_one_urgent': ', срочный',
        'leads': 'Найдено новых лидов: {n}, из них срочных: {high}.',
        'drafts_one': 'Писем, ждавших ответа: 1; черновик был готов (100%).',
        'drafts': 'Писем, ждавших ответа: {needed}; черновик был готов для {drafts} ({share}%).',
        'hours': 'Сэкономлено около {hours} ч ручной работы с почтой (оценка).',
        'injections_one': 'Заблокировано писем со скрытыми инструкциями для ИИ: 1 — по нему ничего не делалось.',
        'injections': 'Заблокировано писем со скрытыми инструкциями для ИИ: {n} — по ним ничего не делалось.',
        'invoices': 'Счета из PDF-вложений: сверено автоматически — {ok}, отмечено на ручную сверку — {check}.',
    },
}


def highlights(events: Sequence[Event], language: str) -> list[str]:
    """Главное за месяц для отчёта клиенту.

    Args:
        events: События пакета inbox за месяц.
        language: Язык клиента.

    Returns:
        Строки «главного»; пусто, если за месяц писем не было.
    """
    t = _TEXT.get(language, _TEXT['en'])
    counts = Counter(event.kind for event in events)
    out: list[str] = []
    leads = [event for event in events if event.kind == 'inbox.lead_found']
    high = sum(event.payload.get('urgency') == 'high' for event in leads)
    if len(leads) == 1:
        out.append(t['leads_one'].format(urgent=t['leads_one_urgent'] if high else ''))
    elif leads:
        out.append(t['leads'].format(n=len(leads), high=high))
    needed = sum(event.kind == 'inbox.triaged' and event.payload.get('needs_reply') == 'yes' for event in events)
    drafts = counts['inbox.draft_created']
    if needed == 1 and drafts:
        out.append(t['drafts_one'])
    elif needed and drafts:
        share = round(100 * min(drafts, needed) / needed)
        out.append(t['drafts'].format(drafts=drafts, needed=needed, share=share))
    minutes = sum(counts[spec.kind] * spec.minutes_saved for spec in METRICS)
    if minutes:
        out.append(t['hours'].format(hours=f'{minutes / 60:.1f}'))
    invoices_ok, invoices_check = counts['inbox.invoice_extracted'], counts['inbox.invoice_unverified']
    if invoices_ok or invoices_check:
        out.append(t['invoices'].format(ok=invoices_ok, check=invoices_check))
    blocked = counts['inbox.injection_blocked']
    if blocked == 1:
        out.append(t['injections_one'])
    elif blocked:
        out.append(t['injections'].format(n=blocked))
    return out

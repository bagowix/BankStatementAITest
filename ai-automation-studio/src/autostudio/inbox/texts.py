"""Тексты, которые видит владелец бизнеса: таблица, уведомления, пометки в черновике.

Каждый текст есть на английском и русском; язык выбирает `client.language`,
незнакомый язык — английский. Тексты для оператора (фрилансера) — по-русски, как
и сигналы ядра о сбоях.
"""

from typing import Final

_TEXTS: Final[dict[str, dict[str, str]]] = {
    'en': {
        'auto_reply': 'Auto-reply or delivery notice — no action needed.',
        'bulk': 'Newsletter or bulk mailing — no action needed.',
        'manual': 'Could not be processed automatically — please read it yourself.',
        'unreadable': 'Unreadable message (UID {uid}) — please check the mailbox.',
        'yes': 'yes',
        'no': 'no',
        'draft_check': 'yes — check numbers',
        'flag_injection': 'suspicious instructions inside — no actions taken',
        'flag_prefilter': 'sorted by rules, no AI',
        'flag_manual': 'needs manual review',
        'flag_dropped': 'unverified fields removed: {fields}',
        'flag_low_confidence': 'AI was unsure — please check the category',
        'flag_quiet': 'older than a day at go-live — no draft or alert',
        'flag_failed_notify': 'alert to you was not delivered — please reply yourself',
        'flag_failed_draft': 'reply draft could not be saved — please reply yourself',
        'flag_failed_model': 'reply draft not prepared (AI unavailable) — please reply yourself',
        'flag_failed_invoice': 'invoice PDF was not processed (AI unavailable) — please check it yourself',
        'flag_failed_copy': 'could not copy to folder {folder}',
        'flag_failed_other': 'not every step finished — please check this email yourself',
        'flag_vanished': 'left the folder before processing finished',
        'flag_operator_failed': 'security alert to the operator was not delivered',
        'field_category': 'category',
        'field_phone': 'phone',
        'field_email': 'email',
        'field_budget': 'budget',
        'field_sender_name': 'name',
        'field_company': 'company',
        'urgency_low': 'low',
        'urgency_normal': 'normal',
        'urgency_high': 'high',
        'hot_title': '{icon} {category} from {who} ({urgency} urgency)',
        'hot_phone': 'Phone: {phone}',
        'hot_budget': 'Budget: {budget}',
        'hot_draft': 'A reply draft is waiting in Drafts.',
        'hot_no_draft': 'No draft was prepared — please reply yourself.',
        'numbers_warning': (
            '⚠ CHECK BEFORE SENDING — not in your company facts: {numbers}. Delete this line when checked.'
        ),
        'flag_invoice_ok': 'invoice checked: lines and total match (Invoices tab)',
        'flag_invoice_check': 'invoice NOT verified — see the Invoices tab',
        'invoice_verified': 'verified',
        'invoice_unverified': 'NOT verified — check by hand',
        'invoice_manual': 'not read — open the PDF',
        'reason_no_total': 'no total found',
        'reason_total_not_in_pdf': 'this total is not printed in the PDF',
        'reason_tax_not_in_pdf': 'this tax amount is not printed in the PDF',
        'reason_lines_sum': 'lines do not add up to the total',
        'reason_lines_incomplete': 'some lines have no amount',
        'reason_no_lines': 'no line items extracted — nothing to check the total against',
        'reason_line_not_in_pdf': 'a line amount is not printed in the PDF',
        'reason_single_line_is_total': 'the only line is the total itself — no line amounts printed, '
        'nothing to check the total against',
        'reason_line_amount_reused': 'a line repeats an amount printed only once (as the total, tax or another line)',
        'reason_total_is_subtotal': 'this total looks like a subtotal: the PDF also shows it plus another amount',
        'reason_bad_date': 'a date could not be read',
        'reason_due_before_issue': 'due date is before the invoice date',
        'reason_number_not_in_pdf': 'this invoice number is not printed in the PDF',
        'reason_no_text': 'scanned PDF without a text layer — needs OCR or manual entry',
        'reason_encrypted': 'password-protected PDF',
        'reason_unreadable': 'PDF could not be read (damaged or cut off)',
        'reason_too_large': 'PDF is too large to process',
        'reason_too_many_pages': 'too many pages for an invoice',
        'reason_suspicious': 'suspicious instructions inside the PDF — not processed',
        'note_tax_included': 'VAT is included in the line prices',
    },
    'ru': {
        'auto_reply': 'Автоответ или уведомление о доставке — делать ничего не нужно.',
        'bulk': 'Рассылка — делать ничего не нужно.',
        'manual': 'Автоматически разобрать не удалось — прочитайте письмо сами.',
        'unreadable': 'Письмо не читается (UID {uid}) — проверьте ящик.',
        'yes': 'да',
        'no': 'нет',
        'draft_check': 'да — проверьте цифры',
        'flag_injection': 'внутри подозрительные инструкции — никаких действий',
        'flag_prefilter': 'разобрано правилами, без ИИ',
        'flag_manual': 'нужна ручная проверка',
        'flag_dropped': 'удалены непроверенные поля: {fields}',
        'flag_low_confidence': 'ИИ не уверен — проверьте категорию',
        'flag_quiet': 'письмо старше суток на момент подключения — без черновика и сигнала',
        'flag_failed_notify': 'сообщение вам не доставлено — ответьте, пожалуйста, сами',
        'flag_failed_draft': 'черновик не сохранился в ящике — ответьте, пожалуйста, сами',
        'flag_failed_model': 'черновик не подготовлен (ИИ недоступен) — ответьте, пожалуйста, сами',
        'flag_failed_invoice': 'счёт из PDF не разобран (ИИ недоступен) — проверьте его сами',
        'flag_failed_copy': 'не удалось скопировать в папку {folder}',
        'flag_failed_other': 'не все шаги выполнены — проверьте письмо сами',
        'flag_vanished': 'письмо ушло из папки до конца обработки',
        'flag_operator_failed': 'сигнал оператору о подозрительном письме не доставлен',
        'field_category': 'категория',
        'field_phone': 'телефон',
        'field_email': 'e-mail',
        'field_budget': 'бюджет',
        'field_sender_name': 'имя',
        'field_company': 'компания',
        'urgency_low': 'низкая',
        'urgency_normal': 'обычная',
        'urgency_high': 'высокая',
        'hot_title': '{icon} {category} от {who} (срочность: {urgency})',
        'hot_phone': 'Телефон: {phone}',
        'hot_budget': 'Бюджет: {budget}',
        'hot_draft': 'Черновик ответа ждёт в «Черновиках».',
        'hot_no_draft': 'Черновик не готовился — ответьте, пожалуйста, сами.',
        'numbers_warning': (
            '⚠ ПРОВЕРЬТЕ ПЕРЕД ОТПРАВКОЙ — этого нет в фактах компании: {numbers}. Удалите эту строку после проверки.'
        ),
        'flag_invoice_ok': 'счёт сверен: строки и итог сходятся (вкладка Invoices)',
        'flag_invoice_check': 'счёт НЕ сверен — см. вкладку Invoices',
        'invoice_verified': 'сверен',
        'invoice_unverified': 'НЕ сверен — проверьте вручную',
        'invoice_manual': 'не разобран — откройте PDF',
        'reason_no_total': 'итог не найден',
        'reason_total_not_in_pdf': 'такого итога нет в тексте PDF',
        'reason_tax_not_in_pdf': 'такой суммы налога нет в тексте PDF',
        'reason_lines_sum': 'строки не сходятся с итогом',
        'reason_lines_incomplete': 'у части строк нет суммы',
        'reason_no_lines': 'строки счёта не извлечены — итог не с чем сверить',
        'reason_line_not_in_pdf': 'суммы одной из строк нет в тексте PDF',
        'reason_single_line_is_total': 'единственная строка — это сам итог: сумм строк в PDF нет, итог не с чем '
        'сверить',
        'reason_line_amount_reused': 'строка повторяет сумму, напечатанную в PDF один раз (итог, налог или другая '
        'строка)',
        'reason_total_is_subtotal': 'похоже на подытог: в PDF есть и эта сумма плюс другая (налог, доставка)',
        'reason_bad_date': 'дата не разбирается',
        'reason_due_before_issue': 'срок оплаты раньше даты счёта',
        'reason_number_not_in_pdf': 'такого номера счёта нет в тексте PDF',
        'reason_no_text': 'скан без текстового слоя — нужен OCR или ручной ввод',
        'reason_encrypted': 'PDF защищён паролем',
        'reason_unreadable': 'PDF не читается (повреждён или обрезан)',
        'reason_too_large': 'PDF слишком большой для разбора',
        'reason_too_many_pages': 'слишком много страниц для счёта',
        'reason_suspicious': 'в PDF подозрительные инструкции — не разбирался',
        'note_tax_included': 'НДС включён в цены строк',
    },
}

_HEADERS: Final[dict[str, list[str]]] = {
    'en': [
        'Received', 'From', 'Subject', 'Category', 'Urgency', 'Summary', 'Name', 'Company', 'Phone', 'Email',
        'Intent', 'Budget', 'Language', 'Draft', 'Notes',
    ],
    'ru': [
        'Получено', 'От', 'Тема', 'Категория', 'Срочность', 'Кратко', 'Имя', 'Компания', 'Телефон', 'Email',
        'Запрос', 'Бюджет', 'Язык', 'Черновик', 'Отметки',
    ],
}  # fmt: skip

_INVOICE_HEADERS: Final[dict[str, list[str]]] = {
    'en': [
        'Received', 'From', 'File', 'Status', 'Vendor', 'Invoice #', 'Invoice date', 'Due date', 'Currency',
        'Total', 'Tax', 'Lines sum', 'Difference', 'Lines', 'Notes',
    ],
    'ru': [
        'Получено', 'От', 'Файл', 'Статус', 'Поставщик', 'Номер счёта', 'Дата счёта', 'Оплатить до', 'Валюта',
        'Итого', 'Налог', 'Сумма строк', 'Расхождение', 'Строк', 'Отметки',
    ],
}  # fmt: skip


def lang(language: str) -> str:
    """Язык, на котором у нас есть тексты: `ru` или `en` для всех остальных."""
    return language if language in _TEXTS else 'en'


def text(language: str, key: str, **values: str) -> str:
    """Текст для владельца на его языке.

    Args:
        language: Язык клиента.
        key: Ключ текста.
        **values: Подстановки.

    Returns:
        Готовая строка.
    """
    template = _TEXTS[lang(language)][key]
    return template.format(**values) if values else template


def sheet_header(language: str) -> list[str]:
    """Заголовок вкладки таблицы на языке клиента."""
    return list(_HEADERS[lang(language)])


def invoice_header(language: str) -> list[str]:
    """Заголовок вкладки счетов на языке клиента."""
    return list(_INVOICE_HEADERS[lang(language)])


def invoice_reasons(language: str, reasons: list[str]) -> list[str]:
    """Причины «не сверено» словами владельца; незнакомый код остаётся кодом."""
    known = _TEXTS[lang(language)]
    return [known.get(f'reason_{reason}', reason) for reason in reasons]


def field_names(language: str, fields: list[str]) -> str:
    """Названия полей разбора для отметки в таблице: `phone` → «телефон» у русскоязычного владельца."""
    known = _TEXTS[lang(language)]
    return ', '.join(known.get(f'field_{field}', field) for field in fields)


def operator_injection(client: str, domain: str, subject: str, markers: list[str]) -> str:
    """Сигнал оператору о письме с подозрением на prompt-инъекцию (без текста письма)."""
    found = '; '.join(markers[:3]) or 'модель пометила письмо'
    return (
        f'🛡 {client} / inbox: письмо от домена {domain or "?"} похоже на prompt-инъекцию '
        f'(тема: «{subject[:80]}»; признаки: {found}). Черновик не создан, действий не было.'
    )


def operator_gave_up(client: str, uid: int, error: str) -> str:
    """Сигнал оператору: письмо не разобрано после нескольких попыток."""
    return (
        f'⚠️ {client} / inbox: письмо UID {uid} не разобрано после повторов ({error[:200]}). '
        'В таблице — строка «нужна ручная проверка».'
    )


def operator_degraded(client: str, uid: int, step: str, error: str) -> str:
    """Сигнал оператору: письмо разобрано, но один шаг (уведомление, черновик, копия) так и не прошёл."""
    return (
        f'⚠️ {client} / inbox: письмо UID {uid} разобрано, но шаг «{step}» не выполнен ({error[:200]}). '
        'Разбор записан в таблицу с пометкой — проверьте канал или папку.'
    )

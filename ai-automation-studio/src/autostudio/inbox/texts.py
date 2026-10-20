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
        'urgency_low': 'low',
        'urgency_normal': 'normal',
        'urgency_high': 'high',
        'hot_title': '🔥 New {category} ({urgency}) from {who}',
        'hot_phone': 'Phone: {phone}',
        'hot_budget': 'Budget: {budget}',
        'hot_draft': 'A reply draft is waiting in Drafts.',
        'hot_no_draft': 'No draft was prepared — please reply yourself.',
        'numbers_warning': (
            '⚠ CHECK BEFORE SENDING — not in your company facts: {numbers}. Delete this line when checked.'
        ),
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
        'urgency_low': 'низкая',
        'urgency_normal': 'обычная',
        'urgency_high': 'высокая',
        'hot_title': '🔥 Новое письмо «{category}» (срочность: {urgency}) от {who}',
        'hot_phone': 'Телефон: {phone}',
        'hot_budget': 'Бюджет: {budget}',
        'hot_draft': 'Черновик ответа ждёт в «Черновиках».',
        'hot_no_draft': 'Черновик не готовился — ответьте, пожалуйста, сами.',
        'numbers_warning': (
            '⚠ ПРОВЕРЬТЕ ПЕРЕД ОТПРАВКОЙ — этого нет в фактах компании: {numbers}. Удалите эту строку после проверки.'
        ),
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

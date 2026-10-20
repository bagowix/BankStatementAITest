"""Тексты, которые видит владелец бизнеса: таблица и уведомления, на английском и русском.

Тексты оператору (фрилансеру) — по-русски, как и сообщения ядра: это рабочий канал,
а не витрина. Всё, что уходит клиенту, — коротко, дружелюбно и на языке из конфига.
"""

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

LANGUAGES: Final = ('en', 'ru')


def lang(language: str) -> str:
    """Язык клиента или английский, если перевода нет (`ka` и т.п.)."""
    return language if language in LANGUAGES else 'en'


TABS: Final[dict[str, dict[str, str]]] = {
    'en': {'now': 'Prices now', 'history': 'History', 'changes': 'Changes'},
    'ru': {'now': 'Цены сейчас', 'history': 'История', 'changes': 'Изменения'},
}

HEADERS: Final[dict[str, dict[str, list[str]]]] = {
    'en': {
        'now': [
            'Product',
            'Your price',
            'Competitor',
            'Price',
            'Currency',
            'Diff vs yours, %',
            'Change vs last, %',
            'Availability',
            'Source',
            'Checked at',
            'Status',
            'URL',
        ],
        'history': [
            'Checked at',
            'Product',
            'Competitor',
            'Price',
            'Currency',
            'Availability',
            'Source',
            'Text on page',
            'URL',
        ],
        'changes': [
            'Checked at',
            'Product',
            'Competitor',
            'What changed',
            'Was',
            'Now',
            'Change, %',
            'Your price',
            'URL',
        ],
    },
    'ru': {
        'now': [
            'Товар',
            'Ваша цена',
            'Конкурент',
            'Цена',
            'Валюта',
            'Разница с вашей, %',
            'Изменение, %',
            'Наличие',
            'Источник',
            'Проверено',
            'Статус',
            'Ссылка',
        ],
        'history': [
            'Проверено',
            'Товар',
            'Конкурент',
            'Цена',
            'Валюта',
            'Наличие',
            'Источник',
            'Текст на странице',
            'Ссылка',
        ],
        'changes': [
            'Проверено',
            'Товар',
            'Конкурент',
            'Что изменилось',
            'Было',
            'Стало',
            'Изменение, %',
            'Ваша цена',
            'Ссылка',
        ],
    },
}

METHODS: Final[dict[str, dict[str, str]]] = {
    'en': {
        'selector': 'page element',
        'json_ld': 'schema.org data',
        'microdata': 'schema.org markup',
        'meta': 'page meta tags',
        'llm': 'AI, verified quote',
    },
    'ru': {
        'selector': 'элемент страницы',
        'json_ld': 'данные schema.org',
        'microdata': 'разметка schema.org',
        'meta': 'meta-теги страницы',
        'llm': 'ИИ, проверенная цитата',
    },
}

AVAILABILITY: Final[dict[str, dict[str, str]]] = {
    'en': {'in_stock': 'in stock', 'out_of_stock': 'out of stock', 'unknown': ''},
    'ru': {'in_stock': 'в наличии', 'out_of_stock': 'нет в наличии', 'unknown': ''},
}

# Причины сбоя простыми словами: клиенту не нужны коды, ему нужно понять, что происходит.
FAILURES: Final[dict[str, dict[str, str]]] = {
    'en': {
        'robots_disallowed': 'skipped: the site asks robots not to visit this page',
        'http_error': 'page unavailable',
        'redirected_away': 'page removed: the link now leads elsewhere',
        'unavailable': 'the site did not respond',
        'blocked_url': 'address not allowed',
        'unreadable': 'page could not be read',
        'no_price': 'price not found on the page',
        'internal_error': 'check failed',
    },
    'ru': {
        'robots_disallowed': 'пропущено: сайт просит роботов не заходить на эту страницу',
        'http_error': 'страница недоступна',
        'redirected_away': 'страница удалена: ссылка ведёт в другое место',
        'unavailable': 'сайт не ответил',
        'blocked_url': 'адрес не разрешён',
        'unreadable': 'страницу не удалось прочитать',
        'no_price': 'цена на странице не найдена',
        'internal_error': 'проверка не удалась',
    },
}

_FAILURE_GROUPS: Final[dict[str, str]] = {
    'rate_limited': 'unavailable',
    'server_error': 'unavailable',
    'timeout': 'unavailable',
    'network': 'unavailable',
    'dns': 'unavailable',
    'too_large': 'unreadable',
    'not_html': 'unreadable',
    'too_many_redirects': 'unreadable',
}

STATUS: Final[dict[str, dict[str, str]]] = {
    'en': {
        'ok': 'ok',
        'from': 'ok: a “from” price',
        'range': 'ok: lowest price of a range',
        'free': 'ok: free',
        'stale': '{reason}; showing the last known price',
        'na_currency': 'n/a: {theirs} ≠ {yours}',
    },
    'ru': {
        'ok': 'ок',
        'from': 'ок: цена «от»',
        'range': 'ок: нижняя цена диапазона',
        'free': 'ок: бесплатно',
        'stale': '{reason}; показана последняя известная цена',
        'na_currency': 'н/д: {theirs} ≠ {yours}',
    },
}

CHANGE_KINDS: Final[dict[str, dict[str, str]]] = {
    'en': {
        'price': 'price change',
        'undercut': 'cheaper than you',
        'stock': 'availability',
        'currency': 'currency',
    },
    'ru': {
        'price': 'изменение цены',
        'undercut': 'дешевле вас',
        'stock': 'наличие',
        'currency': 'валюта',
    },
}

DIGEST: Final[dict[str, dict[str, str]]] = {
    'en': {
        'title': '📈 Competitor prices — {client} · {when}',
        'price': 'Price changes:',
        'price_line': '• {product} — {competitor}: {old} → {new} {currency}{pct}',
        'undercut': 'Cheaper than you:',
        'undercut_line': '• {product} — {competitor}: {price} {currency}, yours {own} ({pct}%)',
        'stock': 'Availability:',
        'stock_out': '• {product} — {competitor}: out of stock',
        'stock_back': '• {product} — {competitor}: back in stock',
        'currency': 'Currency:',
        'currency_line': '• {product} — {competitor}: now in {new} (was {old}), not compared with your price',
        'unchecked': '{failed} of {total} pages could not be checked this time — we are on it.',
        'sheet': 'Full table: {url}',
    },
    'ru': {
        'title': '📈 Цены конкурентов — {client} · {when}',
        'price': 'Изменения цен:',
        'price_line': '• {product} — {competitor}: {old} → {new} {currency}{pct}',
        'undercut': 'Дешевле вас:',
        'undercut_line': '• {product} — {competitor}: {price} {currency}, у вас {own} ({pct}%)',
        'stock': 'Наличие:',
        'stock_out': '• {product} — {competitor}: нет в наличии',
        'stock_back': '• {product} — {competitor}: снова в наличии',
        'currency': 'Валюта:',
        'currency_line': '• {product} — {competitor}: теперь в {new} (было {old}), с вашей ценой не сравниваем',
        'unchecked': 'Не удалось проверить {failed} из {total} страниц — уже разбираемся.',
        'sheet': 'Вся таблица: {url}',
    },
}


def failure_text(reason: str, language: str) -> str:
    """Причина сбоя для клиента."""
    texts = FAILURES[lang(language)]
    if reason in texts:
        return texts[reason]
    group = _FAILURE_GROUPS.get(reason)
    if group is not None:
        return texts[group]
    # selector_*, llm_* и прочее, что сводится к «цены на странице не нашли».
    return texts['no_price']


def cell_amount(amount: Decimal) -> str:
    """Сумма для ячейки таблицы: точка, без разделителей тысяч, два знака (если их не больше)."""
    cents = amount.quantize(Decimal('0.01'))
    return f'{cents:.2f}' if cents == amount else format(amount, 'f')


def cell_pct(value: Decimal) -> str:
    """Процент для ячейки: один знак после точки, без «+» (иначе таблица примет его за формулу)."""
    return f'{value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP):.1f}'


def show_amount(amount: Decimal, language: str) -> str:
    """Сумма для сообщения: «2,599» / «2 599», копейки — только если они есть."""
    cents = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = f'{cents:,.0f}' if cents == cents.to_integral_value() else f'{cents:,.2f}'
    if lang(language) == 'ru':
        text = text.replace(',', ' ').replace('.', ',')
    return text


def show_pct(value: Decimal) -> str:
    """Процент для сообщения со знаком: «−5.5», «+3.2»."""
    rounded = value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
    if rounded > 0:
        return f'+{rounded:.1f}'
    return f'{rounded:.1f}'.replace('-', '−')


def show_time(moment: dt.datetime, zone: dt.tzinfo) -> str:
    """Время в часовом поясе клиента."""
    return moment.astimezone(zone).strftime('%Y-%m-%d %H:%M')

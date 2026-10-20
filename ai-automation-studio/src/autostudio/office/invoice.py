"""Счёт прямому клиенту: позиции, итог в Decimal, печатный HTML и короткая сводка в Markdown.

Счёт — документ для налоговой и для банка клиента, поэтому формулировка услуг
выбрана под режим малого бизнеса: «разработка, настройка, интеграция и
сопровождение ПО», никогда не «консалтинг» (консалтинг из режима 1% исключён,
бриф 6.2). Код отказывается выпускать счёт со словом «consulting» в позициях.
"""

import datetime as dt
import html
import re
import tomllib
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

import msgspec

from autostudio.errors import ConfigError
from autostudio.office.money import (
    InvoiceCurrency,
    Language,
    format_amount,
    format_money,
    invoice_currency,
    money,
    parse_amount,
)
from autostudio.office.operator import OperatorProfile

type Preset = Literal['setup', 'support']
type InvoiceStatus = Literal['issued', 'paid', 'void']

DEFAULT_DUE_DAYS: Final = 7
MAX_DUE_DAYS: Final = 120
# Формулировки по умолчанию — те, что в договоре (1.1) и в вопросе бухгалтеру (бриф 6.3).
PRESET_WORDING: Final[dict[Language, dict[Preset, str]]] = {
    'en': {
        'setup': 'Software development, configuration and integration services',
        'support': 'Technical support and maintenance of software (monthly)',
    },
    'ru': {
        'setup': 'Услуги по разработке, настройке и интеграции программного обеспечения',
        'support': 'Техническая поддержка и сопровождение программного обеспечения (ежемесячно)',
    },
}
# Слова, из-за которых работу могут счесть консалтингом (бриф 6.2: приказ Минфина № 996).
_CONSULTING: Final = re.compile(r'consult|консульт|კონსულტ', re.IGNORECASE)
_COUNTRY: Final = re.compile(r'^[A-Z]{2}$')
# `--item "описание;количество;цена"` или `"описание;цена"`.
_FULL_ITEM: Final = 3
_SHORT_ITEM: Final = 2
_MONTH_NAMES: Final[dict[Language, tuple[str, ...]]] = {
    'en': (
        'January',
        'February',
        'March',
        'April',
        'May',
        'June',
        'July',
        'August',
        'September',
        'October',
        'November',
        'December',
    ),
    'ru': (
        'январь',
        'февраль',
        'март',
        'апрель',
        'май',
        'июнь',
        'июль',
        'август',
        'сентябрь',
        'октябрь',
        'ноябрь',
        'декабрь',
    ),
}


class ClientParty(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Кому выставлен счёт.

    Attributes:
        name: Наименование клиента.
        address: Адрес (можно в несколько строк).
        tax_id: Налоговый номер клиента или None.
        email: E-mail для счетов или None.
        country: Страна клиента, ISO-3166 из двух букв; `GE` считается в порог НДС.
    """

    name: str
    address: str = ''
    tax_id: str | None = None
    email: str | None = None
    country: str | None = None


class LineItem(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Позиция счёта.

    Attributes:
        description: Что оказано.
        quantity: Количество (часы могут быть дробными).
        unit_price: Цена за единицу в валюте счёта.
    """

    description: str
    quantity: Decimal = Decimal(1)
    unit_price: Decimal

    @property
    def amount(self) -> Decimal:
        # Округляем каждую строку: клиент сверяет итог сложением строк, и копейки должны сойтись.
        return money(self.quantity * self.unit_price)


class Invoice(msgspec.Struct, frozen=True, kw_only=True):
    """Выпущенный счёт — то, что лежит в реестре и печатается.

    Attributes:
        number: Номер `AS-2026-001`; он же назначение платежа.
        issue_date: Дата счёта (Тбилиси).
        due_date: Срок оплаты.
        currency: Валюта.
        language: Язык документа.
        client: Клиент.
        items: Позиции.
        notes: Примечание внизу счёта.
    """

    number: str
    issue_date: dt.date
    due_date: dt.date
    currency: InvoiceCurrency
    language: Language
    client: ClientParty
    items: list[LineItem]
    notes: str = ''

    @property
    def total(self) -> Decimal:
        return total_of(self.items)


class InvoiceDraft(msgspec.Struct, frozen=True, kw_only=True):
    """Счёт до номера: всё, кроме номера, который выдаёт реестр.

    Attributes:
        client: Клиент.
        items: Позиции.
        currency: Валюта.
        issue_date: Дата счёта.
        due_days: Дней на оплату.
        language: Язык документа.
        notes: Примечание.
    """

    client: ClientParty
    items: list[LineItem]
    currency: InvoiceCurrency
    issue_date: dt.date
    due_days: int = DEFAULT_DUE_DAYS
    language: Language = 'en'
    notes: str = ''

    def numbered(self, number: str) -> Invoice:
        """Счёт с номером из реестра."""
        return Invoice(
            number=number,
            issue_date=self.issue_date,
            due_date=self.issue_date + dt.timedelta(days=self.due_days),
            currency=self.currency,
            language=self.language,
            client=self.client,
            items=list(self.items),
            notes=self.notes,
        )


def validate_draft(draft: InvoiceDraft) -> None:
    """Проверяет счёт до выдачи номера: номер потом не вернуть, поэтому ошибки ловим здесь.

    Args:
        draft: Черновик счёта.

    Raises:
        ConfigError: Нет клиента или позиций, нулевой итог, неверный срок, страна или «консалтинг».
    """
    if not draft.client.name.strip():
        raise ConfigError('клиент: нужно наименование (--client-name или [client].name)')
    if draft.client.country is not None and not _COUNTRY.match(draft.client.country):
        raise ConfigError(f'страна клиента {draft.client.country!r}: две заглавные буквы ISO 3166, например GE или US')
    if not draft.items:
        raise ConfigError('в счёте нет позиций: --setup, --support, --item или [[items]] в файле')
    if not 0 <= draft.due_days <= MAX_DUE_DAYS:
        raise ConfigError(f'срок оплаты {draft.due_days} дн.: допустимо от 0 до {MAX_DUE_DAYS}')
    for item in draft.items:
        if not item.description.strip():
            raise ConfigError('позиция без описания')
        if _CONSULTING.search(item.description):
            raise ConfigError(
                f'позиция {item.description!r}: слово «consulting/консультации» в счёте — риск выпасть из режима '
                'малого бизнеса (консалтинг исключён, бриф 6.2). Пишите «software development, configuration, '
                'integration and maintenance» / «разработка, настройка, интеграция и сопровождение ПО»'
            )
        if item.quantity <= 0 or not item.quantity.is_finite():
            raise ConfigError(f'позиция {item.description!r}: количество должно быть больше нуля')
        if item.unit_price < 0 or not item.unit_price.is_finite():
            raise ConfigError(f'позиция {item.description!r}: цена не может быть отрицательной')
    if total_of(draft.items) <= 0:
        raise ConfigError('итог счёта — ноль: проверьте цены')


def preset_item(
    preset: Preset, price: str | Decimal, language: Language, *, quantity: Decimal = Decimal(1)
) -> LineItem:
    """Позиция со стандартной формулировкой: разработка/настройка или ежемесячное сопровождение.

    Args:
        preset: `setup` или `support`.
        price: Цена за единицу.
        language: Язык счёта.
        quantity: Количество (месяцев поддержки).

    Returns:
        Позиция счёта.
    """
    return LineItem(
        description=PRESET_WORDING[language][preset],
        quantity=quantity,
        unit_price=parse_amount(price, what=f'--{preset}'),
    )


def parse_item(spec: str) -> LineItem:
    """Позиция из флага `--item "описание;количество;цена"` или `"описание;цена"`.

    Описание может содержать точку с запятой: число и цена берутся справа.

    Raises:
        ConfigError: Нет цены или числа не читаются.
    """
    parts = spec.rsplit(';', 2)
    if len(parts) == _FULL_ITEM:
        description, quantity, price = parts
        try:
            qty = parse_amount(quantity, what='--item: количество')
        except ConfigError:
            # «a;b;100»: средняя часть не число — значит, это часть описания.
            description, qty, price = f'{description};{quantity}', Decimal(1), price
    elif len(parts) == _SHORT_ITEM:
        description, price = parts
        qty = Decimal(1)
    else:
        raise ConfigError(f'--item {spec!r}: ожидается "описание;количество;цена" или "описание;цена"')
    return LineItem(description=description.strip(), quantity=qty, unit_price=parse_amount(price, what='--item: цена'))


def period_suffix(month: dt.date, language: Language) -> str:
    """« — November 2026» / « — ноябрь 2026» для позиции абонентской поддержки."""
    return f' — {_MONTH_NAMES[language][month.month - 1]} {month.year}'


class _RequestItem(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    description: str | None = None
    preset: Preset | None = None
    quantity: Decimal = Decimal(1)
    unit_price: Decimal


class InvoiceRequest(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Счёт из небольшого TOML-файла (`--from-file`).

    Attributes:
        client: Клиент.
        items: Позиции; у позиции есть `description` или `preset = "setup" | "support"`.
        currency: Валюта или None — из профиля оператора.
        language: Язык документа.
        issue_date: Дата счёта или None — сегодня в Тбилиси.
        due_days: Дней на оплату.
        notes: Примечание.
    """

    client: ClientParty
    items: list[_RequestItem]
    currency: str | None = None
    language: Language = 'en'
    issue_date: dt.date | None = None
    due_days: int = DEFAULT_DUE_DAYS
    notes: str = ''

    def line_items(self, language: Language) -> list[LineItem]:
        """Позиции с подставленными стандартными формулировками."""
        items: list[LineItem] = []
        for raw in self.items:
            if raw.description:
                description = raw.description
            elif raw.preset is not None:
                description = PRESET_WORDING[language][raw.preset]
            else:
                raise ConfigError('[[items]]: нужно description или preset = "setup" | "support"')
            items.append(
                LineItem(
                    description=description,
                    quantity=parse_amount(raw.quantity, what='items.quantity'),
                    unit_price=parse_amount(raw.unit_price, what='items.unit_price', allow_zero=True),
                )
            )
        return items

    def draft(self, *, default_currency: InvoiceCurrency, today: dt.date) -> InvoiceDraft:
        """Черновик счёта с умолчаниями оператора."""
        return InvoiceDraft(
            client=self.client,
            items=self.line_items(self.language),
            currency=invoice_currency(self.currency) if self.currency else default_currency,
            issue_date=self.issue_date or today,
            due_days=self.due_days,
            language=self.language,
            notes=self.notes,
        )


def load_invoice_request(path: Path) -> InvoiceRequest:
    """Читает счёт из TOML; дробные числа — сразу Decimal, без float.

    Raises:
        ConfigError: Файла нет, TOML битый или поля не по схеме.
    """
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'), parse_float=Decimal)
    except FileNotFoundError:
        raise ConfigError(f'файл счёта не найден: {path}') from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'{path}: битый TOML: {exc}') from None
    try:
        return msgspec.convert(data, type=InvoiceRequest)
    except msgspec.ValidationError as exc:
        raise ConfigError(f'{path}: {exc}') from None


_LABELS: Final[dict[Language, dict[str, str]]] = {
    'en': {
        'title': 'Invoice',
        'number': 'Invoice No.',
        'issued': 'Issue date',
        'due': 'Due date',
        'from': 'From',
        'to': 'Bill to',
        'id': 'ID number',
        'tax_id': 'Tax ID',
        'description': 'Description',
        'qty': 'Qty',
        'unit': 'Unit price',
        'amount': 'Amount',
        'total': 'Total due',
        'pay': 'Payment details',
        'beneficiary': 'Beneficiary',
        'iban': 'IBAN',
        'bank': 'Bank',
        'swift': 'SWIFT/BIC',
        'reference': 'Payment reference',
        'fees': 'Please pay the full amount; bank charges are paid by each party for its own bank.',
        'notes': 'Notes',
        'void': 'VOID',
        'currency': 'Currency',
    },
    'ru': {
        'title': 'Счёт на оплату',
        'number': 'Счёт №',
        'issued': 'Дата',
        'due': 'Оплатить до',
        'from': 'Исполнитель',
        'to': 'Заказчик',
        'id': 'Идентификационный номер',
        'tax_id': 'Налоговый номер',
        'description': 'Описание',
        'qty': 'Кол-во',
        'unit': 'Цена',
        'amount': 'Сумма',
        'total': 'Итого к оплате',
        'pay': 'Реквизиты для оплаты',
        'beneficiary': 'Получатель',
        'iban': 'IBAN',
        'bank': 'Банк',
        'swift': 'SWIFT/BIC',
        'reference': 'Назначение платежа',
        'fees': 'Комиссию своего банка каждая сторона оплачивает сама.',
        'notes': 'Примечание',
        'void': 'АННУЛИРОВАН',
        'currency': 'Валюта',
    },
}

_CSS: Final = """
@page { size: A4; margin: 16mm 16mm 18mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #eceff1; color: #1b1f23;
  font: 10.5pt/1.45 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif; }
.sheet { width: 210mm; min-height: 297mm; margin: 10mm auto; padding: 16mm; background: #fff;
  box-shadow: 0 1px 8px rgba(0, 0, 0, .15); position: relative; }
header { display: flex; justify-content: space-between; gap: 10mm; align-items: flex-start;
  border-bottom: 2px solid #1b1f23; padding-bottom: 5mm; }
h1 { font-size: 22pt; line-height: 1.1; margin: 0 0 2mm; letter-spacing: .02em; }
.issuer { font-weight: 600; }
.meta { border-collapse: collapse; }
.meta td { padding: .4mm 0 .4mm 6mm; white-space: nowrap; }
.meta td:first-child { color: #5f6b76; padding-left: 0; }
.parties { display: grid; grid-template-columns: 1fr 1fr; gap: 10mm; margin: 7mm 0; }
.label { font-size: 8pt; text-transform: uppercase; letter-spacing: .08em; color: #5f6b76; margin-bottom: 1.5mm; }
.party p { margin: 0; }
.party .name { font-weight: 600; }
table.items { width: 100%; border-collapse: collapse; }
table.items th { text-align: left; font-size: 8pt; font-weight: 600; text-transform: uppercase; letter-spacing: .06em;
  color: #5f6b76; border-bottom: 1px solid #1b1f23; padding: 2mm 1.5mm; }
table.items td { border-bottom: 1px solid #d9dee3; padding: 2.5mm 1.5mm; vertical-align: top; }
table.items tr { break-inside: avoid; }
.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.idx { width: 8mm; color: #5f6b76; }
tr.total td { border-bottom: none; border-top: 2px solid #1b1f23; font-weight: 700; font-size: 12pt; padding-top: 3mm; }
.pay { margin-top: 8mm; border: 1px solid #1b1f23; padding: 4mm 5mm; break-inside: avoid; }
.pay table { border-collapse: collapse; }
.pay td { padding: .7mm 0; vertical-align: top; }
.pay td:first-child { color: #5f6b76; width: 45mm; }
.mono { font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace; letter-spacing: .03em; }
.small { color: #5f6b76; font-size: 9pt; margin: 3mm 0 0; }
.notes { margin-top: 6mm; break-inside: avoid; }
.tax { margin-top: 6mm; font-size: 9.5pt; }
.stamp { position: absolute; top: 60mm; left: 0; right: 0; text-align: center; font-size: 64pt; font-weight: 800;
  color: rgba(176, 0, 32, .22); transform: rotate(-18deg); pointer-events: none; letter-spacing: .1em; }
@media print {
  body { background: #fff; }
  .sheet { width: auto; min-height: 0; margin: 0; padding: 0; box-shadow: none; }
}
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _multiline(text: str) -> str:
    return '<br>'.join(_esc(line) for line in text.strip().splitlines())


def _quantity(value: Decimal, language: Language) -> str:
    text = format(value.normalize(), 'f') if value == value.to_integral_value() else format(value, 'f')
    return text.replace('.', ',') if language == 'ru' else text


def render_html(invoice: Invoice, operator: OperatorProfile, *, status: InvoiceStatus = 'issued') -> str:
    """Счёт в самодостаточном HTML: A4, без внешних шрифтов и картинок, «Печать → PDF» в браузере.

    Всё, что пришло из флагов и файлов (клиент, позиции, примечания), экранируется.

    Args:
        invoice: Счёт.
        operator: Реквизиты оператора.
        status: Статус в реестре; аннулированный счёт печатается с отметкой.

    Returns:
        HTML-документ.
    """
    lang = invoice.language
    t = _LABELS[lang]
    issuer = operator.display_name(lang)
    rows = ''.join(
        f'<tr><td class="idx">{index}</td><td>{_esc(item.description)}</td>'
        f'<td class="num">{_quantity(item.quantity, lang)}</td>'
        f'<td class="num">{format_amount(item.unit_price, lang)}</td>'
        f'<td class="num">{format_amount(item.amount, lang)}</td></tr>'
        for index, item in enumerate(invoice.items, start=1)
    )
    client_lines = [f'<p class="name">{_esc(invoice.client.name)}</p>']
    if invoice.client.address:
        client_lines.append(f'<p>{_multiline(invoice.client.address)}</p>')
    if invoice.client.tax_id:
        client_lines.append(f'<p>{_esc(t["tax_id"])}: {_esc(invoice.client.tax_id)}</p>')
    if invoice.client.email:
        client_lines.append(f'<p>{_esc(invoice.client.email)}</p>')
    contacts = [operator.email, operator.phone, operator.website]
    issuer_lines = [
        f'<p class="name">{_esc(issuer)}</p>',
        f'<p>{_esc(t["id"])}: {_esc(operator.id_number)}</p>',
        f'<p>{_multiline(operator.address)}</p>',
        *(f'<p>{_esc(contact)}</p>' for contact in contacts if contact),
    ]
    notes = (
        f'<div class="notes"><div class="label">{_esc(t["notes"])}</div><p>{_multiline(invoice.notes)}</p></div>'
        if invoice.notes.strip()
        else ''
    )
    stamp = f'<div class="stamp">{_esc(t["void"])}</div>' if status == 'void' else ''
    total = format_money(invoice.total, invoice.currency, lang)
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_esc(t["title"])} {_esc(invoice.number)} — {_esc(invoice.client.name)}</title>'
        f'<style>{_CSS}</style></head><body><main class="sheet">{stamp}'
        f'<header><div><h1>{_esc(t["title"])}</h1><div class="issuer">{_esc(issuer)}</div></div>'
        f'<table class="meta"><tr><td>{_esc(t["number"])}</td><td><b>{_esc(invoice.number)}</b></td></tr>'
        f'<tr><td>{_esc(t["issued"])}</td><td>{invoice.issue_date.isoformat()}</td></tr>'
        f'<tr><td>{_esc(t["due"])}</td><td>{invoice.due_date.isoformat()}</td></tr>'
        f'<tr><td>{_esc(t["currency"])}</td><td>{_esc(invoice.currency)}</td></tr></table></header>'
        f'<section class="parties"><div class="party"><div class="label">{_esc(t["from"])}</div>'
        f'{"".join(issuer_lines)}</div>'
        f'<div class="party"><div class="label">{_esc(t["to"])}</div>{"".join(client_lines)}</div></section>'
        f'<table class="items"><thead><tr><th class="idx">#</th><th>{_esc(t["description"])}</th>'
        f'<th class="num">{_esc(t["qty"])}</th><th class="num">{_esc(t["unit"])}</th>'
        f'<th class="num">{_esc(t["amount"])}</th></tr></thead><tbody>{rows}'
        f'<tr class="total"><td></td><td colspan="3">{_esc(t["total"])}</td><td class="num">{_esc(total)}</td></tr>'
        '</tbody></table>'
        f'<section class="pay"><div class="label">{_esc(t["pay"])}</div><table>'
        f'<tr><td>{_esc(t["beneficiary"])}</td><td>{_esc(operator.legal_name)}</td></tr>'
        f'<tr><td>{_esc(t["iban"])}</td><td class="mono">{_esc(operator.iban_grouped)}</td></tr>'
        f'<tr><td>{_esc(t["bank"])}</td><td>{_esc(operator.bank_name)}</td></tr>'
        f'<tr><td>{_esc(t["swift"])}</td><td class="mono">{_esc(operator.swift_code)}</td></tr>'
        f'<tr><td>{_esc(t["reference"])}</td><td class="mono"><b>{_esc(invoice.number)}</b></td></tr>'
        f'</table><p class="small">{_esc(t["fees"])}</p></section>'
        f'{notes}<p class="tax">{_esc(operator.tax_note.text(lang))}</p>'
        '</main></body></html>\n'
    )


def md_text(text: str) -> str:
    """Текст для Markdown-таблицы: без переводов строк, `|` и сырого HTML."""
    return ' '.join(text.split()).replace('|', '\\|').replace('<', '&lt;').replace('>', '&gt;')


def render_markdown(invoice: Invoice, operator: OperatorProfile, *, status: InvoiceStatus = 'issued') -> str:
    """Короткая сводка счёта в Markdown — для письма клиенту или заметок оператора.

    Args:
        invoice: Счёт.
        operator: Реквизиты оператора.
        status: Статус в реестре.

    Returns:
        Markdown-текст.
    """
    lang = invoice.language
    t = _LABELS[lang]
    void = f' — **{t["void"]}**' if status == 'void' else ''
    lines = [
        f'# {t["title"]} {invoice.number}{void}',
        '',
        f'- {t["to"]}: {md_text(invoice.client.name)}',
        f'- {t["issued"]}: {invoice.issue_date.isoformat()} · {t["due"]}: {invoice.due_date.isoformat()}',
        f'- {t["total"]}: **{format_money(invoice.total, invoice.currency, lang)}**',
        '',
        f'| # | {t["description"]} | {t["qty"]} | {t["unit"]} | {t["amount"]} |',
        '|---:|---|---:|---:|---:|',
        *(
            f'| {index} | {md_text(item.description)} | {_quantity(item.quantity, lang)} | '
            f'{format_amount(item.unit_price, lang)} | {format_amount(item.amount, lang)} |'
            for index, item in enumerate(invoice.items, start=1)
        ),
        '',
        f'**{t["pay"]}:** {md_text(operator.legal_name)} · {t["iban"]} `{operator.iban_grouped}` · '
        f'{md_text(operator.bank_name)} · {t["swift"]} `{operator.swift_code}` · {t["reference"]}: `{invoice.number}`',
        '',
        md_text(operator.tax_note.text(lang)),
    ]
    if invoice.notes.strip():
        lines += ['', f'{t["notes"]}: {md_text(invoice.notes)}']
    return '\n'.join(lines) + '\n'


def total_of(items: Sequence[LineItem]) -> Decimal:
    """Итог позиций — сумма округлённых строк."""
    return sum((item.amount for item in items), Decimal(0))

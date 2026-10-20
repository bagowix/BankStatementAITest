"""Команды офиса оператора: `autostudio office invoice | ledger | contract`.

Реквизиты — из `operator.toml` (флаг `--operator` или `AUTOSTUDIO_OPERATOR`), данные —
в `var/office/` (флаг `--data-dir` или `AUTOSTUDIO_OFFICE_DIR`). Сеть нужна только для
курса НБГ, и без неё всё работает с `--rate`.
"""

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Final, Literal

import cyclopts
import httpx
import msgspec

from autostudio.clock import system_now
from autostudio.errors import ConfigError
from autostudio.office.contract import (
    DEFAULT_TEMPLATES_DIR,
    AgreementInfo,
    ContractParty,
    ContractRequest,
    TemplateName,
    contract_html,
    fields_skeleton,
    load_contract_request,
    load_template,
    merge_requests,
    output_name,
    render_contract,
    template_path,
)
from autostudio.office.invoice import (
    DEFAULT_DUE_DAYS,
    ClientParty,
    Invoice,
    InvoiceDraft,
    LineItem,
    load_invoice_request,
    parse_item,
    period_suffix,
    preset_item,
    render_html,
    render_markdown,
)
from autostudio.office.ledger import (
    Channel,
    Receipt,
    build_month_report,
    month_end,
    receipts_csv,
    render_month_report,
)
from autostudio.office.money import (
    Language,
    currency_code,
    format_money,
    invoice_currency,
    money,
    parse_amount,
    parse_month,
    tbilisi_today,
)
from autostudio.office.operator import OperatorProfile, load_operator, operator_path
from autostudio.office.rates import NBG_TIMEOUT_S, RateLookup, resolve_rate
from autostudio.office.store import DB_NAME, InvoiceRecord, OfficeStore

app = cyclopts.App(name='office', help='Бумаги оператора: счета, книга поступлений и налог 1%, договоры.')
invoice_app = app.command(
    cyclopts.App(name='invoice', help='Счёт прямому клиенту; list, show, paid, void — реестр счетов.')
)
ledger_app = app.command(
    cyclopts.App(name='ledger', help='Книга поступлений: add, month (цифры для декларации), export, rate.')
)
contract_app = app.command(
    cyclopts.App(name='contract', help='Договор, SLA или DPA клиенту из шаблонов docs/legal; fields — все поля.')
)

OFFICE_DIR_ENV: Final = 'AUTOSTUDIO_OFFICE_DIR'
DEFAULT_OFFICE_DIR: Final = Path('var/office')
_COUNTRY_LENGTH: Final = 2
_USER_AGENT: Final = 'autostudio-office/1 (own bookkeeping: NBG official rate)'

type OutputFormat = Literal['html', 'md']


def office_dir(explicit: Path | None) -> Path:
    """Каталог данных офиса: флаг, `AUTOSTUDIO_OFFICE_DIR` или `var/office`."""
    if explicit is not None:
        return explicit
    from_env = os.environ.get(OFFICE_DIR_ENV, '').strip()
    return Path(from_env) if from_env else DEFAULT_OFFICE_DIR


def _store(data_dir: Path | None) -> OfficeStore:
    return OfficeStore(office_dir(data_dir) / DB_NAME)


def _operator(path: Path | None) -> OperatorProfile:
    return load_operator(operator_path(path, os.environ))


def _now() -> dt.datetime:
    return system_now()


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=NBG_TIMEOUT_S, headers={'User-Agent': _USER_AGENT}, follow_redirects=True)


def _country_code(text: str) -> str:
    code = text.strip().upper()
    if len(code) != _COUNTRY_LENGTH or not code.isascii() or not code.isalpha():
        raise ConfigError(f'страна {text!r}: две буквы ISO 3166, например GE, US, DE')
    return code


def _country(text: str | None) -> str | None:
    return None if text is None else _country_code(text)


def _write_invoice_files(invoice: Invoice, operator: OperatorProfile, record_dir: Path, record: InvoiceRecord) -> Path:
    record_dir.mkdir(parents=True, exist_ok=True)
    html_path = record_dir / f'{invoice.number}.html'
    html_path.write_text(render_html(invoice, operator, status=record.status), encoding='utf-8')
    (record_dir / f'{invoice.number}.md').write_text(
        render_markdown(invoice, operator, status=record.status), encoding='utf-8'
    )
    return html_path


def _invoice_from_flags(
    *,
    setup: str | None,
    support: str | None,
    months: int,
    period: str | None,
    items: list[str],
    language: Language,
) -> list[LineItem]:
    if months < 1:
        raise ConfigError('--months: от 1')
    if support is None and (period is not None or months != 1):
        raise ConfigError('--period и --months относятся к позиции поддержки: добавьте --support <цена за месяц>')
    lines: list[LineItem] = []
    if setup is not None:
        lines.append(preset_item('setup', setup, language))
    if support is not None:
        line = preset_item('support', support, language, quantity=Decimal(months))
        if period is not None:
            line = msgspec.structs.replace(
                line, description=line.description + period_suffix(parse_month(period), language)
            )
        lines.append(line)
    lines += [parse_item(spec) for spec in items]
    return lines


@invoice_app.default
def invoice_new(
    *,
    client_name: str | None = None,
    client_address: str | None = None,
    client_tax_id: str | None = None,
    client_email: str | None = None,
    client_country: str | None = None,
    setup: str | None = None,
    support: str | None = None,
    months: int = 1,
    period: str | None = None,
    item: list[str] | None = None,
    currency: str | None = None,
    date: dt.date | None = None,
    due_days: int | None = None,
    lang: Language | None = None,
    notes: str | None = None,
    from_file: Path | None = None,
    operator: Path | None = None,
    data_dir: Path | None = None,
) -> None:
    """Выставляет счёт: номер из реестра, HTML для печати в PDF и сводка в Markdown.

    Args:
        client_name: Наименование клиента.
        client_address: Адрес клиента.
        client_tax_id: Налоговый номер клиента.
        client_email: E-mail клиента.
        client_country: Страна клиента (GE, US, …): нужна, чтобы оплату учесть в пороге НДС.
        setup: Цена разработки и настройки — позиция со стандартной формулировкой.
        support: Цена месяца поддержки — позиция «Technical support and maintenance of software».
        months: Сколько месяцев поддержки в счёте.
        period: Месяц поддержки ГГГГ-ММ — добавится к позиции («— November 2026»).
        item: Своя позиция "описание;количество;цена" (можно несколько раз).
        currency: USD, EUR или GEL; по умолчанию — из operator.toml.
        date: Дата счёта; по умолчанию сегодня в Тбилиси.
        due_days: Дней на оплату (по умолчанию 7).
        lang: Язык счёта: en или ru.
        notes: Примечание внизу счёта.
        from_file: Счёт из TOML-файла ([client], [[items]], currency, language, …).
        operator: Путь к operator.toml.
        data_dir: Каталог данных офиса.
    """
    profile = _operator(operator)
    now = _now()
    today = tbilisi_today(now)
    client_flags = [client_name, client_address, client_tax_id, client_email, client_country]
    item_flags = [setup, support, period, *(item or [])]
    if from_file is not None:
        if any(value is not None for value in client_flags + item_flags):
            raise ConfigError('--from-file: клиент и позиции берутся из файла — не смешивайте их с флагами')
        request = load_invoice_request(from_file)
        if lang is not None:
            request = msgspec.structs.replace(request, language=lang)
        draft = request.draft(default_currency=profile.default_currency, today=today)
        draft = msgspec.structs.replace(
            draft,
            currency=invoice_currency(currency) if currency else draft.currency,
            issue_date=date or draft.issue_date,
            due_days=draft.due_days if due_days is None else due_days,
            notes=draft.notes if notes is None else notes,
            client=msgspec.structs.replace(draft.client, country=_country(draft.client.country)),
        )
    else:
        if not client_name or not client_name.strip():
            raise ConfigError('нужно --client-name (или --from-file)')
        language: Language = lang or 'en'
        client = ClientParty(
            name=client_name,
            address=client_address or '',
            tax_id=client_tax_id,
            email=client_email,
            country=_country(client_country),
        )
        draft = InvoiceDraft(
            client=client,
            items=_invoice_from_flags(
                setup=setup,
                support=support,
                months=months,
                period=period,
                items=item or [],
                language=language,
            ),
            currency=invoice_currency(currency) if currency else profile.default_currency,
            issue_date=date or today,
            due_days=DEFAULT_DUE_DAYS if due_days is None else due_days,
            language=language,
            notes=notes or '',
        )
    store = _store(data_dir)
    invoice = store.issue_invoice(draft, prefix=profile.invoice_prefix, now=now)
    record = store.get_invoice(invoice.number)
    html_path = _write_invoice_files(invoice, profile, office_dir(data_dir) / 'invoices', record)
    print(
        f'счёт {invoice.number} выставлен: {invoice.client.name}, '
        f'{format_money(invoice.total, invoice.currency, "en")}, оплатить до {invoice.due_date.isoformat()}\n'
        f'  для печати в PDF: {html_path}\n  сводка: {html_path.with_suffix(".md")}'
    )
    if invoice.client.country == 'GE' and invoice.currency != 'GEL':
        print('  заметка: местные клиенты платят в лари на счёт ИП (бриф 2.1) — валюту уточните у бухгалтера')
    if invoice.issue_date > today:
        print('  заметка: дата счёта в будущем')


@invoice_app.command(name='list')
def invoice_list(*, year: int | None = None, data_dir: Path | None = None) -> None:
    """Реестр счетов: номер, дата, клиент, сумма, статус.

    Args:
        year: Только счета этого года.
        data_dir: Каталог данных офиса.
    """
    records = _store(data_dir).list_invoices(year)
    if not records:
        print('счетов нет')
        return
    for record in records:
        invoice = record.invoice
        status = {
            'issued': f'выставлен, срок {invoice.due_date.isoformat()}',
            'paid': f'оплачен {record.paid_on.isoformat() if record.paid_on else ""}'.rstrip(),
            'void': f'АННУЛИРОВАН: {record.void_reason or ""}'.rstrip(),
        }[record.status]
        print(
            f'{invoice.number}  {invoice.issue_date.isoformat()}  {invoice.client.name}  '
            f'{format_money(invoice.total, invoice.currency, "en")}  {status}'
        )


@invoice_app.command(name='show')
def invoice_show(
    number: str, *, html: bool = False, operator: Path | None = None, data_dir: Path | None = None
) -> None:
    """Показывает счёт из реестра; с --html заново пишет файлы для печати.

    Args:
        number: Номер счёта.
        html: Перезаписать HTML и Markdown в var/office/invoices (с отметкой, если счёт аннулирован).
        operator: Путь к operator.toml.
        data_dir: Каталог данных офиса.
    """
    record = _store(data_dir).get_invoice(number)
    profile = _operator(operator)
    print(render_markdown(record.invoice, profile, status=record.status), end='')
    if html:
        path = _write_invoice_files(record.invoice, profile, office_dir(data_dir) / 'invoices', record)
        print(f'\nфайл для печати: {path}')


@invoice_app.command(name='void')
def invoice_void(number: str, *, reason: str, operator: Path | None = None, data_dir: Path | None = None) -> None:
    """Аннулирует счёт: он остаётся в реестре с отметкой, номер не используется повторно.

    Args:
        number: Номер счёта.
        reason: Причина (видна в реестре).
        operator: Путь к operator.toml.
        data_dir: Каталог данных офиса.
    """
    profile = _operator(operator)
    record = _store(data_dir).void_invoice(number, reason=reason, now=_now())
    path = _write_invoice_files(record.invoice, profile, office_dir(data_dir) / 'invoices', record)
    print(f'счёт {number} аннулирован ({reason}); файл с отметкой: {path}')


def _lookup_rate(
    store: OfficeStore, currency: str, on_date: dt.date, *, today: dt.date, rate: str | None, fetch: bool
) -> RateLookup:
    return resolve_rate(
        currency,
        on_date,
        today=today,
        manual=None if rate is None else parse_amount(rate, what='--rate'),
        cache=store,
        client_factory=_http_client if fetch else None,
    )


def _describe(receipt: Receipt, lookup: RateLookup) -> str:
    source = {
        'manual': 'курс вручную',
        'nbg': f'курс НБГ от {lookup.source_date.isoformat() if lookup.source_date else "?"}',
        'gel': 'лари',
    }[lookup.source]
    return (
        f'#{receipt.id}: {receipt.received_on.isoformat()} {receipt.amount} {receipt.currency} × {receipt.rate} = '
        f'{receipt.gel_amount} GEL ({source}), {receipt.channel}, {receipt.counterparty}, {receipt.client_country}'
    )


@invoice_app.command(name='paid')
def invoice_paid(
    number: str,
    *,
    date: dt.date | None = None,
    amount: str | None = None,
    currency: str | None = None,
    channel: Channel = 'bank',
    rate: str | None = None,
    fee: str | None = None,
    country: str | None = None,
    note: str = '',
    fetch: bool = True,
    data_dir: Path | None = None,
) -> None:
    """Отмечает счёт оплаченным и записывает поступление в книгу.

    Args:
        number: Номер счёта.
        date: Дата получения денег; по умолчанию сегодня в Тбилиси.
        amount: Полученная сумма ДО комиссий; по умолчанию — сумма счёта.
        currency: Валюта поступления; по умолчанию — валюта счёта.
        channel: Канал: bank, wise, paypal, payoneer, upwork, fiverr.
        rate: Курс НБГ вручную, лари за 1 единицу.
        fee: Комиссия канала в валюте поступления — для сведения.
        country: Страна клиента, если её нет в счёте.
        note: Заметка.
        fetch: Спросить курс у НБГ, если его нет в кэше.
        data_dir: Каталог данных офиса.
    """
    store = _store(data_dir)
    now = _now()
    today = tbilisi_today(now)
    record = store.get_invoice(number)
    if record.status != 'issued':
        raise ConfigError(f'счёт {number}: статус {record.status}, отметить оплату нельзя')
    invoice = record.invoice
    client_country = _country(country) or invoice.client.country
    if client_country is None:
        raise ConfigError('в счёте нет страны клиента: укажите --country (GE — грузинский клиент, порог НДС)')
    received_on = date or today
    received = money(parse_amount(amount, what='--amount', places=2)) if amount is not None else invoice.total
    received_currency = currency_code(currency) if currency else invoice.currency
    lookup = _lookup_rate(store, received_currency, received_on, today=today, rate=rate, fetch=fetch)
    receipt = Receipt(
        received_on=received_on,
        amount=received,
        currency=received_currency,
        rate=lookup.gel_per_unit,
        rate_source=lookup.source,
        channel=channel,
        counterparty=invoice.client.name,
        client_country=client_country,
        invoice_number=number,
        platform_fee=None if fee is None else money(parse_amount(fee, what='--fee', allow_zero=True, places=2)),
        note=note,
    )
    _, saved = store.pay_invoice(number, receipt, now=now)
    print(f'счёт {number} оплачен; в книге {_describe(saved, lookup)}')
    if received_currency == invoice.currency and received != invoice.total:
        print(
            f'  внимание: получено {received}, а в счёте {invoice.total} {invoice.currency} — '
            'в оборот идёт полученная сумма до комиссий'
        )


@ledger_app.command(name='add')
def ledger_add(
    *,
    amount: str,
    currency: str,
    channel: Channel,
    counterparty: str,
    country: str,
    date: dt.date | None = None,
    rate: str | None = None,
    fee: str | None = None,
    invoice: str | None = None,
    note: str = '',
    fetch: bool = True,
    data_dir: Path | None = None,
) -> None:
    """Записывает поступление: сумма ДО комиссии площадки, курс НБГ на дату получения.

    Args:
        amount: Сумма до комиссии площадки (Upwork, Fiverr, PayPal): консервативная база налога.
        currency: Валюта поступления.
        channel: Канал: bank, wise, paypal, payoneer, upwork, fiverr.
        counterparty: Кто заплатил (клиент или площадка).
        country: Страна клиента: GE — грузинский клиент (идёт в порог НДС), иначе иностранный.
        date: Дата получения (зачисление на счёт или баланс площадки); по умолчанию сегодня.
        rate: Курс НБГ вручную, лари за 1 единицу; сохраняется с поступлением.
        fee: Комиссия площадки в той же валюте — только для сведения, из оборота не вычитается.
        invoice: Номер счёта, если платили по счёту (отметить оплату: office invoice paid).
        note: Заметка.
        fetch: Спросить курс у НБГ, если его нет в кэше.
        data_dir: Каталог данных офиса.
    """
    store = _store(data_dir)
    now = _now()
    today = tbilisi_today(now)
    received_on = date or today
    code = currency_code(currency)
    client_country = _country_code(country)
    if not counterparty.strip():
        raise ConfigError('--counterparty: кто заплатил (клиент или площадка)')
    lookup = _lookup_rate(store, code, received_on, today=today, rate=rate, fetch=fetch)
    receipt = Receipt(
        received_on=received_on,
        amount=money(parse_amount(amount, what='--amount', places=2)),
        currency=code,
        rate=lookup.gel_per_unit,
        rate_source=lookup.source,
        channel=channel,
        counterparty=counterparty.strip(),
        client_country=client_country,
        invoice_number=invoice.strip() if invoice else None,
        platform_fee=None if fee is None else money(parse_amount(fee, what='--fee', allow_zero=True, places=2)),
        note=note,
    )
    saved = store.add_receipt(receipt, now=now)
    print(f'записано {_describe(saved, lookup)}')
    if invoice and store.get_invoice(invoice.strip()).status == 'issued':
        print(f'  счёт {invoice} всё ещё «выставлен»: отметить оплату — autostudio office invoice paid {invoice}')


@ledger_app.command(name='month')
def ledger_month(month: str, *, data_dir: Path | None = None) -> None:
    """Цифры месяца для декларации: оборот в лари, налог 1% (оценка), потолок 500 000, порог НДС, срок.

    Args:
        month: Отчётный месяц ГГГГ-ММ.
        data_dir: Каталог данных офиса.
    """
    first = parse_month(month)
    report = build_month_report(_store(data_dir).receipts(until=month_end(first)), first)
    print(render_month_report(report), end='')
    print(f'\nвыгрузка для бухгалтера: autostudio office ledger export --month {first:%Y-%m}')


@ledger_app.command(name='export')
def ledger_export(
    *, month: str | None = None, year: int | None = None, out: Path | None = None, data_dir: Path | None = None
) -> None:
    """CSV поступлений для бухгалтера за месяц или год.

    Args:
        month: Месяц ГГГГ-ММ.
        year: Год целиком.
        out: Куда записать; по умолчанию var/office/exports/ledger-<период>.csv.
        data_dir: Каталог данных офиса.
    """
    if (month is None) == (year is None):
        raise ConfigError('укажите ровно одно: --month ГГГГ-ММ или --year ГГГГ')
    if month is not None:
        start = parse_month(month)
        end = month_end(start)
        label = f'{start:%Y-%m}'
    else:
        start, end, label = dt.date(year or 0, 1, 1), dt.date(year or 0, 12, 31), str(year)
    receipts = [r for r in _store(data_dir).receipts(until=end) if r.received_on >= start]
    path = out or office_dir(data_dir) / 'exports' / f'ledger-{label}.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    # BOM: Excel у бухгалтера иначе показывает кириллицу и грузинский как кракозябры.
    path.write_text(receipts_csv(receipts), encoding='utf-8-sig', newline='')
    total = sum((r.gel_amount for r in receipts), Decimal(0))
    print(f'{path}: {len(receipts)} поступлений, {total} GEL')


@ledger_app.command(name='rate')
def ledger_rate(day: dt.date, *, currency: str = 'USD', data_dir: Path | None = None) -> None:
    """Курс НБГ на дату (из кэша или с nbg.gov.ge) — проверить источник перед записью.

    Args:
        day: Дата получения денег.
        currency: Валюта.
        data_dir: Каталог данных офиса.
    """
    code = currency_code(currency)
    store = _store(data_dir)
    lookup = _lookup_rate(store, code, day, today=tbilisi_today(_now()), rate=None, fetch=True)
    source_date = lookup.source_date.isoformat() if lookup.source_date else day.isoformat()
    print(f'{code} на {day.isoformat()}: {lookup.gel_per_unit} GEL за 1 {code} (НБГ, курс от {source_date})')


@contract_app.default
def contract_render(
    template: TemplateName,
    *,
    lang: Language = 'en',
    client_file: list[Path] | None = None,
    client_name: str | None = None,
    client_legal_form: str | None = None,
    client_country: str | None = None,
    client_reg_number: str | None = None,
    client_address: str | None = None,
    client_representative: str | None = None,
    business_name: str | None = None,
    number: str | None = None,
    date: str | None = None,
    set_: Annotated[list[str] | None, cyclopts.Parameter(name='--set')] = None,
    drop_review_notes: bool = False,
    fmt: Annotated[OutputFormat, cyclopts.Parameter(name='--format')] = 'html',
    templates: Path = DEFAULT_TEMPLATES_DIR,
    out: Path | None = None,
    operator: Path | None = None,
    data_dir: Path | None = None,
) -> None:
    """Собирает договор из шаблона; если хоть одно место не заполнено — ошибка со списком, файл не пишется.

    Args:
        template: service-agreement, support-sla или dpa.
        lang: Язык шаблона: en или ru.
        client_file: TOML с [client], [agreement], [fields]; можно несколько, следующий переопределяет.
        client_name: Полное наименование клиента.
        client_legal_form: Организационно-правовая форма.
        client_country: Страна регистрации.
        client_reg_number: Регистрационный номер.
        client_address: Юридический адрес.
        client_representative: Кто подписывает: «имя, должность».
        business_name: Название бизнеса для текстов уведомлений (DPA).
        number: Номер договора.
        date: Дата договора.
        set_: Поле по точному тексту скобки: --set "14 / 30=14" (можно несколько раз).
        drop_review_notes: Снять пометки юриста и бухгалтера (после их проверки).
        fmt: html — для печати в PDF, md — для правок.
        templates: Каталог шаблонов.
        out: Куда записать; по умолчанию var/office/contracts/.
        operator: Путь к operator.toml.
        data_dir: Каталог данных офиса.
    """
    fields: dict[str, str] = {}
    for spec in set_ or []:
        key, sep, value = spec.partition('=')
        if not sep or not key.strip():
            raise ConfigError(f'--set {spec!r}: ожидается "текст в скобках=значение"')
        fields[key.strip()] = value
    flags = ContractRequest(
        client=ContractParty(
            name=client_name,
            legal_form=client_legal_form,
            country=client_country,
            registration_number=client_reg_number,
            address=client_address,
            representative=client_representative,
            business_name=business_name,
        ),
        agreement=AgreementInfo(number=number, date=date),
        fields=fields,
    )
    request = merge_requests([*(load_contract_request(path) for path in client_file or []), flags])
    profile = _operator(operator)
    path = template_path(templates, template, lang)
    rendered = render_contract(
        load_template(path),
        template=template,
        language=lang,
        operator=profile,
        request=request,
        drop_review_notes=drop_review_notes,
        template_label=str(path),
    )
    target = out or office_dir(data_dir) / 'contracts' / output_name(request, template, lang, fmt)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = contract_html(rendered.text, language=lang) if fmt == 'html' else rendered.text
    target.write_text(body, encoding='utf-8')
    print(f'договор собран: {target}')
    if rendered.dropped_notes:
        print(f'  снято пометок юриста/бухгалтера: {rendered.dropped_notes}')
    for notice in rendered.notices:
        print(f'  заметка: {notice}')


@contract_app.command(name='fields')
def contract_fields(
    template: TemplateName, *, lang: Language = 'en', templates: Path = DEFAULT_TEMPLATES_DIR, out: Path | None = None
) -> None:
    """Все поля шаблона заготовкой TOML для файла клиента (всё закомментировано).

    Args:
        template: service-agreement, support-sla или dpa.
        lang: Язык шаблона: en или ru.
        templates: Каталог шаблонов.
        out: Записать в файл (существующий не перезаписывается); по умолчанию — в stdout.
    """
    path = template_path(templates, template, lang)
    skeleton = fields_skeleton(load_template(path), template=template, language=lang, label=str(path))
    if out is None:
        print(skeleton, end='')
        return
    if out.exists():
        raise ConfigError(f'{out} уже существует — правьте его, а не перезаписывайте')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(skeleton, encoding='utf-8')
    print(f'заготовка полей: {out}')

"""Книга поступлений ИП и месячные цифры для декларации малого бизнеса.

Правила (бриф 6.2, всё — «не проверено», решает бухгалтер):
- налог 1% с валового оборота, декларация ежемесячно, нарастающим итогом, до 15-го
  следующего месяца. Поэтому «к уплате за месяц» считается как 1% от оборота с начала
  года минус 1% на конец прошлого месяца (каждое — до тетри): так суммы по месяцам
  сходятся с налогом за год, а не расходятся на округлениях. Простой 1% от оборота
  месяца показывается рядом, для сверки;
- оборот — в лари по официальному курсу НБГ на дату получения;
- консервативно: база — сумма ДО комиссии площадки (Upwork, Fiverr, PayPal), а дата —
  зачисление на баланс площадки или кошелька. Комиссия записывается отдельно, только
  для сведения, и из оборота не вычитается;
- потолок режима — 500 000 GEL оборота за календарный год;
- регистрация плательщиком НДС обязательна при обороте больше 100 000 GEL за любые
  12 месяцев подряд; услуги иностранным бизнесам в порог, скорее всего, не входят,
  поэтому порог считается по грузинским клиентам (`client_country = "GE"`).
"""

import csv
import datetime as dt
import io
from collections.abc import Sequence
from decimal import Decimal
from typing import Final, Literal

import msgspec

from autostudio.office.money import money, shift_months
from autostudio.office.rates import RateSource
from autostudio.sinks.csv_sink import csv_cell

type Channel = Literal['bank', 'wise', 'paypal', 'payoneer', 'upwork', 'fiverr']

CHANNELS: Final[tuple[Channel, ...]] = ('bank', 'wise', 'paypal', 'payoneer', 'upwork', 'fiverr')
SMALL_BUSINESS_RATE: Final = Decimal('0.01')
SMALL_BUSINESS_CEILING_GEL: Final = Decimal(500_000)
VAT_THRESHOLD_GEL: Final = Decimal(100_000)
# Предупреждать заранее: регистрация плательщиком НДС — за 2 рабочих дня после превышения.
WARNING_SHARE: Final = Decimal('0.8')
DECLARATION_DAY: Final = 15
GEORGIA: Final = 'GE'
_PERCENT: Final = Decimal(100)
_ONE_DECIMAL: Final = Decimal('0.1')

CSV_HEADER: Final = (
    'date_received',
    'amount',
    'currency',
    'nbg_rate_gel',
    'rate_source',
    'amount_gel',
    'channel',
    'counterparty',
    'client_country',
    'invoice',
    'platform_fee',
    'note',
)


class Receipt(msgspec.Struct, frozen=True, kw_only=True):
    """Одно поступление денег.

    Attributes:
        received_on: Дата получения (зачисление на счёт, баланс площадки или кошелька).
        amount: Сумма ДО комиссии площадки, в валюте поступления.
        currency: Код валюты.
        rate: Лари за 1 единицу валюты (1 для GEL).
        rate_source: Откуда курс: `manual`, `nbg`, `gel`.
        channel: Канал: банк, Wise, PayPal, Payoneer, Upwork, Fiverr.
        counterparty: Кто заплатил.
        client_country: Страна клиента (ISO из двух букв); `GE` идёт в порог НДС.
        invoice_number: Номер счёта, если платили по счёту.
        platform_fee: Комиссия площадки в той же валюте — только для сведения.
        note: Заметка.
        id: Номер записи в книге (после сохранения).
    """

    received_on: dt.date
    amount: Decimal
    currency: str
    rate: Decimal
    rate_source: RateSource
    channel: Channel
    counterparty: str
    client_country: str
    invoice_number: str | None = None
    platform_fee: Decimal | None = None
    note: str = ''
    id: int | None = None

    @property
    def gel_amount(self) -> Decimal:
        # Каждое поступление округляется до тетри отдельно: так же его увидит бухгалтер в выгрузке.
        return money(self.amount * self.rate)

    @property
    def georgian(self) -> bool:
        return self.client_country == GEORGIA


class VatWindow(msgspec.Struct, frozen=True, kw_only=True):
    """Самое «тяжёлое» 12-месячное окно, которое заканчивается в отчётном месяце.

    Attributes:
        start: Первый день окна.
        end: Последний день окна.
        georgian_turnover_gel: Оборот с грузинскими клиентами в окне, GEL.
    """

    start: dt.date
    end: dt.date
    georgian_turnover_gel: Decimal


class MonthReport(msgspec.Struct, frozen=True, kw_only=True):
    """Цифры месяца для декларации и порогов.

    Attributes:
        month: Первое число отчётного месяца.
        receipts: Поступления месяца по дате.
        turnover_gel: Оборот месяца в лари.
        tax_estimate_gel: 1% от оборота месяца, до тетри — для сверки.
        ytd_turnover_gel: Оборот с 1 января по конец месяца.
        ytd_tax_estimate_gel: Оценка налога нарастающим итогом.
        prior_ytd_turnover_gel: Оборот с 1 января по конец прошлого месяца (0 для января).
        tax_due_gel: К уплате за месяц: 1% нарастающим итогом минус 1% на конец прошлого месяца.
        vat_window: Пиковое 12-месячное окно по грузинским клиентам.
        deadline: Срок декларации и уплаты (15-е следующего месяца).
        fees: Комиссии площадок за месяц по валютам — для сведения.
    """

    month: dt.date
    receipts: list[Receipt]
    turnover_gel: Decimal
    tax_estimate_gel: Decimal
    ytd_turnover_gel: Decimal
    ytd_tax_estimate_gel: Decimal
    prior_ytd_turnover_gel: Decimal
    tax_due_gel: Decimal
    vat_window: VatWindow
    deadline: dt.date
    fees: dict[str, Decimal]

    @property
    def ceiling_share(self) -> Decimal:
        return self.ytd_turnover_gel / SMALL_BUSINESS_CEILING_GEL

    @property
    def vat_share(self) -> Decimal:
        return self.vat_window.georgian_turnover_gel / VAT_THRESHOLD_GEL

    def warnings(self) -> list[str]:
        """Что требует действий: порог НДС и потолок режима."""
        found: list[str] = []
        vat = self.vat_window.georgian_turnover_gel
        if vat > VAT_THRESHOLD_GEL:
            found.append(
                f'ПОРОГ НДС ПРЕВЫШЕН: {_gel(vat)} GEL с грузинскими клиентами за 12 месяцев '
                f'({self.vat_window.start.isoformat()} — {self.vat_window.end.isoformat()}). Регистрация плательщиком '
                'НДС — в течение 2 рабочих дней (бриф 6.2), звоните бухгалтеру сегодня.'
            )
        elif self.vat_share >= WARNING_SHARE:
            found.append(
                f'Порог НДС близко: {_percent(self.vat_share)}% от 100 000 GEL с грузинскими клиентами за 12 месяцев. '
                'Спросите бухгалтера заранее: после превышения на регистрацию 2 рабочих дня.'
            )
        if self.ytd_turnover_gel > SMALL_BUSINESS_CEILING_GEL:
            found.append(
                f'ПОТОЛОК РЕЖИМА ПРЕВЫШЕН: {_gel(self.ytd_turnover_gel)} GEL с начала года. Сумма сверх 500 000 GEL '
                'облагается иначе (по брифу 3%, не проверено) — оценка 1% здесь занижена, решает бухгалтер.'
            )
        elif self.ceiling_share >= WARNING_SHARE:
            found.append(f'С начала года {_percent(self.ceiling_share)}% от потолка 500 000 GEL.')
        return found


def tax_estimate(turnover_gel: Decimal) -> Decimal:
    """1% оборота, до тетри по ROUND_HALF_UP. Это оценка: сумму в декларации подтверждает бухгалтер."""
    return money(turnover_gel * SMALL_BUSINESS_RATE)


def declaration_deadline(month: dt.date) -> dt.date:
    """15-е число месяца, следующего за отчётным."""
    return shift_months(month.replace(day=1), 1).replace(day=DECLARATION_DAY)


def month_end(month: dt.date) -> dt.date:
    """Последний день месяца."""
    return shift_months(month.replace(day=1), 1) - dt.timedelta(days=1)


def twelve_month_start(end: dt.date) -> dt.date:
    """Первый день 12-месячного окна, которое заканчивается `end` включительно."""
    return shift_months(end, -12) + dt.timedelta(days=1)


def peak_vat_window(receipts: Sequence[Receipt], month: dt.date) -> VatWindow:
    """Максимальный оборот с грузинскими клиентами за 12 месяцев среди окон, заканчивающихся в этом месяце.

    Порог НДС — «за любые 12 месяцев подряд», а не за календарный год и не на конец
    месяца: окно со 2025-10-06 по 2026-10-05 может перейти порог, когда оба окна «на
    конец месяца» ещё нет. Сумма окна растёт только в дни поступлений, а уменьшается
    при сдвиге начала, поэтому максимум достигается в первый день месяца или в день
    одного из поступлений месяца — их и перебираем.

    Args:
        receipts: Все поступления (лишние отфильтруются).
        month: Любой день отчётного месяца.

    Returns:
        Окно с наибольшим оборотом.
    """
    first = month.replace(day=1)
    last = month_end(first)
    georgian = [r for r in receipts if r.georgian]

    def window(end: dt.date) -> VatWindow:
        start = twelve_month_start(end)
        total = sum((r.gel_amount for r in georgian if start <= r.received_on <= end), Decimal(0))
        return VatWindow(start=start, end=end, georgian_turnover_gel=total)

    best = window(first)
    for end in sorted({r.received_on for r in georgian if first < r.received_on <= last}):
        candidate = window(end)
        if candidate.georgian_turnover_gel > best.georgian_turnover_gel:
            best = candidate
    return best


def build_month_report(receipts: Sequence[Receipt], month: dt.date) -> MonthReport:
    """Считает цифры месяца из всей книги поступлений.

    Args:
        receipts: Поступления (можно все сразу: функция сама выберет нужные).
        month: Любой день отчётного месяца.

    Returns:
        Отчёт за месяц.
    """
    first = month.replace(day=1)
    last = month_end(first)
    in_month = sorted((r for r in receipts if first <= r.received_on <= last), key=lambda r: (r.received_on, r.id or 0))
    year_start = first.replace(month=1)
    turnover = sum((r.gel_amount for r in in_month), Decimal(0))
    ytd = sum((r.gel_amount for r in receipts if year_start <= r.received_on <= last), Decimal(0))
    prior_ytd = sum((r.gel_amount for r in receipts if year_start <= r.received_on < first), Decimal(0))
    fees: dict[str, Decimal] = {}
    for receipt in in_month:
        if receipt.platform_fee:
            fees[receipt.currency] = fees.get(receipt.currency, Decimal(0)) + receipt.platform_fee
    return MonthReport(
        month=first,
        receipts=in_month,
        turnover_gel=turnover,
        tax_estimate_gel=tax_estimate(turnover),
        ytd_turnover_gel=ytd,
        ytd_tax_estimate_gel=tax_estimate(ytd),
        prior_ytd_turnover_gel=prior_ytd,
        tax_due_gel=tax_estimate(ytd) - tax_estimate(prior_ytd),
        vat_window=peak_vat_window(receipts, first),
        deadline=declaration_deadline(first),
        fees=fees,
    )


def _gel(value: Decimal) -> str:
    return format(money(value), ',.2f').replace(',', ' ')


def _percent(share: Decimal) -> str:
    return str((share * _PERCENT).quantize(_ONE_DECIMAL))


def render_month_report(report: MonthReport) -> str:
    """Отчёт месяца в Markdown — для себя и для бухгалтера.

    Args:
        report: Посчитанный отчёт.

    Returns:
        Markdown-текст.
    """
    lines = [
        f'# Поступления и налог за {report.month:%Y-%m}',
        '',
        '> Оценка, не налоговая консультация: сумму в декларации подтверждает бухгалтер.',
        '',
    ]
    if report.receipts:
        lines += [
            '| Дата | Сумма | Курс НБГ | GEL | Канал | Плательщик | Страна | Счёт |',
            '|---|---:|---:|---:|---|---|---|---|',
        ]
        for r in report.receipts:
            source = {'manual': ' (вручную)', 'nbg': '', 'gel': ''}[r.rate_source]
            lines.append(
                f'| {r.received_on.isoformat()} | {r.amount} {r.currency} | {r.rate}{source} | {_gel(r.gel_amount)} | '
                f'{r.channel} | {_md(r.counterparty)} | {r.client_country} | {r.invoice_number or "—"} |'
            )
    else:
        lines.append('Поступлений нет. Нулевую декларацию всё равно подать (бриф 6.2).')
    window = report.vat_window
    lines += [
        '',
        f'- Оборот месяца: **{_gel(report.turnover_gel)} GEL**',
        f'- С начала года (нарастающим итогом): **{_gel(report.ytd_turnover_gel)} GEL** '
        f'({_percent(report.ceiling_share)}% от потолка 500 000 GEL); на конец прошлого месяца — '
        f'{_gel(report.prior_ytd_turnover_gel)} GEL',
        f'- Налог к уплате за месяц (оценка): **{_gel(report.tax_due_gel)} GEL** = 1% нарастающим итогом '
        f'{_gel(report.ytd_tax_estimate_gel)} − 1% на конец прошлого месяца '
        f'{_gel(tax_estimate(report.prior_ytd_turnover_gel))}',
        f'- Для сверки: 1% от оборота месяца — {_gel(report.tax_estimate_gel)} GEL (расхождение на тетри — '
        'округление; какую цифру вносить в форму rs.ge, решает бухгалтер)',
        f'- Порог НДС: {_gel(window.georgian_turnover_gel)} GEL с грузинскими клиентами за 12 месяцев '
        f'{window.start.isoformat()} — {window.end.isoformat()} ({_percent(report.vat_share)}% от 100 000 GEL; '
        'иностранные клиенты не считаются — уточните у бухгалтера)',
        f'- Декларация и уплата: **до {report.deadline.isoformat()}** на rs.ge (нарастающим итогом)',
    ]
    if report.fees:
        fees = ', '.join(f'{amount} {currency}' for currency, amount in sorted(report.fees.items()))
        lines.append(f'- Комиссии площадок (для сведения, из оборота не вычитаются): {fees}')
    if report.receipts and any(r.rate_source == 'manual' for r in report.receipts):
        lines.append('- Курс «вручную» сверить с официальным курсом НБГ на дату получения.')
    warnings = report.warnings()
    if warnings:
        lines += ['', '## Внимание', '', *(f'- {item}' for item in warnings)]
    return '\n'.join(lines) + '\n'


def _md(text: str) -> str:
    return ' '.join(text.split()).replace('|', '\\|').replace('<', '&lt;').replace('>', '&gt;')


def receipts_csv(receipts: Sequence[Receipt]) -> str:
    """Выгрузка для бухгалтера: одна строка — одно поступление, суммы в каноничной записи.

    Текст от плательщиков (имя, заметка) защищён от формул: файл откроют в Excel.

    Args:
        receipts: Поступления по дате.

    Returns:
        CSV-текст с заголовком.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for r in receipts:
        writer.writerow(
            [
                r.received_on.isoformat(),
                str(r.amount),
                r.currency,
                str(r.rate),
                r.rate_source,
                str(r.gel_amount),
                r.channel,
                csv_cell(r.counterparty),
                r.client_country,
                csv_cell(r.invoice_number or ''),
                '' if r.platform_fee is None else str(r.platform_fee),
                csv_cell(r.note),
            ]
        )
    return buffer.getvalue()

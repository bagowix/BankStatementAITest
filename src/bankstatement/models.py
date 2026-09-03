"""Схема результата извлечения.

Деньги везде Decimal. Округление до центов происходит только на границе
сериализации (`to_payload`), внутри арифметика точная — иначе сверка балансов
начинает расходиться на копейки там, где документ сходится идеально.

Модели построены на msgspec.Struct, а не на pydantic. Причина не в моде: у нас
нет внешней валидации, ради которой pydantic и нужен, — все структуры собирает
наш же код. Зато есть горячий путь на тысячи операций, где Struct со слотами
экономит и память, и время, а сериализация msgspec быстрее стандартной в разы.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final

import msgspec


class BankStatementError(Exception):
    """Базовый класс для ошибок домена банковской выписки."""

    pass


CENTS: Final[Decimal] = Decimal('0.01')
ZERO: Final[Decimal] = Decimal('0.00')

# Sequence/Mapping, а не list/dict: они ковариантны, и list[int] проходит как
# Sequence[JsonValue], тогда как list[int] в list[JsonValue] не превращается.
type JsonValue = str | int | float | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None
type Payload = dict[str, JsonValue]
"""Сериализуемый результат. Явный тип вместо Any: строгий mypy его запрещает, а
структура выдачи — часть контракта и должна проверяться, а не проскакивать."""


def money_out(value: Decimal | None) -> float | None:
    """Decimal -> float ровно на границе выдачи, с явным квантованием."""
    if value is None:
        return None

    return float(value.quantize(CENTS))


class Guarantee(StrEnum):
    """Чем подтверждён результат периода.

    Разные документы допускают разную силу проверки, и смешивать их в одно
    «извлечено успешно» нельзя: аудитору важно, что именно доказано.
    """

    CHAIN = 'A'  # каждая строка подтверждена цепочкой running balance
    AGGREGATE = 'B'  # сходятся только итоги (в документе нет колонки баланса)
    NONE = 'C'  # не сведено


class ReconStatus(StrEnum):
    RECONCILED = 'reconciled'
    FAILED = 'failed'


class Opinion(StrEnum):
    """Заключение по документу целиком.

    Названия взяты из аудиторской практики намеренно: там ровно эта шкала, и
    читателю отчёта не нужно догадываться, что значит «сошлось 1 из 10».
    Выводится механически из вердиктов оракула — своего мнения здесь нет.
    """

    CLEAN = 'clean'
    """Все выписки подтверждены арифметикой документа."""

    QUALIFIED = 'qualified'
    """Часть подтверждена, часть требует человека."""

    ADVERSE = 'adverse'
    """Не подтверждена ни одна: доверять нечему."""

    DISCLAIMER = 'disclaimer'
    """Выписок в документе не нашлось — заключать не о чем."""


class Source(msgspec.Struct, frozen=True):
    """Происхождение значения: без него цифра в аудите непригодна."""

    page: int
    bbox: tuple[float, float, float, float] | None = None
    engine: str = 'unknown'
    confidence: float | None = None
    repaired: bool = False
    printed: str | None = None

    def to_payload(self) -> Payload:
        return {
            'page': self.page,
            'bbox': list(self.bbox) if self.bbox else None,
            'engine': self.engine,
            'confidence': self.confidence,
            'repaired': self.repaired,
            'printed': self.printed,
        }


class Transaction(msgspec.Struct, frozen=True):
    date: date
    description: str
    deposit: Decimal | None = None
    withdrawal: Decimal | None = None
    balance: Decimal | None = None
    source: Source | None = None

    @property
    def signed_amount(self) -> Decimal:
        return (self.deposit or ZERO) - (self.withdrawal or ZERO)

    def to_payload(self) -> Payload:
        payload: Payload = {
            'date': self.date.isoformat(),
            'description': self.description,
            'deposit': money_out(self.deposit),
            'withdrawal': money_out(self.withdrawal),
        }

        if self.balance is not None:
            payload['balance'] = money_out(self.balance)

        if self.source is not None:
            payload['source'] = self.source.to_payload()

        return payload


class Period(msgspec.Struct, frozen=True):
    start: date
    end: date

    def to_payload(self) -> Payload:
        return {'start': self.start.isoformat(), 'end': self.end.isoformat()}


class Account(msgspec.Struct, frozen=True):
    bank: str | None = None
    account_last4: str | None = None
    period: Period | None = None

    def to_payload(self) -> Payload:
        return {
            'bank': self.bank,
            'account_last4': self.account_last4,
            'period': self.period.to_payload() if self.period else None,
        }


class Summary(msgspec.Struct, frozen=True):
    """Итоги, как они напечатаны в шапке выписки.

    Это одновременно требуемый выход (критерий exact match) и эталон, которым
    проверяются транзакции. Поэтому читается отдельно и с избыточностью.
    """

    beginning_balance: Decimal | None = None
    ending_balance: Decimal | None = None
    deposits_total: Decimal | None = None
    deposits_count: int | None = None
    withdrawals_total: Decimal | None = None
    withdrawals_count: int | None = None
    sources: dict[str, Source] = msgspec.field(default_factory=dict)

    def self_check(self) -> Decimal | None:
        """Шапка самопроверяема: beginning + deposits - withdrawals == ending."""
        beginning, ending = self.beginning_balance, self.ending_balance
        deposits, withdrawals = self.deposits_total, self.withdrawals_total

        if beginning is None or ending is None or deposits is None or withdrawals is None:
            return None

        return beginning + deposits - withdrawals - ending

    def to_payload(self) -> Payload:
        payload: Payload = {
            'beginning_balance': money_out(self.beginning_balance),
            'ending_balance': money_out(self.ending_balance),
            'deposits_total': money_out(self.deposits_total),
            'deposits_count': self.deposits_count,
            'withdrawals_total': money_out(self.withdrawals_total),
            'withdrawals_count': self.withdrawals_count,
        }

        if self.sources:
            payload['sources'] = {k: v.to_payload() for k, v in self.sources.items()}

        return payload


class UnplacedRow(msgspec.Struct, frozen=True):
    """Строка-кандидат, которую разбор не превратил в операцию."""

    date: date | None
    description: str
    amounts: list[Decimal]
    reason: str
    kind: str = 'lost'
    source: Source | None = None

    def to_payload(self) -> Payload:
        payload: Payload = {
            'date': self.date.isoformat() if self.date else None,
            'description': self.description,
            'amounts': [money_out(a) for a in self.amounts],
            'reason': self.reason,
            'kind': self.kind,
            'source': self.source.to_payload() if self.source else None,
        }

        return payload


class Losses(msgspec.Struct, frozen=True):
    """Что разбор разместил, что изменил и чего не разместил."""

    placed: int = 0
    repaired: int = 0
    proven: bool = False
    """Состав операций доказан: цепочка подтвердила каждую строку, либо совпали
    оба напечатанных оборота и каждая неразмещённая строка объяснена."""
    unplaced: list[UnplacedRow] = msgspec.field(default_factory=list)

    @property
    def lost(self) -> list[UnplacedRow]:
        """Настоящие потери — то, из-за чего заключению нельзя быть чистым.

        У доказанного периода потерь нет по построению: если каждая размещённая
        строка подтверждена соседями, то добавить в период ещё одну операцию
        невозможно — цепочка разошлась бы. Значит оставшаяся строка не операция,
        а повтор сводки или служебный маркер, и объявлять её потерей значит
        поднимать ложную тревогу: на Fulton таких строк пятьдесят шесть при
        безупречном разборе. Период класса B доказанным считается лишь без
        необъяснённых строк, так что и здесь правило не прячет ничего.
        """
        if self.proven:
            return []

        return [row for row in self.unplaced if row.kind == 'lost']

    def to_payload(self) -> Payload:
        return {
            'placed': self.placed,
            'repaired': self.repaired,
            'proven': self.proven,
            'unplaced': [u.to_payload() for u in self.unplaced],
        }


class DeltaDiagnosis(msgspec.Struct, frozen=True):
    """Форма расхождения указывает на его причину.

    Превращает слепой ретрай в прицельный: агенту (или человеку) сообщается не
    «не сошлось на 68647.56», а «скорее всего задвоена вот эта строка».
    """

    kind: str
    detail: str
    candidates: list[str] = []
    rows: list[int] = []
    """Номера строк в списке операций периода, о которых идёт речь.

    Диагноз без адреса заставляет искать строку глазами по списку на двести
    позиций; с адресом интерфейс приводит прямо к ней."""

    def to_payload(self) -> Payload:
        return {
            'kind': self.kind,
            'detail': self.detail,
            'candidates': self.candidates,
            'rows': self.rows,
        }


class Reconciliation(msgspec.Struct, frozen=True):
    status: ReconStatus
    guarantee: Guarantee
    delta: Decimal
    computed_deposits: Decimal
    computed_withdrawals: Decimal
    computed_ending: Decimal | None = None
    chain_rows: int = 0
    chain_breaks: int = 0
    delta_deposits: Decimal | None = None
    delta_withdrawals: Decimal | None = None
    count_delta_deposits: int | None = None
    count_delta_withdrawals: int | None = None
    diagnoses: list[DeltaDiagnosis] = []

    @property
    def residual(self) -> Decimal:
        """Полная невязка периода: расхождение и по остатку, и по оборотам.

        Пустой разбор сходится по остатку тривиально (ничего не прибавили и не
        отняли), поэтому одного `delta` для сравнения гипотез недостаточно —
        иначе «не нашли ничего» выигрывало бы у «нашли почти всё».
        """
        parts = [abs(self.delta), abs(self.delta_deposits or ZERO), abs(self.delta_withdrawals or ZERO)]

        return sum(parts, ZERO)

    def to_payload(self) -> Payload:
        return {
            'status': self.status.value,
            'guarantee': self.guarantee.value,
            'delta': money_out(self.delta),
            'delta_deposits': money_out(self.delta_deposits),
            'delta_withdrawals': money_out(self.delta_withdrawals),
            'computed_deposits': money_out(self.computed_deposits),
            'computed_withdrawals': money_out(self.computed_withdrawals),
            'computed_ending': money_out(self.computed_ending),
            'chain_rows': self.chain_rows,
            'chain_breaks': self.chain_breaks,
            'count_delta': {
                'deposits': self.count_delta_deposits,
                'withdrawals': self.count_delta_withdrawals,
            },
            'diagnoses': [d.to_payload() for d in self.diagnoses],
        }


class StatementPeriod(msgspec.Struct, frozen=True):
    """Один период. В бандле их может быть много (Binder2 — десять)."""

    account: Account
    summary: Summary
    transactions: list[Transaction]
    reconciliation: Reconciliation
    pages: list[int] = []
    losses: Losses = msgspec.field(default_factory=Losses)

    def to_payload(self) -> Payload:
        payload: Payload = {
            'account': self.account.to_payload(),
            'summary': self.summary.to_payload(),
            'transactions': [t.to_payload() for t in self.transactions],
            'reconciliation': self.reconciliation.to_payload(),
            'pages': self.pages,
        }

        if self.losses.placed or self.losses.repaired or self.losses.unplaced:
            payload['losses'] = self.losses.to_payload()

        return payload


class ExtractionResult(msgspec.Struct, frozen=True):
    """Ответ надмножеством контракта из ТЗ.

    Плоские account/summary/transactions повторяют первый период — так
    одно-периодный контракт выполняется буквально, а многопериодный бандл
    не ломает формат.
    """

    periods: list[StatementPeriod]
    source_file: str | None = None
    engine_stats: dict[str, int] = {}
    """Сколько страниц прочёл каждый движок, с перечитами: это строка о цене,
    и страница, отданная модели дважды, стоила дважды."""
    page_count: int = 0
    truncated: bool = False
    """Разбор остановлен по времени: выписки после последней готовой не
    разобраны, и заключение по документу целиком невозможно."""

    @property
    def reconciled_periods(self) -> int:
        return sum(1 for p in self.periods if p.reconciliation.status is ReconStatus.RECONCILED)

    @property
    def opinion(self) -> Opinion:
        """Итог по документу целиком — то, ради чего его открыли."""
        if not self.periods:
            return Opinion.DISCLAIMER

        # Сведённый период доказан арифметикой документа, но доказательство
        # относится к тому, что разбор разместил. Строка с датой и суммой,
        # оставшаяся за бортом и не объяснённая ни маркером остатка, ни дублем
        # сводки, — это то, о чём заключение обязано предупредить.
        has_lost = any(p.losses.lost for p in self.periods)

        if self.reconciled_periods == len(self.periods) and not has_lost and not self.truncated:
            return Opinion.CLEAN

        return Opinion.QUALIFIED if self.reconciled_periods else Opinion.ADVERSE

    @property
    def failures(self) -> list[StatementPeriod]:
        """Что требует человека — по убыванию расхождения.

        Порядок именно такой, а не документный: расхождение в копейку и
        расхождение в двенадцать тысяч требуют разного внимания, и первым
        читатель должен увидеть второе.
        """
        failed = [p for p in self.periods if p.reconciliation.status is not ReconStatus.RECONCILED]

        return sorted(failed, key=lambda p: abs(p.reconciliation.delta), reverse=True)

    @property
    def unexplained(self) -> Decimal:
        """Сумма невязок по несведённым периодам.

        Именно полной невязки, а не дельты по остатку: период, у которого
        сошёлся остаток, но разошлись обороты, не сведён, и показать по нему
        «нераскрыто 0» значило бы спрятать расхождение.
        """
        return sum((p.reconciliation.residual for p in self.failures), ZERO)

    @property
    def transactions_total(self) -> int:
        return sum(len(p.transactions) for p in self.periods)

    @property
    def pages_total(self) -> int:
        """Страниц в документе.

        Сумма по движкам здесь не годится: страница, перечитанная моделью,
        учтена у каждого, кто её читал. Сумма остаётся запасным вариантом для
        результата, собранного без документа.
        """
        return self.page_count or sum(self.engine_stats.values())

    def to_payload(self) -> Payload:
        first = self.periods[0] if self.periods else None

        return {
            'account': first.account.to_payload() if first else None,
            'summary': first.summary.to_payload() if first else None,
            'transactions': [t.to_payload() for t in first.transactions] if first else [],
            'periods': [p.to_payload() for p in self.periods],
            'meta': {
                'source_file': self.source_file,
                'opinion': self.opinion.value,
                'periods_total': len(self.periods),
                'periods_reconciled': self.reconciled_periods,
                'unexplained': money_out(self.unexplained),
                'engine_stats': self.engine_stats,
                'pages_total': self.pages_total,
                'truncated': self.truncated,
            },
        }

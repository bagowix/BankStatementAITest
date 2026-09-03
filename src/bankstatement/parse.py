"""Превращение строк страницы в транзакции — несколькими способами сразу.

Единственного верного разбора не существует: одна выписка печатает сумму со
знаком, другая — двумя колонками, третья вообще без колонки баланса. Поэтому
здесь не одна процедура с ветвлениями по банку, а несколько независимых гипотез
о том, как читать строку. Какая из них верна, решает не код, а арифметика
документа: гипотезы отдаются оракулу, и выигрывает сошедшаяся.

Сильнейшая гипотеза — цепочка running balance. В ней сумма и знак операции не
угадываются, а вычисляются: balance[i] - balance[i-1]. Колонка при этом
становится не источником, а подтверждением, и ошибка распознавания в одной
строке остаётся в этой строке.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import product
from typing import Final

import msgspec

from bankstatement.layout import Line
from bankstatement.models import Summary, Transaction, UnplacedRow
from bankstatement.money import parse_money, plausible_misreading, reading_variants
from bankstatement.rows import DateRange, Hypothesis, Row, collect_rows
from bankstatement.sections import BALANCE_MARKER, section_hypothesis

ZERO = Decimal(0)

MAX_REPAIR_ROWS: Final[int] = 40
"""Ремонт по вариантам прочтения — точечный инструмент. Если чинить приходится
десятки строк, дело не в отдельных символах, а в неверной гипотезе, и молчаливый
подбор превратился бы в подгонку результата под ответ."""

MAX_PENDING: Final[int] = 8
"""Сколько строк без остатка цепочка держит подвешенными, прежде чем сдаться.
Больше восьми подряд указывает на другую раскладку страницы, а не на потерю
отдельных остатков."""


@dataclass(slots=True)
class _Chain:
    """Состояние прохода по цепочке остатков.

    Вынесено из цикла в объект намеренно: у прохода четыре переменные состояния
    и пять способов закрыть строку, и в одной длинной функции они путаются.
    Здесь каждый способ — отдельный короткий метод, а состояние названо.
    """

    rows: list[Row]
    period: DateRange | None
    previous: Decimal
    printed: frozenset[Decimal] = frozenset()
    """Напечатанные остатки шапки: строка-маркер с таким числом — не операция."""
    out: list[Transaction] = field(default_factory=list)
    pending: list[tuple[Row, Decimal]] = field(default_factory=list)
    suspect: set[int] = field(default_factory=set)
    repaired: int = 0
    skipped: int = 0
    unplaced_rows: list['UnplacedRow'] = field(default_factory=list)

    def feed(self, index: int, row: Row) -> None:
        """Обработать очередную строку-кандидата."""
        if not row.money:
            return

        last = parse_money(row.money[-1].text)

        if last is None:
            self.skipped += 1

            return

        if self._is_marker(row):
            return

        if len(row.money) == 1:
            self._lonely_amount(row, last)

            return

        amount = parse_money(row.money[-2].text)

        if self.pending and self._settle_pending(row, amount, last):
            return

        signed = last - self.previous

        if signed == 0 and amount == 0:
            # Нулевая сумма при неизменном остатке — строка сводки остатков по
            # дням, а не операция: операция баланс двигает. Считать её строкой
            # значило бы раздувать список и ломать счётчики из шапки.
            return

        if amount is not None and abs(amount) == abs(signed):
            self._commit(row, signed, last)

            return

        self._unconfirmed(index, row, amount, last)

    def _is_marker(self, row: Row) -> bool:
        """Строка остатка, замаскированная под операцию.

        Обычно маркер отсекает сам остаток: он баланс не двигает. Но «Sep 30
        ENDING BALANCE −4.00» рядом с колонкой дневных остатков несёт два
        числа, и второе случайно продолжило цепочку. Подпись об остатке плюс
        совпадение с напечатанным итогом — достаточная улика, чтобы не считать
        такую строку операцией; в потери она уходит как маркер, не как потеря.
        """
        if not self.printed or not BALANCE_MARKER.search(row.description):
            return False

        amounts = [value for word in row.money if (value := parse_money(word.text)) is not None]

        if not any(abs(value) in self.printed for value in amounts):
            return False

        self.unplaced_rows.append(
            UnplacedRow(
                date=row.date,
                description=row.description,
                amounts=amounts,
                reason='balance_marker',
                kind='marker',
                source=row.source,
            )
        )

        return True

    def _lonely_amount(self, row: Row, last: Decimal) -> None:
        """Одинокая сумма: либо маркер известного остатка, либо потерянный баланс."""
        if last == self.previous:
            return  # маркер уже известного баланса, не операция

        # Сумма есть, а баланс рядом с ней не прочитался. Строку не выбрасываем:
        # её вернёт следующая строка с целым балансом.
        self.pending.append((row, last))
        self.suspect.add(row.line.page)

    def _settle_pending(self, row: Row, amount: Decimal | None, last: Decimal) -> bool:
        """Попробовать закрыть подвешенные строки этой. True — строка обработана."""
        resolved = _resolve_pending(self.pending, amount, last - self.previous)

        if resolved is None:
            if len(self.pending) >= MAX_PENDING:
                self._record_unplaced_pending('pending_overflow')
                self.skipped += len(self.pending)
                self.pending = []

            return False

        self.previous = _emit_pending(self.out, self.pending, self.previous)
        self.repaired += len(self.pending)
        self.pending = []
        self._commit(row, resolved, last)

        return True

    def _unconfirmed(self, index: int, row: Row, amount: Decimal | None, last: Decimal) -> None:
        """Строка не подтвердилась: чиним, пересинхронизируемся или откладываем."""
        fixed = _repair(row, self.previous, last, amount, self.repaired, self.rows, index, self.period)
        typo = _balance_by_typo(self.rows, index, self.previous, last, amount, self.period, self.repaired)
        self.suspect.add(row.line.page)

        if fixed is not None:
            self.repaired += 1
            self._commit(row, fixed[0], fixed[1], fixed[2])
        elif typo is not None:
            self.repaired += 1
            self._commit(row, typo[0], typo[1], typo[2])
        elif amount is not None and _continues_chain(self.rows, index, last, self.period):
            # Строка не сходится с нашим остатком, но следующая сходится с её
            # остатком. Значит между ними потеряна операция, а не эта строка
            # ошибочна: цепляемся за новый достоверный остаток и идём дальше.
            # Период всё равно не сойдётся — потерю покажет дельта, и это
            # честнее, чем бросить оставшиеся сто строк.
            self.skipped += 1
            self._commit(row, _closest_sign(amount, self.previous, last), last)
        elif amount is not None:
            # Ни починить, ни подтвердить. Баланс этой строки за основу не берём:
            # цепочка держится за последнее подтверждённое значение, а строка
            # уходит в подвешенные — следующая ещё может её объяснить.
            self.pending.append((row, amount))
        else:
            self.skipped += 1

    def _commit(self, row: Row, signed: Decimal, balance: Decimal, printed: str | None = None) -> None:
        self.out.append(_transaction(row, signed, balance, repaired=printed is not None, printed=printed))
        self.previous = balance

    def _record_unplaced_pending(self, reason: str) -> None:
        """Записать потерянные pending строки перед забыванием."""
        for row, value in self.pending:
            amounts = [value]

            if len(row.money) > 1:
                # Попробаем собрать все денежные токены из строки
                for token in row.money[:-1]:
                    parsed = parse_money(token.text)

                    if parsed is not None and parsed not in amounts:
                        amounts.append(parsed)

            unplaced = UnplacedRow(
                date=row.date,
                description=row.description,
                amounts=amounts,
                reason=reason,
                source=row.source,
            )
            self.unplaced_rows.append(unplaced)
            self.suspect.add(row.line.page)

    def finish(self) -> Hypothesis:
        # Записать оставшиеся pending перед finish
        if self.pending:
            self._record_unplaced_pending('unconfirmed_no_path')
            self.skipped += len(self.pending)

        return Hypothesis(
            name='chain',
            transactions=self.out,
            repaired=self.repaired,
            skipped=self.skipped,
            suspect_pages=frozenset(self.suspect),
            unplaced_rows=self.unplaced_rows,
        )


def chain_hypothesis(lines: list[Line], summary: Summary, period: DateRange | None) -> Hypothesis:
    """Последний денежный токен строки — running balance.

    Операцией считается только строка, которая баланс двигает. Это и отсекает
    служебные строки («ENDING BALANCE FROM PRIOR STATEMENT»), и сводные блоки,
    повторяющие операции без колонки баланса, — без единого правила про
    конкретный банк.
    """
    if summary.beginning_balance is None:
        return Hypothesis(name='chain', transactions=[])

    rows = collect_rows(lines, period)
    printed = frozenset(
        abs(value) for value in (summary.beginning_balance, summary.ending_balance) if value is not None
    )
    chain = _Chain(rows=rows, period=period, previous=summary.beginning_balance, printed=printed)

    for index, row in enumerate(rows):
        chain.feed(index, row)

    return chain.finish()


def _balance_by_typo(
    rows: list[Row],
    index: int,
    previous: Decimal,
    balance: Decimal,
    amount: Decimal | None,
    period: DateRange | None,
    already: int = 0,
) -> tuple[Decimal, Decimal, str] | None:
    """Остаток строки прочитан с одной перепутанной цифрой.

    Сумма строки задаёт остаток однозначно, поэтому кандидат не подбирается, а
    вычисляется. И всё же одной похожести мало: потерянная операция способна
    дать разницу, которая случайно выглядит как опечатка. Поэтому правка
    принимается только если её подтверждает следующая строка — та самая
    избыточность, ради которой в выписке и печатают остаток в каждой строке.
    """
    if already >= MAX_REPAIR_ROWS or amount is None:
        return None

    for required in (previous + abs(amount), previous - abs(amount)):
        if plausible_misreading(balance, required) and _continues_chain(rows, index, required, period):
            return required - previous, required, rows[index].money[-1].text

    return None


def _continues_chain(rows: list[Row], index: int, balance: Decimal, period: DateRange | None) -> bool:
    """Подтверждает ли следующая строка остаток этой.

    Проверка отделяет потерянную операцию от чужого блока. Строки сводки
    остатков по дням тоже выглядят как «сумма и остаток», но между собой в
    цепочку не складываются — и якорь на них не переносится.
    """
    for following in rows[index + 1 : index + 2]:
        if len(following.money) < 2:
            return False

        next_balance = parse_money(following.money[-1].text)
        next_amount = parse_money(following.money[-2].text)

        if next_balance is None or next_amount is None:
            return False

        return abs(next_balance - balance) == abs(next_amount)

    return False


def _closest_sign(amount: Decimal, previous: Decimal, balance: Decimal) -> Decimal:
    """Знак суммы, при котором остаток ближе к тому, что был до потери."""
    plus, minus = abs(amount), -abs(amount)

    return plus if abs(previous + plus - balance) <= abs(previous + minus - balance) else minus


def _resolve_pending(pending: list[tuple[Row, Decimal]], amount: Decimal | None, signed: Decimal) -> Decimal | None:
    """Разложить скачок баланса на подвешенные суммы и сумму текущей строки.

    Когда у строки не прочитался баланс, её сумма остаётся «подвешенной», а
    следующая целая строка задаёт суммарный сдвиг. Если сдвиг в точности
    раскладывается на эти суммы с некоторым распределением знаков, все строки
    восстановлены: цепочка сама вернула то, что потеряло распознавание.

    Перебор знаков полный: подвешенных строк не бывает больше MAX_PENDING —
    цепочка сбрасывает их раньше, — и 2^9 комбинаций считаются мгновенно.
    """
    if amount is None or len(pending) > MAX_PENDING:
        return None

    amounts = [value for _, value in pending]

    for signs in product((1, -1), repeat=len(amounts) + 1):
        total = sum((s * abs(a) for s, a in zip(signs, [*amounts, amount], strict=True)), ZERO)

        if total == signed:
            for slot, (row, value) in enumerate(pending):
                pending[slot] = (row, signs[slot] * abs(value))

            return signs[-1] * abs(amount)

    return None


def _emit_pending(out: list[Transaction], pending: list[tuple[Row, Decimal]], previous: Decimal) -> Decimal:
    """Выпустить подвешенные строки: их остаток вычислен цепочкой, а не прочитан.

    Признак правки здесь обязателен, даже когда перекрывать нечего: у одинокой
    суммы колонка баланса не распозналась вовсе, и напечатанного токена под
    вычисленным числом просто нет.
    """
    for row, value in pending:
        previous = previous + value
        printed = row.money[-1].text if len(row.money) > 1 else None
        out.append(_transaction(row, value, previous, repaired=True, printed=printed))

    return previous


def _repair(
    row: Row,
    previous: Decimal,
    balance: Decimal,
    amount: Decimal | None,
    already: int,
    rows: list[Row],
    index: int,
    period: DateRange | None,
) -> tuple[Decimal, Decimal, str] | None:
    """Согласовать сумму и баланс строки, перебрав правдоподобные прочтения.

    Чинится ровно одно число из двух: сначала сумма (её ошибка локальна), затем
    баланс (его ошибка ломает и следующую строку). Ремонт всегда обязан привести
    к точному равенству — приближения здесь не бывает.

    Для баланса требуется подтверждение от следующей строки, как в _balance_by_typo.
    Для суммы — тоже, но только когда правка сдвигает разряд: подмена символа
    даёт совпадение случайно редко, а сдвиг точки превращает верные 120.00 в
    12.00 всякий раз, когда соседние остатки отличаются ровно на 12.

    Returns:
        Сумма со знаком, остаток и напечатанный токен, который правка перекрыла.
    """
    if already >= MAX_REPAIR_ROWS:
        return None

    signed = balance - previous
    printed = parse_money(row.money[-2].text)
    shifted = {printed / 10, printed * 10} if printed is not None else set()

    for candidate in reading_variants(row.money[-2].text):
        if abs(candidate) != abs(signed):
            continue

        if candidate in shifted and not _continues_chain(rows, index, balance, period):
            continue

        return signed, balance, row.money[-2].text

    if amount is None:
        return None

    for candidate in reading_variants(row.money[-1].text):
        if abs(candidate - previous) == abs(amount) and _continues_chain(rows, index, candidate, period):
            return candidate - previous, candidate, row.money[-1].text

    return None


def _transaction(
    row: Row, signed: Decimal, balance: Decimal, repaired: bool = False, printed: str | None = None
) -> Transaction:
    """Операция из строки, с признаком правки в провенансе.

    Аудитору мало итогового числа: он обязан видеть, что на этом месте
    напечатано другое и что число здесь выбрано кодом. Признак и напечатанное
    чтение разделены нарочно — правка бывает и там, где перекрывать нечего: у
    одинокой суммы колонка баланса не распозналась вовсе.

    Args:
        row: Строка-кандидат.
        signed: Сумма со знаком.
        balance: Остаток после операции.
        repaired: Число получено правкой, а не прочитано.
        printed: Что стояло в документе на месте исправленного числа.

    Returns:
        Операция с провенансом.
    """
    source = msgspec.structs.replace(row.source, repaired=True, printed=printed) if repaired else row.source

    return Transaction(
        date=row.date,
        description=row.description,
        deposit=signed if signed > 0 else None,
        withdrawal=-signed if signed < 0 else None,
        balance=balance,
        source=source,
    )


def hypotheses(lines: list[Line], summary: Summary, period: DateRange | None) -> list[Hypothesis]:
    """Все прочтения страницы. Выбор между ними — не здесь, а у оракула."""
    return [
        chain_hypothesis(lines, summary, period),
        section_hypothesis(lines, summary, period),
    ]

"""Оценочный стенд: единственная регрессия, которая что-то значит.

Главная метрика — доля периодов, которые документ подтвердил сам: у каждой
выписки есть собственная арифметика, и такая проверка переносится на незнакомый
файл без изменений. Эталон есть только у шапок выданных файлов
(`evaluation/truth`, сверен глазами по изображению первой страницы каждой
выписки); по нему считается точное совпадение полей — критерий задания, который
арифметикой не измерить: имя банка и номер счёта в уравнение не входят.

Вторая проверка — сшивка: в подшивке конец одного периода обязан совпасть с
началом следующего по тому же счёту. Она внешняя по отношению к разбору строк и
ловит то, чего оракул одного периода увидеть не может: потерянную выписку и
неверную границу между ними.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Final

from bankstatement.extract import extract_result
from bankstatement.models import ExtractionResult, Guarantee, Payload, ReconStatus, StatementPeriod

TRUTH_DIR: Final[Path] = Path('evaluation/truth')
"""Эталоны шапок: файл на документ, имя — имя PDF без расширения."""

HEADER_FIELDS: Final[tuple[str, ...]] = (
    'start',
    'end',
    'bank',
    'account_last4',
    'beginning_balance',
    'deposits_total',
    'deposits_count',
    'withdrawals_total',
    'withdrawals_count',
    'ending_balance',
)
"""Поля шапки, по которым считается точное совпадение. Поле, которого банк не
печатает, в эталоне пусто и не считается."""


@dataclass(frozen=True, slots=True)
class FileScore:
    name: str
    pages: int
    periods: int
    reconciled: int
    transactions: int
    chain_periods: int
    aggregate_periods: int
    seconds: float
    engines: dict[str, int]
    continuity_ok: int
    continuity_total: int
    unplaced_total: int = 0
    failures: list[str] = field(default_factory=list)
    header_exact: int = 0
    header_fields: int = 0
    header_misses: list[str] = field(default_factory=list)

    @property
    def header(self) -> str:
        """Совпавших полей шапки из проверенных, или прочерк без эталона."""
        return f'{self.header_exact}/{self.header_fields}' if self.header_fields else '—'

    @property
    def clean(self) -> bool:
        """Все периоды сведены и стыкуются. Ноль периодов — не успех, а пустой файл."""
        return self.periods > 0 and self.reconciled == self.periods and self.continuity_ok == self.continuity_total


@dataclass(frozen=True, slots=True)
class Evaluation:
    files: list[FileScore]

    @property
    def periods(self) -> int:
        return sum(f.periods for f in self.files)

    @property
    def reconciled(self) -> int:
        return sum(f.reconciled for f in self.files)

    @property
    def transactions(self) -> int:
        return sum(f.transactions for f in self.files)

    @property
    def header_exact(self) -> int:
        return sum(f.header_exact for f in self.files)

    @property
    def header_fields(self) -> int:
        return sum(f.header_fields for f in self.files)

    def to_payload(self) -> Payload:
        return {
            'files': [
                {
                    'name': f.name,
                    'pages': f.pages,
                    'periods': f.periods,
                    'reconciled': f.reconciled,
                    'transactions': f.transactions,
                    'guarantee_a': f.chain_periods,
                    'guarantee_b': f.aggregate_periods,
                    'unplaced': f.unplaced_total,
                    'seconds': round(f.seconds, 1),
                    'engines': f.engines,
                    'continuity': f'{f.continuity_ok}/{f.continuity_total}',
                    'failures': f.failures,
                    'header': f.header,
                    'header_misses': f.header_misses,
                }
                for f in self.files
            ],
            'totals': {
                'periods': self.periods,
                'reconciled': self.reconciled,
                'transactions': self.transactions,
                'header': f'{self.header_exact}/{self.header_fields}',
            },
        }


def continuity(result: ExtractionResult) -> tuple[int, int]:
    """Сколько стыков между смежными выписками одного счёта сошлись.

    Проверяются только периоды, примыкающие по времени: в подшивке выписки лежат
    не по порядку и с пропусками, и требовать стыковки через полугодовой разрыв
    нельзя — там просто нет выписки, а не потеря. Зато у примыкающей пары конец
    одного периода обязан быть началом следующего, и это проверка, внешняя по
    отношению к разбору строк: она ловит потерянную выписку и неверную границу
    между ними — то, чего оракул одного периода увидеть не может.
    """
    ok = total = 0
    by_account: dict[str, list[StatementPeriod]] = {}

    for period in result.periods:
        if period.account.period is None:
            continue

        by_account.setdefault(period.account.account_last4 or 'unknown', []).append(period)

    for chain in by_account.values():
        chain.sort(key=lambda p: p.account.period.start if p.account.period else date.min)

        for previous, following in pairwise(chain):
            if not _adjacent(previous, following):
                continue

            ending, beginning = previous.summary.ending_balance, following.summary.beginning_balance

            if ending is None or beginning is None:
                continue

            total += 1
            ok += int(ending == beginning)

    return ok, total


ADJACENT_SLACK: Final[timedelta] = timedelta(days=3)
"""Насколько начало следующей выписки может отстоять от конца предыдущей. Банки
закрывают период в разные календарные дни, но не через неделю."""


def _adjacent(previous: StatementPeriod, following: StatementPeriod) -> bool:
    left, right = previous.account.period, following.account.period

    if left is None or right is None:
        return False

    return right.start - left.end <= ADJACENT_SLACK


def load_truth(pdf: Path, truth_dir: Path = TRUTH_DIR) -> list[dict[str, object]] | None:
    """Эталон шапок документа, если он размечен."""
    path = truth_dir / f'{pdf.stem}.json'

    if not path.is_file():
        return None

    data = json.loads(path.read_text(encoding='utf-8'))

    return list(data['periods'])


def _extracted_header(period: StatementPeriod) -> dict[str, object]:
    account, summary = period.account, period.summary

    return {
        'start': account.period.start.isoformat() if account.period else None,
        'end': account.period.end.isoformat() if account.period else None,
        'bank': account.bank,
        'account_last4': account.account_last4,
        'beginning_balance': summary.beginning_balance,
        'deposits_total': summary.deposits_total,
        'deposits_count': summary.deposits_count,
        'withdrawals_total': summary.withdrawals_total,
        'withdrawals_count': summary.withdrawals_count,
        'ending_balance': summary.ending_balance,
    }


def _same(got: object, expected: object) -> bool:
    if isinstance(expected, float | int) and not isinstance(expected, bool):
        return got is not None and Decimal(str(expected)) == Decimal(str(got))

    return got == expected


def _pair_up(result: ExtractionResult, truth: list[dict[str, object]]) -> list[StatementPeriod | None]:
    """Сопоставить эталонные периоды с извлечёнными.

    Если их поровну, порядок документа и есть сопоставление. Иначе — по датам и
    номеру счёта: в подшивке две выписки за один месяц по разным счетам.
    """
    if len(result.periods) == len(truth):
        return list(result.periods)

    unused = list(result.periods)
    paired: list[StatementPeriod | None] = []

    for expected in truth:
        match = next(
            (
                p
                for p in unused
                if _extracted_header(p)['start'] == expected['start']
                and _extracted_header(p)['end'] == expected['end']
                and expected.get('account_last4') in (None, p.account.account_last4)
            ),
            None,
        )
        paired.append(match)

        if match is not None:
            unused.remove(match)

    return paired


def header_score(result: ExtractionResult, truth: list[dict[str, object]]) -> tuple[int, int, list[str]]:
    """Сколько полей шапки совпало с эталоном точно.

    Args:
        result: Извлечённый документ.
        truth: Эталонные шапки, по одной на выписку, в порядке документа.

    Returns:
        Совпавших полей, проверенных полей и список расхождений словами.
    """
    exact = total = 0
    misses: list[str] = []

    for expected, period in zip(truth, _pair_up(result, truth), strict=True):
        got = _extracted_header(period) if period is not None else {}

        for name in HEADER_FIELDS:
            if expected.get(name) is None:
                continue

            total += 1

            if period is not None and _same(got.get(name), expected[name]):
                exact += 1
            else:
                misses.append(f'{expected["start"]}: {name} = {got.get(name)!s}, эталон {expected[name]!s}')

    return exact, total, misses


def score_file(path: Path, truth_dir: Path = TRUTH_DIR) -> FileScore:
    started = time.monotonic()
    result = extract_result(path)
    elapsed = time.monotonic() - started
    truth = load_truth(path, truth_dir)
    header_exact, header_fields, header_misses = header_score(result, truth) if truth is not None else (0, 0, [])

    reconciled = [p for p in result.periods if p.reconciliation.status is ReconStatus.RECONCILED]
    ok, total = continuity(result)
    failures = [
        f'{p.account.period.start if p.account.period else "?"}: дельта {p.reconciliation.delta}'
        + (f', невязка {p.reconciliation.residual}' if p.reconciliation.residual != abs(p.reconciliation.delta) else '')
        + f' ({", ".join(d.kind for d in p.reconciliation.diagnoses) or "без диагноза"})'
        for p in result.periods
        if p.reconciliation.status is not ReconStatus.RECONCILED
    ]
    unplaced_total = sum(len(p.losses.lost) for p in result.periods)

    return FileScore(
        name=path.name,
        pages=result.pages_total,
        periods=len(result.periods),
        reconciled=len(reconciled),
        transactions=sum(len(p.transactions) for p in result.periods),
        chain_periods=sum(1 for p in reconciled if p.reconciliation.guarantee is Guarantee.CHAIN),
        aggregate_periods=sum(1 for p in reconciled if p.reconciliation.guarantee is Guarantee.AGGREGATE),
        seconds=elapsed,
        engines=result.engine_stats,
        continuity_ok=ok,
        continuity_total=total,
        unplaced_total=unplaced_total,
        failures=failures,
        header_exact=header_exact,
        header_fields=header_fields,
        header_misses=header_misses,
    )


def evaluate_paths(paths: list[Path]) -> Evaluation:
    return Evaluation(files=[score_file(path) for path in paths])


def render_table(report: Evaluation) -> str:
    header = (
        f'{"файл":<38} {"стр":>4} {"периодов":>9} {"сведено":>8} {"A/B":>7} '
        f'{"транзакций":>11} {"потерь":>7} {"шапка":>8} {"стык":>6} {"сек":>6}'
    )
    rows = [header, '-' * len(header)]

    for score in report.files:
        rows.append(
            f'{score.name[:38]:<38} {score.pages:>4} {score.periods:>9} {score.reconciled:>8} '
            f'{f"{score.chain_periods}/{score.aggregate_periods}":>7} {score.transactions:>11} '
            f'{score.unplaced_total:>7} {score.header:>8} {f"{score.continuity_ok}/{score.continuity_total}":>6} '
            f'{score.seconds:>6.1f}'
        )

    rows.append('-' * len(header))
    rows.append(
        f'{"итого":<38} {"":>4} {report.periods:>9} {report.reconciled:>8} {"":>7} {report.transactions:>11} '
        f'{"":>7} {f"{report.header_exact}/{report.header_fields}":>8}'
    )

    problems = [f'{s.name}: {f}' for s in report.files for f in s.failures]

    if problems:
        rows.extend(['', 'не сведено:', *(f'  - {p}' for p in problems)])

    misses = [f'{s.name}: {m}' for s in report.files for m in s.header_misses]

    if misses:
        rows.extend(['', 'шапка мимо эталона:', *(f'  - {m}' for m in misses)])

    return '\n'.join(rows)


TABLE_MARKER: Final[str] = '<!-- EVAL_TABLE -->'
NOTES_MARKER: Final[str] = '<!-- EVAL_NOTES -->'


def render_markdown(report: Evaluation) -> str:
    """Та же таблица в разметке — README собирается из замера, а не из памяти."""
    rows = [
        '| файл | стр. | периодов | сведено | A/B | транзакций | потерь | шапка | стык | сек |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]

    for score in report.files:
        rows.append(
            f'| {score.name} | {score.pages} | {score.periods} | {score.reconciled} | '
            f'{score.chain_periods}/{score.aggregate_periods} | {score.transactions} | '
            f'{score.unplaced_total} | {score.header} | {score.continuity_ok}/{score.continuity_total} | '
            f'{score.seconds:.1f} |'
        )

    rows.append(
        f'| **итого** | | **{report.periods}** | **{report.reconciled}** | | **{report.transactions}** | | '
        f'**{report.header_exact}/{report.header_fields}** | | |'
    )

    return '\n'.join(rows)


def render_notes(report: Evaluation) -> str:
    """Расшифровка того, что именно не сошлось: отчёт без этого неполон."""
    clean = [s.name for s in report.files if s.clean]
    lines = [
        f'Полностью сведены: {", ".join(clean) if clean else "нет"}.',
    ]
    problems = [(s.name, f) for s in report.files for f in s.failures]

    if problems:
        lines.extend(['', 'Периоды с расхождением (каждый локализован до строки):', ''])
        lines.extend(f'- `{name}` — {failure}' for name, failure in problems)

    if report.header_fields:
        lines.extend(
            [
                '',
                f'Шапка против эталона (`evaluation/truth`, сверен по изображению первой страницы каждой '
                f'выписки): {report.header_exact} из {report.header_fields} полей точно.',
            ]
        )
        misses = [(s.name, m) for s in report.files for m in s.header_misses]

        if misses:
            lines.append('')
            lines.extend(f'- `{name}` — {miss}' for name, miss in misses)

    return '\n'.join(lines)


def update_readme(path: Path, report: Evaluation) -> None:
    """Подставить свежий замер в README между парами маркеров.

    Маркеры обязаны идти парами: без закрывающего непонятно, где кончается
    сгенерированный блок, и подстановка съела бы остаток файла.
    """
    text = path.read_text(encoding='utf-8')

    for marker, block in ((TABLE_MARKER, render_markdown(report)), (NOTES_MARKER, render_notes(report))):
        if text.count(marker) != 2:
            raise ValueError(f'в {path} должно быть ровно два маркера {marker}, найдено {text.count(marker)}')

        head, _, rest = text.partition(marker)
        _, _, tail = rest.partition(marker)
        text = f'{head}{marker}\n\n{block}\n\n{marker}{tail}'

    # Маркер может оказаться последним в файле: без этого подстановка каждый раз
    # срезает перевод строки в конце, и правка попадает в каждый diff.
    path.write_text(text if text.endswith('\n') else f'{text}\n', encoding='utf-8')

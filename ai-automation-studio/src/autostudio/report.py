"""Месячный отчёт клиенту: что автоматизация сделала за его абонентскую плату.

Отчёт — главный довод продлить поддержку, поэтому он честный: считает только то,
что лежит в журнале событий, а сэкономленное время подписано как оценка с
указанием, из скольких минут на одно действие она получена.
"""

import datetime as dt
import html
from collections import Counter
from collections.abc import Sequence
from typing import Final

import msgspec

from autostudio.events import Event

_RUN_OK: Final = 'system.run_ok'
_RUN_FAILED: Final = 'system.run_failed'


class MetricSpec(msgspec.Struct, frozen=True, kw_only=True):
    """Как событие выглядит в отчёте.

    Attributes:
        kind: Код события в журнале.
        label: Подпись по языкам: {'en': ..., 'ru': ...}.
        minutes_saved: Сколько минут ручной работы заменяет одно событие (оценка, 0 — не считать).
    """

    kind: str
    label: dict[str, str]
    minutes_saved: float = 0.0


class MetricLine(msgspec.Struct, frozen=True, kw_only=True):
    label: str
    count: int
    minutes_saved: float


class MonthlyReport(msgspec.Struct, frozen=True, kw_only=True):
    """Готовый отчёт за месяц."""

    client: str
    month: dt.date
    language: str
    lines: list[MetricLine]
    runs_ok: int
    runs_failed: int
    highlights: list[str]

    @property
    def hours_saved(self) -> float:
        return round(sum(line.minutes_saved for line in self.lines) / 60, 1)

    @property
    def success_rate(self) -> float | None:
        total = self.runs_ok + self.runs_failed
        return None if total == 0 else round(100 * self.runs_ok / total, 1)


_TEXT: Final[dict[str, dict[str, str]]] = {
    'en': {
        'title': 'Automation report',
        'done': 'What the automation did',
        'action': 'Action',
        'count': 'Count',
        'saved': 'Time saved (est.)',
        'reliability': 'Reliability',
        'runs': 'Successful runs: {ok} of {total} ({rate}%)',
        'no_runs': 'No scheduled runs this month.',
        'hours': 'Estimated time saved: {hours} h',
        'method': 'Estimate = count × minutes a person spends on the same action by hand.',
        'highlights': 'Highlights',
        'none': 'Nothing to report this month.',
    },
    'ru': {
        'title': 'Отчёт об автоматизации',
        'done': 'Что сделала автоматизация',
        'action': 'Действие',
        'count': 'Количество',
        'saved': 'Сэкономлено (оценка)',
        'reliability': 'Надёжность',
        'runs': 'Успешных запусков: {ok} из {total} ({rate}%)',
        'no_runs': 'Плановых запусков в этом месяце не было.',
        'hours': 'Оценка сэкономленного времени: {hours} ч',
        'method': 'Оценка = количество × минуты, которые человек тратит на то же действие вручную.',
        'highlights': 'Главное за месяц',
        'none': 'За месяц событий не было.',
    },
}


def month_bounds(month: dt.date, zone: dt.tzinfo) -> tuple[dt.datetime, dt.datetime]:
    """Границы календарного месяца в часовом поясе клиента.

    Args:
        month: Любой день месяца.
        zone: Часовой пояс клиента.

    Returns:
        Начало месяца и начало следующего.
    """
    start = dt.datetime(month.year, month.month, 1, tzinfo=zone)
    nxt = dt.datetime(month.year + month.month // 12, month.month % 12 + 1, 1, tzinfo=zone)
    return start, nxt


def build_report(
    *,
    client: str,
    month: dt.date,
    language: str,
    events: Sequence[Event],
    specs: Sequence[MetricSpec],
    highlights: Sequence[str] = (),
) -> MonthlyReport:
    """Сводит события месяца в отчёт.

    Args:
        client: Название клиента.
        month: Любой день отчётного месяца.
        language: `en` или `ru` (иначе английский).
        events: События месяца.
        specs: Подписи событий пакетов.
        highlights: Готовые строки «главного» от пакетов.

    Returns:
        Отчёт.
    """
    lang = language if language in _TEXT else 'en'
    counts = Counter(event.kind for event in events)
    lines = [
        MetricLine(
            label=spec.label.get(lang) or spec.label.get('en') or spec.kind,
            count=counts[spec.kind],
            minutes_saved=counts[spec.kind] * spec.minutes_saved,
        )
        for spec in specs
        if counts[spec.kind]
    ]
    return MonthlyReport(
        client=client,
        month=month.replace(day=1),
        language=lang,
        lines=lines,
        runs_ok=counts[_RUN_OK],
        runs_failed=counts[_RUN_FAILED],
        highlights=list(highlights),
    )


def render_markdown(report: MonthlyReport) -> str:
    """Отчёт в Markdown — для письма или Telegram."""
    t = _TEXT[report.language]
    out = [f'# {t["title"]}: {report.client} — {report.month:%Y-%m}', '', f'## {t["done"]}', '']
    if report.lines:
        out += [f'| {t["action"]} | {t["count"]} | {t["saved"]} |', '|---|---:|---:|']
        out += [f'| {line.label} | {line.count} | {line.minutes_saved / 60:.1f} h |' for line in report.lines]
        out += ['', t['hours'].format(hours=report.hours_saved), '', f'_{t["method"]}_']
    else:
        out.append(t['none'])
    out += ['', f'## {t["reliability"]}', '']
    rate = report.success_rate
    total = report.runs_ok + report.runs_failed
    out.append(t['no_runs'] if rate is None else t['runs'].format(ok=report.runs_ok, total=total, rate=rate))
    if report.highlights:
        out += ['', f'## {t["highlights"]}', '']
        out += [f'- {item}' for item in report.highlights]
    return '\n'.join(out) + '\n'


def render_html(report: MonthlyReport) -> str:
    """Отчёт в самодостаточном HTML — открыть в браузере и «Печать → PDF»."""
    t = _TEXT[report.language]
    esc = html.escape
    rows = ''.join(
        f'<tr><td>{esc(line.label)}</td><td class="n">{line.count}</td>'
        f'<td class="n">{line.minutes_saved / 60:.1f} h</td></tr>'
        for line in report.lines
    )
    rate = report.success_rate
    total = report.runs_ok + report.runs_failed
    reliability = t['no_runs'] if rate is None else t['runs'].format(ok=report.runs_ok, total=total, rate=rate)
    table = (
        f'<table><tr><th>{esc(t["action"])}</th><th>{esc(t["count"])}</th><th>{esc(t["saved"])}</th></tr>{rows}'
        f'</table><p><b>{esc(t["hours"].format(hours=report.hours_saved))}</b></p>'
        f'<p class="muted">{esc(t["method"])}</p>'
        if report.lines
        else f'<p>{esc(t["none"])}</p>'
    )
    highlights = (
        f'<h2>{esc(t["highlights"])}</h2><ul>' + ''.join(f'<li>{esc(h)}</li>' for h in report.highlights) + '</ul>'
        if report.highlights
        else ''
    )
    return (
        f'<!doctype html><html lang="{report.language}"><head><meta charset="utf-8">'
        f'<title>{esc(t["title"])} — {esc(report.client)} {report.month:%Y-%m}</title>'
        '<style>body{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;color:#1d1d1f}'
        'table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:8px;text-align:left}'
        '.n{text-align:right}.muted{color:#666;font-size:13px}</style></head><body>'
        f'<h1>{esc(t["title"])}</h1><p>{esc(report.client)} · {report.month:%Y-%m}</p>'
        f'<h2>{esc(t["done"])}</h2>{table}<h2>{esc(t["reliability"])}</h2><p>{esc(reliability)}</p>{highlights}'
        '</body></html>\n'
    )

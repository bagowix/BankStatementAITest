import datetime as dt
from zoneinfo import ZoneInfo

from autostudio.events import Event
from autostudio.report import MetricSpec, build_report, month_bounds, render_html, render_markdown

SPECS = [
    MetricSpec(kind='booking.created', label={'en': 'Bookings made', 'ru': 'Записей создано'}, minutes_saved=6),
    MetricSpec(kind='booking.reminded', label={'en': 'Reminders sent'}, minutes_saved=0),
]


def _events(*kinds):
    at = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
    return [Event(at=at, package='booking', kind=k) for k in kinds]


def test__month_bounds__december__next_year():
    start, end = month_bounds(dt.date(2026, 12, 15), ZoneInfo('Asia/Tbilisi'))
    assert (start.month, end.year, end.month) == (12, 2027, 1)


def test__build_report__counts_and_hours_and_reliability():
    events = _events('booking.created', 'booking.created', 'booking.reminded', 'system.run_ok', 'system.run_failed')
    report = build_report(
        client='Acme',
        month=dt.date(2026, 10, 20),
        language='ru',
        events=events,
        specs=SPECS,
        highlights=['Пик — пятница'],
    )
    assert [(line.label, line.count) for line in report.lines] == [('Записей создано', 2), ('Reminders sent', 1)]
    assert report.hours_saved == 0.2
    assert report.success_rate == 50.0
    md = render_markdown(report)
    assert 'Записей создано | 2' in md
    assert 'Успешных запусков: 1 из 2 (50.0%)' in md
    assert '- Пик — пятница' in md


def test__build_report__no_events__honest_empty():
    report = build_report(client='Acme', month=dt.date(2026, 10, 1), language='de', events=[], specs=SPECS)
    assert report.language == 'en'
    assert report.success_rate is None
    md = render_markdown(report)
    assert 'Nothing to report' in md
    assert 'No scheduled runs' in md


def test__render_html__escapes_client_text():
    report = build_report(
        client='<script>x</script>',
        month=dt.date(2026, 10, 1),
        language='en',
        events=_events('booking.created', 'system.run_ok'),
        specs=SPECS,
        highlights=['<b>'],
    )
    page = render_html(report)
    assert '<script>x' not in page
    assert '&lt;script&gt;' in page
    assert 'Successful runs: 1 of 1' in page

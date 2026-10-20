import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from autostudio.booking.domain import (
    Booking,
    BookingStatus,
    Customer,
    TimeWindow,
    bookable_days,
    boundary_to_utc,
    clean_name,
    clean_phone,
    day_bounds,
    find_slot,
    free_slots,
    horizon_days,
    in_horizon,
    is_open_at,
    minutes_until,
    nearest_slots,
    next_free_slots,
    wall_to_utc,
)
from tests.booking.helpers import START, TUE, WED, local, make_config

BERLIN = 'Europe/Berlin'
SPRING = dt.date(2026, 3, 29)  # 02:00 CET → 03:00 CEST
AUTUMN = dt.date(2026, 10, 25)  # 03:00 CEST → 02:00 CET


def booking(start: dt.datetime, minutes: int = 45, staff: str = 'giorgi', status=BookingStatus.CONFIRMED, **kw):
    return Booking(
        id=kw.get('id', 'B00001'),
        service_id=kw.get('service', 'haircut'),
        staff_id=staff,
        start=start,
        end=start + dt.timedelta(minutes=minutes),
        customer=kw.get('customer', 'tg:1'),
        name='Zed',
        phone='+995555000111',
        status=status,
        created_at=START,
        scheduled_at=START,
        source='buttons',
        language='en',
    )


def labels(slots, zone='Asia/Tbilisi'):
    return [f'{s.start.astimezone(ZoneInfo(zone)):%H:%M}' for s in slots]


def berlin_config(**hours):
    return make_config(
        client={'timezone': BERLIN},
        hours=hours,
        staff=[{'id': 'giorgi', 'name': 'Giorgi'}],
        services=[{'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 30, 'price': '10'}],
        slot_step_min=30,
        min_lead_min=0,
        horizon_days=30,
    )


def test__free_slots__lunch_break__service_must_fit_before_interval_end(config):
    slots = free_slots(config, 'haircut', WED, [], START, staff_id='giorgi')
    shown = labels(slots)
    assert shown[0] == '10:00'
    assert '13:15' in shown and '13:30' not in shown  # 13:30 + 45 мин залезает на обед
    assert '15:00' in shown and shown[-1] == '19:15'


def test__free_slots__service_longer_than_remaining_interval__not_offered(config):
    shown = labels(free_slots(config, 'combo', WED, [], START))
    assert '12:45' in shown and '13:00' not in shown  # 75 минут до 14:00 — только до 12:45
    assert shown[-1] == '18:45'


def test__free_slots__inside_lead_time_and_past__skipped(config):
    # Сейчас 12:30 вторника, запас 60 минут: первый слот сегодня — после обеда, в 15:00.
    shown = labels(free_slots(config, 'haircut', TUE, [], START))
    assert shown[0] == '15:00'
    assert free_slots(config, 'haircut', TUE - dt.timedelta(days=1), [], START) == []


def test__free_slots__beyond_horizon__empty(config):
    last = TUE + dt.timedelta(days=13)
    assert free_slots(config, 'haircut', last, [], START)
    assert free_slots(config, 'haircut', last + dt.timedelta(days=1), [], START) == []
    assert in_horizon(config, last, START) and not in_horizon(config, last + dt.timedelta(days=1), START)
    assert len(horizon_days(config, START)) == 14


def test__free_slots__closed_date__empty():
    config = make_config(closed_dates=[WED])
    assert free_slots(config, 'haircut', WED, [], START) == []
    assert WED not in bookable_days(config, 'haircut', [], START)


def test__free_slots__sunday_closed__empty(config):
    assert free_slots(config, 'haircut', dt.date(2026, 10, 25), [], START) == []


def test__free_slots__unknown_service__empty(config):
    assert free_slots(config, 'nope', WED, [], START) == []


def test__free_slots__staff_specific_hours__nika_only_from_noon_and_not_on_tuesday(config):
    nika_wed = labels(free_slots(config, 'haircut', WED, [], START, staff_id='nika'))
    assert nika_wed[0] == '12:00' and '15:00' in nika_wed
    assert free_slots(config, 'haircut', dt.date(2026, 10, 26), [], START, staff_id='nika') == []  # пн — выходной
    assert free_slots(config, 'combo', WED, [], START, staff_id='nika') == []  # комплекс — не её услуга


def test__free_slots__busy_staff__other_staff_assigned(config):
    taken = [booking(local(WED, 12), staff='giorgi')]
    slots = free_slots(config, 'haircut', WED, taken, START)
    at_noon = next(s for s in slots if labels([s]) == ['12:00'])
    assert at_noon.staff_id == 'nika'
    # До 12:00 Ника не работает, а Джорджи занят 12:00–12:45: 11:30 не помещается.
    assert '11:30' not in labels(free_slots(config, 'haircut', WED, taken, START, staff_id='giorgi'))


def test__free_slots__both_busy__slot_not_offered(config):
    taken = [booking(local(WED, 15), staff='giorgi', id='B1'), booking(local(WED, 15), staff='nika', id='B2')]
    assert '15:00' not in labels(free_slots(config, 'haircut', WED, taken, START))


def test__free_slots__assignment__fewest_bookings_that_day_then_config_order(config):
    # У Джорджи уже есть запись в среду — свободный слот после обеда достаётся Нике.
    taken = [booking(local(WED, 10), staff='giorgi')]
    slots = free_slots(config, 'haircut', WED, taken, START)
    assert next(s for s in slots if labels([s]) == ['16:00']).staff_id == 'nika'
    # Без записей — первый по конфигу.
    assert (
        next(s for s in free_slots(config, 'haircut', WED, [], START) if labels([s]) == ['16:00']).staff_id == 'giorgi'
    )


def test__free_slots__cancelled_and_ignored_bookings__do_not_block(config):
    cancelled = booking(local(WED, 12), status=BookingStatus.CANCELLED)
    assert '12:00' in labels(free_slots(config, 'haircut', WED, [cancelled], START, staff_id='giorgi'))
    own = booking(local(WED, 12), id='MINE')
    assert '12:00' in labels(free_slots(config, 'haircut', WED, [own], START, staff_id='giorgi', ignore_booking='MINE'))


def test__free_slots__buffer__gap_kept_on_both_sides():
    config = make_config(buffer_min=15)
    taken = [booking(local(WED, 12), staff='giorgi')]  # 12:00–12:45
    shown = labels(free_slots(config, 'haircut', WED, taken, START, staff_id='giorgi'))
    assert '11:00' in shown and '11:15' not in shown  # 11:15–12:00 + 15 мин паузы задевает 12:00
    assert '12:45' not in shown and '13:00' in shown


def test__free_slots__step_alignment__starts_on_grid_from_midnight():
    config = make_config(hours={'wed': ['10:10-12:00']}, staff=[{'id': 'giorgi', 'name': 'G'}],
                         services=[{'id': 'cut', 'name': {'en': 'Cut'}, 'duration_min': 20, 'price': '1'}],
                         slot_step_min=30)  # fmt: skip
    assert labels(free_slots(config, 'cut', WED, [], START)) == ['10:30', '11:00', '11:30']


def test__dst__spring_forward__nonexistent_local_times_skipped():
    config = berlin_config(sun=['01:00-04:00'])
    now = dt.datetime(2026, 3, 27, 12, tzinfo=dt.UTC)
    slots = free_slots(config, 'cut', SPRING, [], now)
    assert labels(slots, BERLIN) == ['01:00', '01:30', '03:00', '03:30']
    assert [s.start.hour for s in slots] == [0, 0, 1, 1]  # UTC: 00:00, 00:30, 01:00, 01:30
    assert all(s.end - s.start == dt.timedelta(minutes=30) for s in slots)


def test__dst__fall_back__ambiguous_hour_offered_once_in_order():
    config = berlin_config(sun=['01:00-04:00'])
    now = dt.datetime(2026, 10, 20, 12, tzinfo=dt.UTC)
    slots = free_slots(config, 'cut', AUTUMN, [], now)
    shown = labels(slots, BERLIN)
    assert shown == ['01:00', '01:30', '02:00', '02:30', '03:00', '03:30']
    starts = [s.start for s in slots]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    # 02:30 — первое (летнее) вхождение: 00:30 UTC; второе (01:30 UTC) не предлагается.
    assert slots[3].start == dt.datetime(2026, 10, 25, 0, 30, tzinfo=dt.UTC)
    assert slots[4].start == dt.datetime(2026, 10, 25, 2, 0, tzinfo=dt.UTC)


def test__dst__interval_end_ambiguous_or_missing__earliest_reading_used():
    zone = ZoneInfo(BERLIN)
    assert boundary_to_utc(AUTUMN, 150, zone) == dt.datetime(2026, 10, 25, 0, 30, tzinfo=dt.UTC)
    assert boundary_to_utc(SPRING, 150, zone) == dt.datetime(2026, 3, 29, 0, 30, tzinfo=dt.UTC)
    config = berlin_config(sun=['01:00-02:30'])
    assert labels(free_slots(config, 'cut', AUTUMN, [], dt.datetime(2026, 10, 20, tzinfo=dt.UTC)), BERLIN) == [
        '01:00',
        '01:30',
        '02:00',
    ]
    assert labels(free_slots(config, 'cut', SPRING, [], dt.datetime(2026, 3, 20, tzinfo=dt.UTC)), BERLIN) == ['01:00']


def test__dst__working_hours_map_to_different_utc_before_and_after_switch():
    config = berlin_config(fri=['09:00-18:00'], mon=['09:00-18:00'])
    now = dt.datetime(2026, 3, 20, tzinfo=dt.UTC)
    before = free_slots(config, 'cut', dt.date(2026, 3, 27), [], now)[0]
    after = free_slots(config, 'cut', dt.date(2026, 3, 30), [], now)[0]
    assert before.start.hour == 8 and after.start.hour == 7  # 09:00 CET = 08:00Z, 09:00 CEST = 07:00Z


def test__wall_to_utc__gap_none_and_midnight_next_day():
    zone = ZoneInfo(BERLIN)
    assert wall_to_utc(SPRING, 150, zone) is None
    assert boundary_to_utc(TUE, 1440, ZoneInfo('Asia/Tbilisi')) == dt.datetime(2026, 10, 20, 20, tzinfo=dt.UTC)


def test__find_slot__taken_or_off_grid__none(config):
    noon = local(WED, 12)
    assert find_slot(config, 'haircut', 'giorgi', noon, [], START).staff_id == 'giorgi'
    assert find_slot(config, 'haircut', 'giorgi', noon, [booking(noon)], START) is None
    assert find_slot(config, 'haircut', None, noon, [booking(noon)], START).staff_id == 'nika'
    assert find_slot(config, 'haircut', 'giorgi', noon + dt.timedelta(minutes=7), [], START) is None
    assert find_slot(config, 'haircut', 'giorgi', local(TUE, 13), [], START) is None  # внутри запаса времени


def test__bookable_days__limit_start_day_and_full_day(config):
    days = bookable_days(config, 'haircut', [], START, limit=3)
    assert days == [TUE, WED, dt.date(2026, 10, 22)]
    assert bookable_days(config, 'haircut', [], START, start_day=WED, limit=1) == [WED]
    nika_days = bookable_days(config, 'haircut', [], START, staff_id='nika')
    assert all(day.weekday() in (2, 3, 4, 5) for day in nika_days)


def test__next_free_slots__window_and_limit(config):
    after_five = next_free_slots(config, 'haircut', [], START, limit=3, window=TimeWindow(17 * 60, None))
    assert labels(after_five) == ['17:00', '17:15', '17:30']
    assert after_five[0].start.date() == TUE
    morning = next_free_slots(config, 'haircut', [], START, limit=2, window=TimeWindow(None, 11 * 60))
    assert labels(morning) == ['10:00', '10:15'] and morning[0].start.astimezone(ZoneInfo('Asia/Tbilisi')).date() == WED
    assert next_free_slots(config, 'haircut', [], START, limit=50, start_day=WED)[0].start == local(WED, 10)


def test__nearest_slots__same_day_by_distance_then_next_days(config):
    taken = [booking(local(WED, 12), staff='giorgi')]
    near = nearest_slots(config, 'haircut', local(WED, 12), taken, START, limit=4, staff_id='giorgi')
    assert labels(near) == ['11:00', '11:15', '12:45', '13:00']
    late = nearest_slots(config, 'haircut', local(WED, 19, 15), [], START, limit=3, staff_id='giorgi')
    assert len(late) == 3 and late == sorted(late, key=lambda s: s.start)


def test__time_window__contains_and_exact():
    assert TimeWindow(600, 600).exact and TimeWindow(600, 600).contains(600)
    assert not TimeWindow(600, 600).contains(615)
    assert TimeWindow(None, 720).contains(0) and not TimeWindow(1020, None).contains(1000)
    assert not TimeWindow(None, None).exact


def test__is_open_at__hours_lunch_and_closed_dates(config):
    assert is_open_at(config, local(TUE, 12, 30))
    assert not is_open_at(config, local(TUE, 14, 30))
    assert not is_open_at(config, local(TUE, 22, 40))
    assert not is_open_at(config, local(dt.date(2026, 10, 25), 12))
    assert not is_open_at(make_config(closed_dates=[TUE]), local(TUE, 12))


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('Anna', 'Anna'),
        ('  anna   maria  ', 'anna maria'),
        ('Анна-Мария', 'Анна-Мария'),
        ("O'Brien", "O'Brien"),
        ('A', None),
        ('Anna2', None),
        ('/start', None),
        ('http://x.y', None),
        ('-Anna', None),
        ('a' * 61, None),
        ('A.', None),
    ],
)
def test__clean_name__cases(raw, expected):
    assert clean_name(raw) == expected


@pytest.mark.parametrize(
    ('raw', 'international', 'expected'),
    [
        ('+995 555 12-34-56', False, '+995555123456'),
        ('(555) 123 456', False, '555123456'),
        ('995555123456', True, '+995555123456'),
        ('12345', False, None),
        ('+1234567890123456', False, None),
        ('555 12+34', False, None),
        ('call me', False, None),
        ('', False, None),
    ],
)
def test__clean_phone__cases(raw, international, expected):
    assert clean_phone(raw, international=international) == expected


def test__customer_complete__needs_name_and_phone():
    assert Customer(key='k', name='A', phone='1').complete
    assert not Customer(key='k', name='A').complete


def test__helpers__minutes_until_and_day_bounds():
    assert minutes_until(START + dt.timedelta(hours=1), START) == 60
    start, end = day_bounds(TUE, ZoneInfo('Asia/Tbilisi'))
    assert start == dt.datetime(2026, 10, 19, 20, tzinfo=dt.UTC) and end - start == dt.timedelta(days=1)

import datetime as dt

import pytest

from autostudio.booking import callbacks as cb
from autostudio.booking.callbacks import SlotRef
from autostudio.booking.config import SERVICE_ID_MAX, STAFF_ID_MAX
from autostudio.booking.messages import CALLBACK_DATA_LIMIT, Button

LONGEST_SERVICE = 's' * SERVICE_ID_MAX
LONGEST_STAFF = 'p' * STAFF_ID_MAX
MOMENT = dt.datetime(2026, 12, 31, 23, 45, tzinfo=dt.UTC)


def test__button__data_over_64_bytes__value_error():
    Button('ok', 'x' * CALLBACK_DATA_LIMIT)
    with pytest.raises(ValueError, match='65 байт'):
        Button('too long', 'x' * 65)
    with pytest.raises(ValueError, match='байт'):
        Button('cyrillic counts bytes', 'я' * 33)  # 66 байт в UTF-8
    with pytest.raises(ValueError):
        Button('empty', '')


@pytest.mark.parametrize('action', [cb.TIME, cb.CONFIRM, cb.EDIT])
@pytest.mark.parametrize('source', ['buttons', 'ai'])
def test__slot_data__longest_ids__fits_telegram_limit(action, source):
    ref = SlotRef(source=source, service_id=LONGEST_SERVICE, staff_id=LONGEST_STAFF, start=MOMENT)
    data = cb.slot_data(action, ref)
    assert len(data.encode()) <= CALLBACK_DATA_LIMIT
    assert cb.parse_slot(cb.split(data)[1]) == ref


def test__other_data__longest_ids__fits_telegram_limit():
    day = MOMENT.date()
    samples = [
        cb.day_data(LONGEST_SERVICE, LONGEST_STAFF, day, page=99),
        cb.join(cb.DAYS_FROM, LONGEST_SERVICE, LONGEST_STAFF, cb.encode_day(day)),
        cb.join(cb.STAFF, LONGEST_SERVICE, LONGEST_STAFF),
        cb.join(cb.MOVE_YES, 'ABCDEF', cb.encode_ts(MOMENT)),
        cb.join(cb.ADMIN_CANCEL, 'ABCDEF'),
    ]
    assert all(len(sample.encode()) <= CALLBACK_DATA_LIMIT for sample in samples)
    assert cb.day_data('cut', None, day) == 'd:cut:_:20261231'


def test__parse_slot__any_staff__roundtrip():
    ref = SlotRef(source='ai', service_id='cut', staff_id=None, start=MOMENT)
    assert cb.parse_slot(cb.split(cb.slot_data(cb.TIME, ref))[1]) == ref


@pytest.mark.parametrize(
    'args',
    [
        [],
        ['b', 'cut', '_'],
        ['z', 'cut', '_', '202612312345'],
        ['b', '', '_', '202612312345'],
        ['b', 'cut', '', '202612312345'],
        ['b', 'cut', '_', '2026123123'],
        ['b', 'cut', '_', '202613312345'],
        ['b', 'cut', '_', 'abcdefghijkl'],
    ],
)
def test__parse_slot__garbage__none(args):
    assert cb.parse_slot(args) is None


def test__decode__garbage__none():
    assert cb.decode_day('20261332') is None
    assert cb.decode_day('2026-12-31') is None
    assert cb.decode_ts('202612312360') is None
    assert cb.decode_day(cb.encode_day(MOMENT.date())) == MOMENT.date()
    assert cb.decode_ts(cb.encode_ts(MOMENT)) == MOMENT


def test__split_join__action_and_args__roundtrip():
    assert cb.split(cb.join('rt', 'ABC', '1')) == ('rt', ['ABC', '1'])
    assert cb.split('m') == ('m', [])
    assert cb.staff_code(None) == '_' and cb.staff_from_code('_') is None and cb.staff_from_code('x') == 'x'

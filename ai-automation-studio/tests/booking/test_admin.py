import datetime as dt

from autostudio.booking.domain import Customer
from autostudio.booking.messages import TEXT_LIMIT
from tests.booking.helpers import START, TUE, App, make_config

ADMIN = 'tg:999'


def fill_week(app: App) -> int:
    """Занимает каждое свободное время обоих мастеров на неделю вперёд."""
    count = 0
    for offset in range(7):
        day = TUE + dt.timedelta(days=offset)
        for staff in ('giorgi', 'nika'):
            while slots := app.service.free_slots('haircut', day, START, staff_id=staff):
                app.book_directly(f'tg:{1000 + count}', 'haircut', staff, slots[0].start, name='Customer Name')
                count += 1
    return count


async def test__week__fully_booked_two_barbers__split_by_days_nothing_clipped(tmp_path):
    app = App(tmp_path, make_config(min_lead_min=0))
    booked = fill_week(app)
    replies = await app.text(ADMIN, '/week', is_admin=True)
    assert len(replies) > 1 and all(len(reply.text) <= TEXT_LIMIT for reply in replies)
    text = '\n\n'.join(reply.text for reply in replies)
    assert '…' not in text
    headers = [line for line in text.splitlines() if line.startswith('📋')]
    assert len(headers) == 7
    assert text.count('Customer Name, +995 555 00 01 11') == booked
    # Каждое сообщение начинается с заголовка дня: день не разорван между сообщениями.
    assert all(reply.text.startswith('📋') for reply in replies)


async def test__today__one_huge_day__split_by_lines(tmp_path):
    staff = [
        {'id': 'giorgi', 'name': 'Giorgi'},
        *({'id': f's{index}', 'name': f'Master {index}'} for index in range(11)),
    ]
    app = App(tmp_path, make_config(staff=staff, min_lead_min=0))
    day = TUE + dt.timedelta(days=1)
    count = 0
    for member in staff:
        while slots := app.service.free_slots('haircut', day, START, staff_id=member['id']):
            app.book_directly(f'tg:{count}', 'haircut', member['id'], slots[0].start, name='Customer Name')
            count += 1
    replies = await app.text(ADMIN, '/tomorrow', is_admin=True)
    assert len(replies) > 1 and all(len(reply.text) <= TEXT_LIMIT for reply in replies)
    assert sum(reply.text.count('Customer Name') for reply in replies) == count


async def test__reply__bare_number_of_known_customer__resolved(app):
    app.store.save_customer(Customer(key='tg:555', language='ru'), START)
    [relay] = await app.text(ADMIN, '/reply 555 Да, ждём вас', is_admin=True)
    assert relay.to == 'tg:555' and relay.text.endswith('Да, ждём вас')
    assert relay.receipt is not None and relay.receipt.delivered == 'Delivered to tg:555.'


async def test__reply__unknown_bare_number__usage_hint_nothing_sent(app):
    [reply] = await app.text(ADMIN, '/reply 12345 hello', is_admin=True)
    assert reply.to is None and reply.text.startswith('No customer 12345')


async def test__reply__unknown_full_key__sent_with_receipt(app):
    [relay] = await app.text(ADMIN, '/reply tg:777 hello', is_admin=True)
    assert relay.to == 'tg:777' and relay.receipt is not None
    assert relay.receipt.failed.startswith('Not delivered to tg:777')

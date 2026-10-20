from autostudio.booking.messages import ADMINS, Button, DeliveryError, Outgoing, Receipt, deliver, split_text
from autostudio.errors import NotifyError
from tests.booking.helpers import Recorder


class Flaky:
    def __init__(self, bad: str) -> None:
        self.bad = bad
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat: str, message: Outgoing) -> None:
        if chat == self.bad:
            raise DeliveryError('down')
        self.sent.append((chat, message.text))


async def test__deliver__mixed_recipients__routed():
    sender, notifier = Recorder(), Recorder()
    failures = await deliver(
        sender,
        'tg:1',
        [Outgoing(text='hi'), Outgoing(text='new booking', to=ADMINS), Outgoing(text='relay', to='tg:5')],
        admins=['tg:900', 'tg:901'],
        notifier=notifier,
    )
    assert failures == []
    assert [(chat, message.text) for chat, message in sender.sent] == [
        ('tg:1', 'hi'),
        ('tg:900', 'new booking'),
        ('tg:901', 'new booking'),
        ('tg:5', 'relay'),
    ]
    assert notifier.texts == ['new booking']


async def test__deliver__one_recipient_down__others_still_served():
    sender = Flaky(bad='tg:900')
    failures = await deliver(
        sender,
        'tg:1',
        [Outgoing(text='alert', to=ADMINS), Outgoing(text='hi')],
        admins=['tg:900', 'tg:901'],
        notifier=Recorder(fail=NotifyError('webhook 500')),
    )
    assert failures == ['@notify', 'tg:900']
    assert sender.sent == [('tg:901', 'alert'), ('tg:1', 'hi')]


async def test__deliver__no_admins_no_notifier__admin_message_dropped_quietly():
    sender = Recorder()
    assert await deliver(sender, 'tg:1', [Outgoing(text='x', to=ADMINS)], admins=[]) == []
    assert sender.sent == []


async def test__deliver__receipt__delivered_or_failed_note_to_sender():
    sender = Flaky(bad='tg:4242')
    relay = Outgoing(text='relay', to='tg:5', receipt=Receipt(delivered='Delivered', failed='Not delivered'))
    lost = Outgoing(text='relay', to='tg:4242', receipt=Receipt(delivered='Delivered', failed='Not delivered'))
    failures = await deliver(sender, 'tg:999', [relay, lost], admins=[])
    assert failures == ['tg:4242']
    assert sender.sent == [('tg:5', 'relay'), ('tg:999', 'Delivered'), ('tg:999', 'Not delivered')]


def test__split_text__blocks_and_long_lines__under_limit_in_order():
    blocks = ['a' * 30, 'b' * 30, '\n'.join(['c' * 20] * 5), 'd' * 200]
    parts = split_text(blocks, limit=70)
    assert all(len(part) <= 70 for part in parts)
    assert parts[0] == f'{"a" * 30}\n\n{"b" * 30}'
    assert parts[1].startswith('c' * 20) and parts[-1].endswith('…')
    assert split_text([]) == []


async def test__routing_sender__by_key_prefix__unknown_channel_gone():
    import pytest

    from autostudio.booking.messages import RecipientGoneError, RoutingSender
    from tests.booking.helpers import Recorder

    telegram, whatsapp = Recorder(), Recorder()
    routing = RoutingSender({'tg': telegram, 'wa': whatsapp})
    await routing.send('tg:1', Outgoing(text='a'))
    await routing.send('wa:3f9c', Outgoing(text='b'))
    assert [chat for chat, _ in telegram.sent] == ['tg:1'] and [chat for chat, _ in whatsapp.sent] == ['wa:3f9c']
    for chat in ('console:operator', 'no-prefix'):
        with pytest.raises(RecipientGoneError):
            await routing.send(chat, Outgoing(text='c'))


def test__coalesce__notice_then_menu__one_message_when_it_fits():
    from autostudio.booking.messages import ADMINS, Receipt, Reminder, coalesce

    menu = Outgoing(text='Menu', buttons=[[Button('Book', 'b')]])
    assert coalesce([Outgoing(text='Cancelled.'), menu], 1024) == [
        Outgoing(text='Cancelled.\n\nMenu', buttons=[[Button('Book', 'b')]])
    ]
    # Разным адресатам, после кнопок, с квитанцией или напоминанием — не склеиваются.
    to_admins = Outgoing(text='New booking', to=ADMINS)
    receipt = Outgoing(text='x', to='tg:2', receipt=Receipt(delivered='ok', failed='no'))
    reminder = Outgoing(text='⏰', reminder=Reminder(service='Haircut', when='now', language='en'))
    kept = [menu, Outgoing(text='after buttons'), to_admins, receipt, reminder, Outgoing(text='last')]
    assert coalesce(kept, 1024) == kept
    assert coalesce([Outgoing(text='x' * 600), Outgoing(text='y' * 600)], 1024) == [
        Outgoing(text='x' * 600),
        Outgoing(text='y' * 600),
    ]


def test__channel_of__prefixes():
    from autostudio.booking.messages import channel_of

    assert [channel_of(key) for key in ('tg:1', 'wa:x', 'console:operator', 'sms:1', 'plain')] == [
        'telegram',
        'whatsapp',
        'console',
        'other',
        'other',
    ]

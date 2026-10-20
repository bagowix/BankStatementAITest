from autostudio.booking.messages import ADMINS, DeliveryError, Outgoing, Receipt, deliver, split_text
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

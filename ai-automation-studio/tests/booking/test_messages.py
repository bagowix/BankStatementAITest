from autostudio.booking.messages import ADMINS, DeliveryError, Outgoing, deliver
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
    assert failures == 0
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
    assert failures == 2
    assert sender.sent == [('tg:901', 'alert'), ('tg:1', 'hi')]


async def test__deliver__no_admins_no_notifier__admin_message_dropped_quietly():
    sender = Recorder()
    assert await deliver(sender, 'tg:1', [Outgoing(text='x', to=ADMINS)], admins=[]) == 0
    assert sender.sent == []

import datetime as dt

from autostudio.booking.alarm import OperatorAlarm
from autostudio.clock import FrozenClock
from autostudio.errors import NotifyError
from tests.booking.helpers import START, Recorder


async def test__alarm__transitions__first_failure_then_silence_then_recovery():
    operator = Recorder()
    alarm = OperatorAlarm(operator, 'Demo / booking', clock=FrozenClock(START))
    assert await alarm.raise_('calendar', 'Google down')
    assert not await alarm.raise_('calendar', 'Google down again')
    assert alarm.active('calendar')
    assert await alarm.clear('calendar', 'back')
    assert not await alarm.clear('calendar', 'back')
    assert operator.texts == ['⚠️ Demo / booking: Google down', '✅ Demo / booking: back']


async def test__alarm__repeat__not_more_often_than_hourly():
    clock = FrozenClock(START)
    operator = Recorder()
    alarm = OperatorAlarm(operator, 'Demo / booking', clock=clock)
    await alarm.raise_('ValueError', 'bug', repeat=True)
    await alarm.raise_('ValueError', 'bug', repeat=True)
    clock.advance(dt.timedelta(hours=1))
    await alarm.raise_('ValueError', 'bug', repeat=True)
    assert len(operator.texts) == 2


async def test__alarm__no_operator_or_notify_failure__logged_not_raised(caplog):
    assert await OperatorAlarm(None, 'Demo / booking').raise_('x', 'text')
    assert 'канал не настроен' in caplog.text
    failing = OperatorAlarm(Recorder(fail=NotifyError('down')), 'Demo / booking')
    assert await failing.raise_('x', 'text')
    assert 'оператор не уведомлён' in caplog.text

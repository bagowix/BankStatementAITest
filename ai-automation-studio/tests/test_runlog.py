import httpx
import respx

from autostudio.errors import LLMError, NotifyError
from autostudio.events import EventLog
from autostudio.runlog import Heartbeat, supervised_run


class Recorder:
    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, text):
        if self.fail:
            raise NotifyError('down')
        self.sent.append(text)


@respx.mock
async def test__supervised_run__success__ok_event_and_pings(tmp_path, clock):
    start = respx.post('https://hc.example.test/abc/start').mock(return_value=httpx.Response(200))
    ok = respx.post('https://hc.example.test/abc').mock(return_value=httpx.Response(200))
    log = EventLog(tmp_path / 'e.sqlite')

    async def job():
        return 7

    async with httpx.AsyncClient() as http:
        result = await supervised_run(package='prices', job=job, events=log, heartbeat=Heartbeat(http, 'https://hc.example.test/abc/'), operator=None, client_name='Acme', now=clock)
    assert result == 7
    assert start.called and ok.called
    assert [e.kind for e in log.between(clock().replace(year=2000), clock().replace(year=2100))] == ['system.run_ok']


@respx.mock
async def test__supervised_run__package_error__failed_event_fail_ping_operator_alert(tmp_path, clock):
    respx.post('https://hc.example.test/abc/start').mock(return_value=httpx.Response(200))
    fail = respx.post('https://hc.example.test/abc/fail').mock(return_value=httpx.Response(200))
    log = EventLog(tmp_path / 'e.sqlite')
    operator = Recorder()

    async def job():
        raise LLMError('quota exceeded')

    async with httpx.AsyncClient() as http:
        result = await supervised_run(package='inbox', job=job, events=log, heartbeat=Heartbeat(http, 'https://hc.example.test/abc'), operator=operator, client_name='Acme', now=clock)
    assert result is None
    assert fail.calls[0].request.content == b'quota exceeded'
    assert operator.sent == ['⚠️ Acme / inbox: quota exceeded']
    events = log.between(clock().replace(year=2000), clock().replace(year=2100))
    assert events[0].kind == 'system.run_failed'
    assert events[0].payload['error'] == 'quota exceeded'


async def test__supervised_run__operator_down__still_returns_none(tmp_path, clock):
    async def job():
        raise LLMError('x')

    async with httpx.AsyncClient() as http:
        result = await supervised_run(package='inbox', job=job, events=EventLog(tmp_path / 'e.sqlite'), heartbeat=Heartbeat(http, None), operator=Recorder(fail=True), client_name='A', now=clock)
    assert result is None


@respx.mock
async def test__heartbeat__network_error__swallowed():
    respx.post('https://hc.example.test/x').mock(side_effect=httpx.ConnectError('down'))
    async with httpx.AsyncClient() as http:
        await Heartbeat(http, 'https://hc.example.test/x').ping()

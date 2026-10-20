from autostudio.booking.console import read_line, render, repl
from autostudio.booking.messages import ADMINS, Button, Outgoing
from tests.booking.helpers import WED, App, local


def scripted(lines):
    queue = list(lines)

    async def read(prompt: str) -> str | None:
        return queue.pop(0) if queue else None

    return read


def test__render__buttons_numbered_and_routing_labels():
    text, buttons = render(Outgoing(text='Pick', buttons=[[Button('A', 'a'), Button('B', 'b')]]), 'console:op', 3)
    assert text == '🤖 Pick\n   [3] A   [4] B' and [b.data for b in buttons] == ['a', 'b']
    assert render(Outgoing(text='New booking', to=ADMINS), 'console:op', 1)[0].startswith('[→ администраторам]')
    assert render(Outgoing(text='Hi', to='tg:5'), 'console:op', 1)[0] == '[→ tg:5] Hi'
    contact, _ = render(Outgoing(text='Phone?', request_contact='📱 Share', keyboard=['Cancel']), 'console:op', 1)
    assert '/share <телефон>' in contact and 'Cancel' in contact


async def test__repl__book_through_numbers_and_share(app: App):
    out: list[str] = []
    lines = ['1', '1', '1', '2', '9', 'Anna', '/share 995555123456', '1', '/quit']
    handled = await repl(app.engine, read=scripted(lines), write=out.append, language='en')
    transcript = '\n'.join(out)
    assert handled == 9
    assert "You're booked!" in transcript
    bookings = app.store.for_customer('console:operator', app.clock())
    assert len(bookings) == 1 and bookings[0].start == local(WED, 12)


async def test__repl__admin_mode_and_eof(app: App):
    out: list[str] = []
    handled = await repl(app.engine, admin=True, read=scripted(['/week', '', '99']), write=out.append)
    assert handled == 3 and any('📋' in line for line in out)


async def test__read_line__eof__none(monkeypatch):
    def eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr('builtins.input', eof)
    assert await read_line('> ') is None
    monkeypatch.setattr('builtins.input', lambda prompt: 'hi')
    assert await read_line('> ') == 'hi'

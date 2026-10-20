import datetime as dt
import imaplib

import pytest

from autostudio.inbox.errors import MailboxError
from autostudio.inbox.imap import (
    MAX_FETCH_BYTES,
    FakeMailBox,
    ImapMailBox,
    decode_folder,
    encode_folder,
    imap_date,
    parse_list,
    quote_folder,
)
from tests.inbox.fake_imap import FakeImapServer
from tests.inbox.helpers import NOW, make_email


def _box(server: FakeImapServer, password: str = 'app-pass-1234') -> ImapMailBox:
    return ImapMailBox(
        host='imap.example.com',
        port=993,
        user=server.user,
        password=password,
        timeout=5,
        connector=lambda host, port, timeout: server,
    )


@pytest.mark.parametrize(
    ('name', 'encoded'),
    [
        ('INBOX', 'INBOX'),
        ('Черновики', '&BCcENQRABD0EPgQyBDgEOgQ4-'),
        ('Tom & Jerry', 'Tom &- Jerry'),
        ('[Gmail]/Отправленные', '[Gmail]/&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-'),
    ],
)
def test__folder_names__modified_utf7__roundtrip(name, encoded):
    assert encode_folder(name) == encoded
    assert decode_folder(encoded) == name


def test__decode_folder__broken_sequence__left_as_is():
    assert decode_folder('&###-') == '&###-'


def test__quote_folder__quotes_and_backslashes_escaped():
    assert quote_folder('[Gmail]/Drafts') == '"[Gmail]/Drafts"'
    assert quote_folder('a"b\\c') == '"a\\"b\\\\c"'


def test__imap_date__english_month_regardless_of_locale():
    assert imap_date(dt.date(2026, 10, 7)) == '07-Oct-2026'


def test__parse_list__quoted_atom_literal_and_special_use():
    folders = parse_list(
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"',
            b'(\\HasNoChildren) "." Archive',
            (b'(\\HasNoChildren \\Drafts) "/" {18}', b'&BCcENQRABD0EPgQyBDgEOgQ4-'),
            b'(\\Noselect) "/" "Quote \\" inside"',
            None,
            b'garbage',
        ]
    )
    assert [folder.name for folder in folders] == ['INBOX', '[Gmail]/Drafts', 'Archive', 'Черновики', 'Quote " inside']
    assert '\\drafts' in folders[1].flags


async def test__imap__fetch__body_peek_only_no_seen_flag():
    server = FakeImapServer(messages={5: make_email()})
    async with _box(server) as box:
        state = await box.select('INBOX')
        raw = await box.fetch(5)
    assert raw == make_email()
    assert state.uidvalidity == 7 and state.uidnext == 6 and state.exists == 1
    fetch = [args for command, args in server.commands if command == 'FETCH']
    assert fetch == [('5', f'(UID BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)')]
    assert server.flags[5] == set()
    assert server.selected == ('"INBOX"', True)
    assert server.logged_out


async def test__imap__fetch__uid_after_literal_and_vanished_message():
    server = FakeImapServer(messages={5: b'x'})
    server.fetch_reply = [(b'1 (BODY[]<0> {3}', b'abc'), b' UID 5)']
    async with _box(server) as box:
        await box.select('INBOX')
        assert await box.fetch(5) == b'abc'
        server.fetch_reply = [None]
        assert await box.fetch(9) is None
        server.fetch_reply = [(b'1 (UID 6 BODY[]<0> {3}', b'abc'), b')']
        assert await box.fetch(5) is None


async def test__imap__search__after_uid_filters_star_quirk_and_since_format():
    server = FakeImapServer(messages={3: b'a', 4: b'b'})
    async with _box(server) as box:
        await box.select('INBOX')
        assert await box.search(after_uid=4) == []
        assert await box.search(after_uid=3) == [4]
        assert await box.search(since=dt.date(2026, 10, 17)) == [3, 4]
        assert await box.search() == [3, 4]
    searches = [args for command, args in server.commands if command == 'SEARCH']
    assert searches == [('UID', '5:*'), ('UID', '4:*'), ('SINCE', '17-Oct-2026'), ('ALL',)]


async def test__imap__append_draft__draft_flag_quoted_utf7_folder():
    server = FakeImapServer()
    async with _box(server) as box:
        await box.append('Черновики', b'From: a\r\n\r\nhi', NOW)
    mailbox, flags, date_time, message = server.appended[0]
    assert mailbox == '"&BCcENQRABD0EPgQyBDgEOgQ4-"'
    assert flags == '(\\Draft \\Seen)'
    assert date_time.startswith('"20-Oct-2026')
    assert message.startswith(b'From: a')


async def test__imap__copy__ok_and_trycreate_error():
    server = FakeImapServer(messages={1: b'x'})
    async with _box(server) as box:
        await box.select('INBOX')
        await box.copy(1, 'Invoices')
        with pytest.raises(MailboxError, match='TRYCREATE'):
            await box.copy(1, 'Missing')
    assert server.copied == [('1', '"Invoices"')]
    assert not any(command in ('STORE', 'EXPUNGE') for command, _ in server.commands)


async def test__imap__folders__listed():
    server = FakeImapServer()
    async with _box(server) as box:
        names = [folder.name for folder in await box.folders()]
    assert names == ['INBOX', 'Drafts', 'Invoices', 'Newsletters']


async def test__imap__wrong_password__clear_error_without_secrets_and_logout():
    server = FakeImapServer()
    with pytest.raises(MailboxError) as caught:
        async with _box(server, password='wrong-secret-pass'):
            pass
    message = str(caught.value)
    assert 'пароль приложения' in message
    assert 'wrong-secret-pass' not in message and server.user not in message
    assert server.logged_out


async def test__imap__server_error_mentions_secret__scrubbed():
    server = FakeImapServer(messages={1: b'x'})
    server.fail['FETCH'] = imaplib.IMAP4.error('FETCH failed for info@acme.example with app-pass-1234')
    with pytest.raises(MailboxError) as caught:
        async with _box(server) as box:
            await box.select('INBOX')
            await box.fetch(1)
    assert 'app-pass-1234' not in str(caught.value) and 'info@acme.example' not in str(caught.value)
    assert '***' in str(caught.value)
    assert server.logged_out


async def test__imap__network_errors__wrapped_type_only():
    server = FakeImapServer()
    server.fail['SELECT'] = TimeoutError('timed out talking to 10.0.0.1')
    with pytest.raises(MailboxError, match='TimeoutError'):
        async with _box(server) as box:
            await box.select('INBOX')

    def refuse(host: str, port: int, timeout: float) -> FakeImapServer:
        raise ConnectionRefusedError('refused')

    box = ImapMailBox(host='h', port=993, user='u', password='p', connector=refuse)
    with pytest.raises(MailboxError, match='подключение — ConnectionRefusedError'):
        async with box:
            pass


async def test__imap__missing_folder_and_missing_uidvalidity__errors():
    server = FakeImapServer()
    async with _box(server) as box:
        with pytest.raises(MailboxError, match='NO'):
            await box.select('Nope')
    server = FakeImapServer()
    server.response = lambda code: (code, [None])  # type: ignore[method-assign]
    async with _box(server) as box:
        with pytest.raises(MailboxError, match='UIDVALIDITY'):
            await box.select('INBOX')


async def test__imap__logout_failure__swallowed():
    server = FakeImapServer()
    server.fail['LOGOUT'] = OSError('socket closed')
    async with _box(server):
        pass
    assert server.logged_out


async def test__imap__used_without_context__error():
    box = _box(FakeImapServer())
    with pytest.raises(MailboxError, match='не открыт'):
        await box.select('INBOX')
    assert box.user == 'info@acme.example'


async def test__fake_mailbox__records_actions_and_renumbers():
    box = FakeMailBox(folders=['Invoices'])
    uid = box.add(b'raw')
    async with box:
        with pytest.raises(MailboxError):
            await box.search()
        state = await box.select('INBOX')
        assert state.uidvalidity == 1 and await box.search(after_uid=0) == [uid]
        await box.copy(uid, 'Invoices')
        await box.append('Drafts', b'd', NOW)
        with pytest.raises(MailboxError):
            await box.copy(uid, 'Missing')
        with pytest.raises(MailboxError):
            await box.append('Missing', b'd', NOW)
        with pytest.raises(MailboxError):
            await box.select('Missing')
        box.fail_fetch.add(uid)
        with pytest.raises(MailboxError):
            await box.fetch(uid)
        assert {folder.name for folder in await box.folders()} == {'INBOX', 'Drafts', 'Invoices'}
    box.renumber('INBOX', 9)
    async with box:
        state = await box.select('INBOX')
        assert state.uidvalidity == 9 and await box.search() == [1000]
    assert box.copies == [(uid, 'Invoices')] and box.appended[0].flags == frozenset({'\\Draft', '\\Seen'})
    assert box.sessions == 2 and not box.open


def test__fake_server__non_peek_fetch__marks_seen():
    # Проверка самой заглушки: без этого тест PEEK ничего бы не доказывал.
    server = FakeImapServer(messages={1: b'x', 2: b'y'})
    server.uid('FETCH', '1', '(BODY[])')
    server.uid('FETCH', '2', '(RFC822)')
    assert server.flags[1] == {'\\Seen'} and server.flags[2] == {'\\Seen'}


async def test__imap__login_refused_for_other_reason__server_reason_without_password_hint():
    server = FakeImapServer()
    server.fail['LOGIN'] = imaplib.IMAP4.error("b'[ALERT] Too many simultaneous connections. (Failure)'")
    with pytest.raises(MailboxError) as caught:
        async with _box(server):
            pass
    message = str(caught.value)
    assert message.endswith('[ALERT] Too many simultaneous connections. (Failure)')
    assert 'пароль приложения' not in message

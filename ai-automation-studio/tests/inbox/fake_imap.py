"""Поддельный IMAP-сервер на уровне imaplib: те же вызовы и та же форма ответов."""

import imaplib
import re

from autostudio.inbox.imap import ImapReply


class FakeImapServer:
    """Имитирует `imaplib.IMAP4_SSL`: письма, флаги, UIDVALIDITY, LIST, APPEND и COPY.

    Не-PEEK-выборка (`BODY[]`, `RFC822`) ставит флаг \\Seen — так тест ловит адаптер,
    который помечал бы письма клиента прочитанными.
    """

    def __init__(
        self,
        *,
        user: str = 'info@acme.example',
        password: str = 'app-pass-1234',
        messages: dict[int, bytes] | None = None,
        uidvalidity: int = 7,
        folders: list[bytes | tuple[bytes, bytes]] | None = None,
    ) -> None:
        self.user = user
        self.password = password
        self.messages = dict(messages or {})
        self.flags: dict[int, set[str]] = {uid: set() for uid in self.messages}
        self.uidvalidity = uidvalidity
        self.folders = (
            folders
            if folders is not None
            else [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
                b'(\\HasNoChildren) "/" "Invoices"',
                b'(\\HasNoChildren) "/" "Newsletters"',
            ]
        )
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.appended: list[tuple[str, str, str, bytes]] = []
        self.copied: list[tuple[str, str]] = []
        self.selected: tuple[str, bool] | None = None
        self.logged_out = False
        self.fail: dict[str, Exception] = {}
        self.fetch_reply: list[bytes | tuple[bytes, bytes] | None] | None = None

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise self.fail[name]

    def login(self, user: str, password: str) -> ImapReply:
        self.commands.append(('LOGIN', (user,)))
        self._maybe_fail('LOGIN')
        if (user, password) != (self.user, self.password):
            raise imaplib.IMAP4.error(f"b'[AUTHENTICATIONFAILED] Invalid credentials for {user} / {password}'")
        return 'OK', [b'LOGIN completed']

    def select(self, mailbox: str = 'INBOX', readonly: bool = False) -> ImapReply:
        self.commands.append(('SELECT', (mailbox, str(readonly))))
        self._maybe_fail('SELECT')
        self.selected = (mailbox, readonly)
        if mailbox not in ('"INBOX"', 'INBOX'):
            return 'NO', [b'[NONEXISTENT] Unknown Mailbox']
        return 'OK', [str(len(self.messages)).encode()]

    def response(self, code: str) -> ImapReply:
        if code == 'UIDVALIDITY':
            return code, [str(self.uidvalidity).encode()]
        if code == 'UIDNEXT':
            return code, [str(max(self.messages, default=0) + 1).encode()]
        return code, [None]

    def uid(self, command: str, *args: str) -> ImapReply:
        self.commands.append((command, args))
        self._maybe_fail(command)
        if command == 'SEARCH':
            uids = sorted(self.messages)
            if args[0] == 'UID':
                start = int(args[1].split(':')[0])
                # Как настоящий сервер: `n:*` включает последнее письмо, даже если UID < n.
                uids = sorted({uid for uid in uids if uid >= start} | ({uids[-1]} if uids else set()))
            return 'OK', [' '.join(str(uid) for uid in uids).encode()]
        if command == 'FETCH':
            uid, query = int(args[0]), args[1]
            if re.search(r'(?<!PEEK)\bBODY\[|RFC822\b(?!\.SIZE)', query.replace('BODY.PEEK[', 'PEEK[')):
                self.flags.setdefault(uid, set()).add('\\Seen')
            if self.fetch_reply is not None:
                return 'OK', self.fetch_reply
            if uid not in self.messages:
                return 'OK', [None]
            raw = self.messages[uid]
            return 'OK', [(f'1 (UID {uid} BODY[]<0> {{{len(raw)}}}'.encode(), raw), b')']
        if command == 'COPY':
            uid_set, folder = args
            if folder not in ('"Invoices"', '"Newsletters"'):
                return 'NO', [b'[TRYCREATE] No such mailbox']
            self.copied.append((uid_set, folder))
            return 'OK', [b'[COPYUID 7 1 1] Done']
        return 'BAD', [b'unknown command']

    def append(self, mailbox: str, flags: str, date_time: str, message: bytes) -> ImapReply:
        self.commands.append(('APPEND', (mailbox, flags)))
        self._maybe_fail('APPEND')
        self.appended.append((mailbox, flags, date_time, message))
        return 'OK', [b'[APPENDUID 7 1] APPEND completed']

    def list(self, directory: str = '""', pattern: str = '*') -> ImapReply:
        self.commands.append(('LIST', (directory, pattern)))
        return 'OK', self.folders

    def logout(self) -> ImapReply:
        self.logged_out = True
        self._maybe_fail('LOGOUT')
        return 'BYE', [b'LOGOUT Requested']

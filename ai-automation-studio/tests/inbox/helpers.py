"""Общие заготовки тестов пакета inbox: письма, конфиг, набор зависимостей."""

import datetime as dt
from pathlib import Path

import msgspec

from autostudio.events import EventLog
from autostudio.inbox.config import InboxConfig
from autostudio.inbox.heuristics import make_fake_llm
from autostudio.inbox.imap import FakeMailBox
from autostudio.inbox.pipeline import InboxDeps
from autostudio.inbox.store import InboxStore
from autostudio.llm import FakeLLM
from autostudio.sinks import CsvSink

NOW = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC)
MAILBOX = 'info@acme-tours.example'


def make_email(
    *,
    subject: str = 'Hello',
    body: str = 'Hi there',
    sender: str = 'Jane Doe <jane@client.example>',
    message_id: str | None = '<m1@client.example>',
    date: str | None = 'Mon, 19 Oct 2026 10:00:00 +0000',
    headers: dict[str, str] | None = None,
    content_type: str = 'text/plain; charset=utf-8',
    raw_body: bytes | None = None,
) -> bytes:
    lines = [f'From: {sender}', f'To: {MAILBOX}', f'Subject: {subject}']
    if message_id is not None:
        lines.append(f'Message-ID: {message_id}')
    if date is not None:
        lines.append(f'Date: {date}')
    for name, value in (headers or {}).items():
        lines.append(f'{name}: {value}')
    lines += ['MIME-Version: 1.0', f'Content-Type: {content_type}']
    head = ('\r\n'.join(lines) + '\r\n\r\n').encode()
    return head + (raw_body if raw_body is not None else body.encode('utf-8'))


def make_config(*, language: str = 'en', **inbox: object) -> InboxConfig:
    data: dict[str, object] = {
        'client': {'id': 'acme-tours', 'name': 'Acme Tours', 'timezone': 'Asia/Tbilisi', 'language': language},
        'inbox': {
            'imap_host': 'imap.example.com',
            'company': {
                'name': 'Acme Tours',
                'signature': 'Acme Tours team',
                'facts': ['Kazbegi day tour costs 120 GEL per person.', 'Office hours: 10:00-19:00 daily.'],
            },
            **inbox,
        },
    }
    return msgspec.convert(data, InboxConfig)


class Recorder:
    """Канал уведомлений, который копит тексты и умеет падать по команде."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, text: str) -> None:
        from autostudio.errors import NotifyError

        if self.fail:
            raise NotifyError('channel down')
        self.sent.append(text)


class Harness:
    """Всё для запуска пайплайна в тесте: ящик, модель, таблица, каналы, состояние."""

    def __init__(
        self,
        tmp_path: Path,
        config: InboxConfig | None = None,
        llm: FakeLLM | None = None,
        operator: bool = True,
    ) -> None:
        self.config = config or make_config(notify_on_urgency='normal')
        folders = [self.config.inbox.drafts_folder]
        folders += [c.move_to for c in self.config.inbox.categories if c.move_to]
        self.box = FakeMailBox(folders=folders)
        self.llm = llm or make_fake_llm()
        self.owner = Recorder()
        self.operator = Recorder() if operator else None
        self.out = tmp_path / 'out'
        self.sink = CsvSink(self.out)
        self.store = InboxStore(tmp_path / 'state' / 'inbox.sqlite')
        self.events = EventLog(tmp_path / 'state' / 'events.sqlite')

    @property
    def deps(self) -> InboxDeps:
        return InboxDeps(
            mailbox=self.box,
            mailbox_user=MAILBOX,
            llm=self.llm,
            sink=self.sink,
            notifier=self.owner,
            operator=self.operator,
            store=self.store,
            events=self.events,
        )

    def sheet(self) -> list[list[str]]:
        import csv

        path = self.sink.path_for(self.config.inbox.sheet_tab)
        if not path.exists():
            return []
        with path.open(encoding='utf-8', newline='') as fh:
            return list(csv.reader(fh))

    def event_kinds(self) -> list[str]:
        start = NOW - dt.timedelta(days=3650)
        return [event.kind for event in self.events.between(start, NOW + dt.timedelta(days=3650))]

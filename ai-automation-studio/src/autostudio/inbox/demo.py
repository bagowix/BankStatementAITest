"""Офлайн-демо разбора почты: 10 настоящих по устройству писем, без сети и ключей.

Ящик — `FakeMailBox` с письмами из `examples/inbox/emails/*.eml`, модель — `FakeLLM`
с эвристиками пакета, таблица — CSV. Запуск идёт дважды: второй ничего не делает
(идемпотентность). Черновики пишутся файлами `.eml` — их можно открыть почтовым клиентом.
"""

import datetime as dt
import re
import shutil
from collections import Counter
from email import message_from_bytes
from email.header import decode_header, make_header
from pathlib import Path
from typing import Final

import msgspec

from autostudio.clientconf import NotifyConfig, SinkConfig, load_client_config
from autostudio.errors import ConfigError
from autostudio.events import EventLog
from autostudio.inbox.config import InboxConfig
from autostudio.inbox.heuristics import make_fake_llm, triage_heuristic
from autostudio.inbox.imap import FakeMailBox
from autostudio.inbox.pipeline import InboxDeps, RunResult, run_once
from autostudio.inbox.store import InboxStore
from autostudio.inbox.triage import Triage
from autostudio.sinks import CsvSink

DEMO_NOW: Final = dt.datetime(2026, 10, 20, 9, 30, tzinfo=dt.UTC)
DEMO_MAILBOX: Final = 'info@demotours.example'
# Телефон из первого письма: демо-модель «вспоминает» его в чужом письме, чтобы
# показать, как код выбрасывает поле, которого в письме нет.
_HALLUCINATED_PHONE: Final = '+1 415 555 0134'
_SLUG: Final = re.compile(r'\W+', re.UNICODE)


class _Recorder:
    """Канал уведомлений демо: копит тексты, а сводка их показывает."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


def examples_dir() -> Path:
    """Каталог `examples/inbox` в репозитории (или в текущем каталоге)."""
    for base in (Path(__file__).resolve().parents[3], Path.cwd()):
        candidate = base / 'examples' / 'inbox'
        if (candidate / 'client.toml').exists():
            return candidate
    raise ConfigError('не найден examples/inbox/client.toml — запускайте демо из корня репозитория')


def _forgetful(system: str, prompt: str) -> Triage:
    # Эвристика плюс «галлюцинация»: в письме с просьбой перезвонить номера нет.
    result = triage_heuristic(system, prompt)
    if 'call me back' in prompt.lower():
        return msgspec.structs.replace(result, phone=_HALLUCINATED_PHONE)
    return result


def _reset(out_dir: Path) -> None:
    # Демо всегда начинается с чистого ящика: удаляем только то, что создаёт само.
    for sub in ('state', 'drafts'):
        shutil.rmtree(out_dir / sub, ignore_errors=True)
    (out_dir / 'Inbox.csv').unlink(missing_ok=True)


def _draft_subject(raw: bytes) -> str:
    value = message_from_bytes(raw).get('Subject', '')
    return str(make_header(decode_header(value)))


def _write_drafts(box: FakeMailBox, out_dir: Path) -> list[Path]:
    target = out_dir / 'drafts'
    target.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, draft in enumerate(box.appended, start=1):
        slug = _SLUG.sub('-', _draft_subject(draft.raw).lower()).strip('-')[:40] or 'draft'
        path = target / f'{index:02d}-{slug}.eml'
        path.write_bytes(draft.raw)
        paths.append(path)
    return paths


def _cut(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 1] + '…'


def _actions(result: RunResult, index: int) -> str:
    outcome = result.outcomes[index]
    parts: list[str] = []
    if outcome.source == 'prefilter':
        parts.append('rules, no AI')
    if outcome.draft:
        parts.append('draft')
    if outcome.notified:
        parts.append('owner pinged')
    if outcome.copied_to:
        parts.append(f'copied → {outcome.copied_to}')
    if outcome.injection:
        parts.append('BLOCKED: injection, operator alerted')
    if outcome.corrected:
        parts.append(f'dropped unverified: {", ".join(outcome.corrected)}')
    return ', '.join(parts) or '—'


def _summary(
    config: InboxConfig,
    first: RunResult,
    second: RunResult,
    owner: _Recorder,
    operator: _Recorder,
    drafts: list[Path],
    out_dir: Path,
) -> str:
    total = len(first.outcomes)
    by_rules = sum(outcome.source == 'prefilter' for outcome in first.outcomes)
    lines = [
        f'AI inbox triage — offline demo for {config.client.name} ({total} emails, stub model, no network)',
        '',
        f'Run 1: {first.processed} processed, {first.failed} failed; '
        f'{by_rules} sorted by rules without AI, {first.llm_calls} model calls (triage + drafts)',
        f'  {"#":>2}  {"From":<34}  {"Subject":<44}  {"Category":<15}  {"Urgency":<7}  Actions',
    ]
    for index, outcome in enumerate(first.outcomes):
        lines.append(
            f'  {index + 1:>2}  {_cut(outcome.sender, 34):<34}  {_cut(outcome.subject, 44):<44}  '
            f'{outcome.category:<15}  {outcome.urgency:<7}  {_actions(first, index)}'
        )
    counts = Counter(first.by_category)
    lines += [
        '',
        'By category: ' + ', '.join(f'{name} {count}' for name, count in counts.most_common()),
        f'Leads found: {first.leads}; reply drafts: {first.drafts} (saved to Drafts, never sent automatically)',
        f'Owner notifications ({len(owner.sent)}):',
        *[f'  🔔 {text.splitlines()[0]}' for text in owner.sent],
        f'Operator alerts ({len(operator.sent)}):',
        *[f'  {text}' for text in operator.sent],
    ]
    dropped = [o for o in first.outcomes if 'phone' in o.corrected]
    for outcome in dropped:
        lines.append(
            f'Model proposed, code verified: phone {_HALLUCINATED_PHONE} for “{_cut(outcome.subject, 40)}” '
            'was dropped — it is not in that email (demo stub was told to “remember” it from another email).'
        )
    lines += [
        '',
        f'Run 2 (same mailbox, 15 min later): {second.processed} processed, {second.llm_calls} model calls, '
        f'{second.drafts} drafts — idempotent, nothing done twice.',
        '',
        f'Sheet:  {out_dir / "Inbox.csv"} ({first.sheet_rows} rows, formula-safe)',
        f'Drafts: {out_dir / "drafts"} ({len(drafts)} .eml files with In-Reply-To/References for threading)',
    ]
    return '\n'.join(lines)


async def run_demo(out_dir: Path) -> str:
    """Полный офлайн-сценарий пакета inbox.

    Args:
        out_dir: Каталог для таблицы (CSV), черновиков и состояния демо.

    Returns:
        Человекочитаемая сводка.
    """
    examples = examples_dir()
    base = load_client_config(examples / 'client.toml', InboxConfig)
    config = msgspec.structs.replace(
        base, sink=SinkConfig(backend='csv', csv_dir=out_dir), notify=NotifyConfig(console=True)
    )
    _reset(out_dir)

    section = config.inbox
    extra = [section.drafts_folder, *(c.move_to for c in section.categories if c.move_to)]
    box = FakeMailBox(folders=extra)
    for path in sorted((examples / 'emails').glob('*.eml')):
        box.add(path.read_bytes(), section.folder)

    llm = make_fake_llm()
    llm.register(Triage, _forgetful)
    owner, operator = _Recorder(), _Recorder()
    deps = InboxDeps(
        mailbox=box,
        mailbox_user=DEMO_MAILBOX,
        llm=llm,
        sink=CsvSink(out_dir),
        notifier=owner,
        operator=operator,
        store=InboxStore(out_dir / 'state' / 'inbox.sqlite'),
        events=EventLog(out_dir / 'state' / 'events.sqlite'),
    )
    first = await run_once(config, deps, DEMO_NOW)
    second = await run_once(config, deps, DEMO_NOW + dt.timedelta(minutes=15))
    drafts = _write_drafts(box, out_dir)
    return _summary(config, first, second, owner, operator, drafts, out_dir)

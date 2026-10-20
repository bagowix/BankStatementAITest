"""Один запуск разбора почты: новые письма → разбор → действия → таблица → журнал.

Порядок для каждого письма: скачать (PEEK) → разобрать → правила → модель →
проверка кодом → черновик / копия в папку / уведомление владельцу / счёт из PDF / сигнал
оператору.
Каждое выполненное действие отмечается сразу, поэтому повтор после сбоя доделывает
только недостающее. Строки таблицы пишутся одной пачкой в конце запуска (лимиты
Google Sheets) и хранятся в SQLite, пока запись не удалась.

Сбой одного письма не останавливает запуск. Кто виноват в сбое, решается по каждой
зависимости отдельно: модель, ящик, папка черновиков, папка для копии, канал владельца.
Если зависимость в этом же запуске работала для других писем, виновато письмо: попытка
засчитывается, после `MAX_ATTEMPTS` письмо закрывается — с разбором и пометкой, какой шаг
не прошёл, или строкой «нужна ручная проверка», если разобрать его не удалось вовсе.
Если зависимость не работала ни разу, письмо не виновато и ждёт, а запуск сообщает об
ошибке (оператору — через `supervised_run`). Недоступность модели (сеть, лимит, перегрузка —
`LLMError.kind == 'outage'`) и поломка настройки (ключ, права, имя модели — `'config'`)
письму не засчитываются никогда. Разобранное письмо ждёт сломанный канал не
дольше `OUTAGE_GRACE`, потом закрывается с пометкой. Отказ модели на конкретном письме
(HTTP 400, отказ, ответ не по схеме) — вина письма, только если в этом же запуске модель
ответила: на другое письмо или на проверочный запрос с безобидным письмом. Иначе это
поломка настройки (неверная модель или effort в `.env`), и письма ждут, как при недоступности.

Письма, которые уже не удавались, следующий запуск берёт после свежих: застрявшее письмо
не загораживает очередь. Если подряд не удаются `BREAKER` писем из-за модели или ящика,
запуск прерывается.
"""

import datetime as dt
import hashlib
import logging
from collections.abc import Callable
from typing import Final, Literal

import msgspec

from autostudio.errors import AutoStudioError, LLMError, NotifyError
from autostudio.events import EventLog
from autostudio.inbox import texts
from autostudio.inbox.config import URGENCY_RANK, Category, InboxConfig
from autostudio.inbox.errors import MailboxError
from autostudio.inbox.imap import FolderState, MailBox
from autostudio.inbox.invoices import InvoiceRecord, extract_invoices
from autostudio.inbox.mail import GENERATED_ID_DOMAIN, ParsedEmail, compose_reply, parse_email
from autostudio.inbox.sheet import build_row, failure_row, invoice_row, sender_cell
from autostudio.inbox.store import Envelope, InboxStore, StoredMessage
from autostudio.inbox.triage import Draft, TriageResult, probe_model, triage_email, write_draft
from autostudio.llm import LLMClient
from autostudio.notify import Notifier
from autostudio.runlog import RunSkipped
from autostudio.sinks import TableSink

logger = logging.getLogger(__name__)

PACKAGE: Final = 'inbox'
MAX_ATTEMPTS: Final = 3
BREAKER: Final = 3
LOCK_MINUTES_MIN: Final = 30
RETENTION: Final = dt.timedelta(days=90)
DRY_RUN_WINDOW: Final = dt.timedelta(days=14)
# Сколько разобранное письмо ждёт сломанный канал (уведомления, папку черновиков), прежде
# чем попасть в таблицу с пометкой «шаг не выполнен»: короткий сбой переживается повтором,
# а заблокированный бот не прячет лида из таблицы навсегда.
OUTAGE_GRACE: Final = dt.timedelta(hours=24)
# В первый запуск (и после смены UIDVALIDITY) письма старше суток только заносятся в
# таблицу: на них, скорее всего, уже ответили, а черновики и сигналы по ним — шум.
FIRST_RUN_QUIET: Final = dt.timedelta(hours=24)
LOW_CONFIDENCE: Final = 0.5
_UNREADABLE_PREFIX: Final = 65_536
_BREAKER_CAUSES: Final = frozenset({'model', 'mailbox'})
# После стольких отказов канала подряд (без единого успеха) остальные письма запуска его
# уже не ждут. Один отказ ничего не значит: бывает, что канал отверг одно сообщение.
FAIL_FAST: Final = 2

type Source = Literal['prefilter', 'model', 'skipped', 'failed', 'gave_up', 'vanished']


class InboxDeps(msgspec.Struct, frozen=True, kw_only=True):
    """Всё, с чем работает запуск: ящик, модель, таблица, уведомления, состояние, журнал.

    Attributes:
        mailbox: Почтовый ящик (IMAP или заглушка).
        mailbox_user: Логин ящика — адрес From черновиков, если `inbox.from_address` не задан.
        llm: Модель.
        sink: Таблица клиента.
        notifier: Канал владельца бизнеса.
        operator: Канал оператора или None.
        store: Состояние разбора.
        events: Журнал событий клиента.
    """

    mailbox: MailBox
    mailbox_user: str
    llm: LLMClient
    sink: TableSink
    notifier: Notifier
    operator: Notifier | None
    store: InboxStore
    events: EventLog


class EmailOutcome(msgspec.Struct, frozen=True, kw_only=True):
    """Что запуск сделал с одним письмом (для сводки в консоли и демо)."""

    uid: int
    subject: str
    sender: str
    source: Source
    category: str = ''
    urgency: str = ''
    draft: bool = False
    draft_check: list[str] = msgspec.field(default_factory=list)
    copied_to: str | None = None
    notified: bool = False
    injection: bool = False
    quiet: bool = False
    corrected: list[str] = msgspec.field(default_factory=list)
    invoices: list[str] = msgspec.field(default_factory=list)
    error: str | None = None


class RunResult(msgspec.Struct, frozen=True, kw_only=True):
    """Итог запуска.

    Attributes:
        invoices_verified: Счетов, которые сошлись (итог в PDF, строки с итогом).
        invoices_unverified: Счетов не сверено: не сошлись или не разбирались (скан, шифр).
        invoice_rows: Строк записано во вкладку счетов.
        postponed: Писем отложено до следующего запуска (модель или канал не работают).
        aborted: Причина остановки по серии сбоев или None.
        errors: Общие ошибки (таблица или канал недоступны) — данные не потеряны, повторятся.
            Текст постоянен для одного вида сбоя (без счётчиков и текста конкретного письма):
            `supervised_run` сравнивает его с прошлым запуском и пишет оператору только о смене.
    """

    folder: str
    uidvalidity: int | None = None
    fetched: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    given_up: int = 0
    drafts: int = 0
    leads: int = 0
    notified: int = 0
    injections: int = 0
    llm_calls: int = 0
    sheet_rows: int = 0
    invoices_verified: int = 0
    invoices_unverified: int = 0
    invoice_rows: int = 0
    postponed: int = 0
    by_category: dict[str, int] = msgspec.field(default_factory=dict)
    outcomes: list[EmailOutcome] = msgspec.field(default_factory=list)
    aborted: str | None = None
    errors: list[str] = msgspec.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.aborted is None and not self.errors


class _Failure(msgspec.Struct, frozen=True):
    """Неудача с письмом в этом запуске.

    Attributes:
        key: Ключ письма в состоянии: Message-ID, хеш байтов нечитаемого письма или None,
            если письмо даже не скачалось.
        cause: Что сломалось: `email` (само письмо), `model`, `model_content`, `mailbox`,
            `drafts`, `notifier`, `copy:<папка>`.
        stage: Шаг, на котором сломалось (`triage`, `draft`, `invoice`, …) — для пометки в таблице.
        signature: Вид сбоя без подробностей конкретного письма — для текста ошибки запуска.
    """

    uid: int
    email: ParsedEmail | None
    key: str | None
    error: str
    cause: str
    stage: str = ''
    signature: str = ''


def model_outage(exc: LLMError) -> bool:
    """Модель не работает вообще (сеть, лимит, перегрузка, ключ, имя модели), а не отказала на этом письме.

    Вид ошибки размечает ядро (`LLMError.kind`) по HTTP-коду провайдера, а не по тексту.
    """
    return exc.kind != 'content'


def failure_signature(exc: Exception) -> str:
    """Вид сбоя для текста ошибки запуска: одинаковый, пока сломано одно и то же.

    Оператору пишут о смене текста ошибки, а не о каждом запуске. Текст ошибки модели
    меняется от письма к письму (ответ не прошёл схему на другом поле, «повторите через 2 с»),
    поэтому для неё в подпись идут только вид (`outage`, `config`, `content`) и HTTP-код;
    подробности остаются в логе. Ошибки каналов (бот заблокирован, папки нет) постоянны и
    полезны как есть.

    Args:
        exc: Исключение по письму.

    Returns:
        Подпись сбоя.
    """
    if isinstance(exc, LLMError):
        status = f', HTTP {exc.status_code}' if exc.status_code is not None else ''
        return f'LLMError ({exc.kind}{status})'
    return f'{type(exc).__name__}: {exc}'[:300]


def unreadable_key(raw: bytes) -> str:
    """Ключ нечитаемого письма по его байтам: переживает смену UIDVALIDITY, в отличие от UID."""
    digest = hashlib.sha256(raw[:_UNREADABLE_PREFIX]).hexdigest()[:40]
    return f'<raw-{digest}@{GENERATED_ID_DOMAIN}>'


def _cause(stage: str, exc: Exception) -> str:
    if isinstance(exc, LLMError):
        return 'model' if model_outage(exc) else 'model_content'
    if isinstance(exc, MailboxError):
        if stage == 'fetch':
            return 'mailbox'
        if stage == 'append':
            return 'drafts'
        if stage.startswith('copy:'):
            return stage
    if isinstance(exc, NotifyError) and stage == 'notify':
        return 'notifier'
    return 'email'


def hot_lead_message(language: str, email: ParsedEmail, triage: TriageResult, category: Category, drafted: bool) -> str:
    """Короткое уведомление владельцу о горячем лиде — без текста письма.

    Args:
        language: Язык владельца.
        email: Письмо.
        triage: Проверенный разбор.
        category: Категория письма.
        drafted: Черновик ответа создан.

    Returns:
        Текст уведомления.
    """
    who = triage.sender_name or email.from_name or email.from_addr
    contact = triage.email or email.reply_address
    if contact and contact not in who:
        who = f'{who} <{contact}>'
    lines = [
        texts.text(
            language,
            'hot_title',
            # Огонь — только для срочного: если гореть будет каждое письмо, срочное не заметят.
            icon='🔥' if triage.urgency == 'high' else '📩',
            category=category.title(texts.lang(language)),
            urgency=texts.text(language, f'urgency_{triage.urgency}'),
            who=who,
        ),
        triage.summary,
    ]
    if triage.phone:
        phone = triage.phone
        # E.164 рядом с записью из письма: Telegram делает «+79161234567» ссылкой для звонка в один тап.
        if triage.phone_e164 and triage.phone_e164 != phone.replace(' ', ''):
            phone = f'{phone} ({triage.phone_e164})'
        lines.append(texts.text(language, 'hot_phone', phone=phone))
    if triage.budget:
        lines.append(texts.text(language, 'hot_budget', budget=triage.budget))
    lines.append(texts.text(language, 'hot_draft' if drafted else 'hot_no_draft'))
    return '\n'.join(lines)


def _sender(email: ParsedEmail | None) -> str:
    if email is None:
        return ''
    return email.from_addr or email.from_name


class _Run:
    """Состояние одного запуска; живёт только внутри `run_once`."""

    def __init__(self, config: InboxConfig, deps: InboxDeps, now: dt.datetime) -> None:
        self.config = config
        self.section = config.inbox
        self.deps = deps
        self.store = deps.store
        self.now = now
        self.language = config.client.language
        self.zone = config.client.zone
        self.outcomes: list[EmailOutcome] = []
        self.failures: list[_Failure] = []
        self.errors: list[str] = []
        self.aborted: str | None = None
        self.fetched = 0
        self.given_up = 0
        self.sheet_rows = 0
        self.invoice_rows = 0
        self.postponed = 0
        self.invoice_counts: dict[str, int] = {}
        self.uidvalidity: int | None = None
        self.calls_before = deps.llm.usage.calls
        self.stage = ''
        # Зависимости, которые в этом запуске сработали хотя бы раз, и те, что отказали и
        # ещё ни разу не сработали: по ним решается, виновато письмо или поломка.
        self.healthy: set[str] = set()
        self.down: set[str] = set()
        self.misses: dict[str, int] = {}
        self.quiet_before: dt.datetime | None = None
        self.probe_error: LLMError | None = None
        self.config_error: LLMError | None = None

    # --- запуск -------------------------------------------------------------------------

    async def execute(self) -> RunResult:
        folder = self.section.folder
        async with self.deps.mailbox as box:
            state = await box.select(folder)
            self.uidvalidity = state.uidvalidity
            cursor = self.store.cursor(folder, state.uidvalidity)
            if cursor is None:
                # Первый запуск или сменилась UIDVALIDITY: берём окно по дате, а уже
                # разобранное узнаём по Message-ID — заново ничего не обрабатывается.
                since = (self.now.astimezone(self.zone) - dt.timedelta(days=self.section.first_run_since_days)).date()
                uids = await box.search(since=since)
                self.quiet_before = self.now - FIRST_RUN_QUIET
            else:
                uids = await box.search(after_uid=cursor)
            pending = [uid for uid in uids if not self.store.uid_done(folder, state.uidvalidity, uid)]
            failed_before = self.store.failed_uids(folder, state.uidvalidity)
            if cursor is not None:
                # Письмо на повторе, которого SEARCH больше не видит, ушло из папки (в Gmail —
                # архив): дописываем его по сохранённому разбору, а не ждём вечно.
                found = set(uids)
                for uid in sorted(uid for uid in failed_before if uid > cursor and uid not in found):
                    self._vanished(state, uid)
            pending.sort(key=lambda uid: (uid in failed_before, uid))
            await self._process_batch(box, state, pending[: self.section.max_per_run])
            await self._settle_failures(state)
            self._advance_cursor(state, cursor, uids)
        await self._flush_sheet()
        await self._flush_invoices()
        self.store.purge(self.now - RETENTION)
        return self._result()

    async def _process_batch(self, box: MailBox, state: FolderState, batch: list[int]) -> None:
        consecutive = 0
        for uid in batch:
            email: ParsedEmail | None = None
            raw: bytes | None = None
            try:
                self.stage = 'fetch'
                raw = await box.fetch(uid)
                self.fetched += 1
                self._ok('mailbox')
                if raw is None:
                    # Письмо удалили (или переложили) между SEARCH и FETCH.
                    self._vanished(state, uid)
                    continue
                self.stage = 'parse'
                email = parse_email(raw, now=self.now, max_body_chars=self.section.max_body_chars)
                await self._handle(box, state, uid, email, raw)
                consecutive = 0
            except Exception as exc:
                # Граница изоляции: письмо от постороннего может уронить что угодно, но не
                # весь запуск. Трассировка уходит в лог, письмо — на повтор.
                if email is None and raw is not None and self._known_unreadable(state, uid, raw):
                    continue
                logger.exception('inbox: письмо UID %s не разобрано (шаг %s)', uid, self.stage)
                cause = _cause(self.stage, exc)
                if isinstance(exc, LLMError) and exc.kind == 'config':
                    self.config_error = exc
                if cause != 'email' and cause not in self.healthy:
                    self.misses[cause] = self.misses.get(cause, 0) + 1
                    if self.misses[cause] >= FAIL_FAST:
                        self.down.add(cause)
                key = email.message_id if email is not None else (unreadable_key(raw) if raw is not None else None)
                error = f'{type(exc).__name__}: {exc}'[:300]
                signature = failure_signature(exc)
                self.failures.append(_Failure(uid, email, key, error, cause, self.stage, signature))
                self.outcomes.append(
                    EmailOutcome(
                        uid=uid,
                        subject=email.subject if email else '',
                        sender=_sender(email),
                        source='failed',
                        error=error,
                    )
                )
                # Серию считают только поломки модели и ящика: битое письмо или отказ модели
                # на его содержимом ничего не говорят о том, жива ли модель.
                consecutive = consecutive + 1 if cause in _BREAKER_CAUSES else 0
                if consecutive >= BREAKER:
                    self.aborted = f'{consecutive} писем подряд не разобраны, последняя ошибка: {signature}'
                    break

    def _ok(self, dependency: str) -> None:
        self.healthy.add(dependency)
        self.down.discard(dependency)
        if dependency == 'model':
            self.store.mark_healthy('model', self.now)

    def _guard(self, stage: str, dependency: str, error: AutoStudioError) -> None:
        # Канал отказал уже несколько раз подряд и ни разу не сработал: не ждём его таймаута
        # на каждом письме и не платим модели за черновик, который некуда положить.
        self.stage = stage
        if dependency in self.down:
            raise error

    def _known_unreadable(self, state: FolderState, uid: int, raw: bytes) -> bool:
        key = unreadable_key(raw)
        known = self.store.get(key)
        if known is None or known.status not in ('done', 'failed'):
            return False
        # То же нечитаемое письмо уже закрыто — под старым UID, до смены UIDVALIDITY.
        self.store.mark_uid(self.section.folder, state.uidvalidity, uid, key, self.now)
        self.outcomes.append(EmailOutcome(uid=uid, subject='', sender='', source='skipped'))
        return True

    def _vanished(self, state: FolderState, uid: int) -> None:
        store = self.store
        folder = self.section.folder
        key = store.failure_message_id(folder, state.uidvalidity, uid)
        stored = store.get(key) if key else None
        if key is not None and stored is not None and stored.status == 'triaged' and stored.triage is not None:
            if stored.envelope is not None:
                # Письмо разобрано, но ушло из папки (в Gmail — архив) до конца действий:
                # лид не должен пропасть из таблицы, а разбор — остаться в базе навсегда.
                email = stored.envelope.as_email(key)
                self._finish(
                    state, uid, email, stored.triage, stored.actions, [texts.text(self.language, 'flag_vanished')]
                )
                self.outcomes.append(
                    EmailOutcome(
                        uid=uid,
                        subject=email.subject,
                        sender=_sender(email),
                        source='vanished',
                        category=stored.triage.category,
                        urgency=stored.triage.urgency,
                    )
                )
                return
            store.finish(
                key,
                status='done',
                category=stored.triage.category,
                urgency=stored.triage.urgency,
                sheet_row=None,
                now=self.now,
            )
        store.mark_uid(folder, state.uidvalidity, uid, key, self.now)

    async def _handle(self, box: MailBox, state: FolderState, uid: int, email: ParsedEmail, raw: bytes) -> None:
        store = self.store
        stored = store.get(email.message_id)
        if stored is not None and stored.status in ('done', 'failed'):
            # То же письмо уже разобрано: под другим UID (смена UIDVALIDITY) или дубль доставки.
            store.mark_uid(self.section.folder, state.uidvalidity, uid, email.message_id, self.now)
            self.outcomes.append(EmailOutcome(uid=uid, subject=email.subject, sender=_sender(email), source='skipped'))
            return
        if stored is None or stored.triage is None:
            self.stage = 'triage'
            triage = await triage_email(email, self.section, self.language, self.deps.llm)
            if triage.source == 'model':
                self._ok('model')
            store.save_triage(email.message_id, triage, self.now, Envelope.of(email))
            stored = store.get(email.message_id)
        if stored is None or stored.triage is None:  # pragma: no cover - только что записано
            raise RuntimeError('разбор письма не сохранился')
        outcome, notes = await self._act(box, uid, email, stored.triage, stored, raw)
        self.stage = 'finish'
        final = store.get(email.message_id)
        self._finish(state, uid, email, stored.triage, final.actions if final else frozenset(), notes)
        self.outcomes.append(outcome)

    def _finish(
        self,
        state: FolderState,
        uid: int,
        email: ParsedEmail,
        triage: TriageResult,
        actions: frozenset[str],
        notes: list[str],
    ) -> None:
        draft_cell = 'no'
        if 'draft' in actions:
            draft_cell = 'draft_check' if 'draft_check' in actions else 'yes'
        row = build_row(
            email,
            triage,
            zone=self.zone,
            language=self.language,
            mask=self.section.mask_pii_in_sheet,
            draft=texts.text(self.language, draft_cell),
            notes=[*self._notes(triage, 'quiet' in actions), *self._invoice_notes(actions), *notes],
        )
        self.store.finish(
            email.message_id,
            status='done',
            category=triage.category,
            urgency=triage.urgency,
            sheet_row=row,
            now=self.now,
        )
        self.store.mark_uid(self.section.folder, state.uidvalidity, uid, email.message_id, self.now)
        self._record_events(triage)

    def _notes(self, triage: TriageResult, quiet: bool) -> list[str]:
        notes: list[str] = []
        if triage.injection_suspected:
            notes.append(texts.text(self.language, 'flag_injection'))
        if triage.source == 'prefilter':
            notes.append(texts.text(self.language, 'flag_prefilter'))
        elif triage.confidence < LOW_CONFIDENCE and not triage.injection_suspected:
            notes.append(texts.text(self.language, 'flag_low_confidence'))
        if triage.corrected:
            fields = texts.field_names(self.language, triage.corrected)
            notes.append(texts.text(self.language, 'flag_dropped', fields=fields))
        if quiet:
            notes.append(texts.text(self.language, 'flag_quiet'))
        return notes

    def _invoice_notes(self, actions: frozenset[str]) -> list[str]:
        # Пометка в строке письма выводится из отметок действий, а не из памяти запуска:
        # повтор после сбоя (или письмо, ушедшее из папки) даёт ту же строку.
        if 'invoice_check' in actions:
            return [texts.text(self.language, 'flag_invoice_check')]
        if 'invoice_ok' in actions:
            return [texts.text(self.language, 'flag_invoice_ok')]
        return []

    def _is_quiet(self, email: ParsedEmail, done: frozenset[str]) -> bool:
        if 'quiet' in done:
            return True
        return self.quiet_before is not None and not email.date_fallback and email.date < self.quiet_before

    async def _act(
        self, box: MailBox, uid: int, email: ParsedEmail, triage: TriageResult, stored: StoredMessage, raw: bytes
    ) -> tuple[EmailOutcome, list[str]]:
        store = self.store
        mid = email.message_id
        done = stored.actions
        category = self.section.category(triage.category)
        injection = triage.injection_suspected
        quiet = self._is_quiet(email, done)
        if quiet and 'quiet' not in done:
            # Решение «старое письмо» запоминается: повтор в следующем запуске (уже с курсором)
            # не должен вдруг создать черновик для давно отвеченного письма.
            store.add_action(mid, 'quiet', self.now)
        # Подозрение на инъекцию отключает всё, что делается «по письму»: ни черновика,
        # ни перекладывания, ни уведомления владельцу. Остаются строка с пометкой и сигнал оператору.
        wants_draft = category.draft_reply and triage.needs_reply and email.replyable and not (injection or quiet)
        wants_copy = category.move_to is not None and not injection
        wants_notify = (
            category.notify
            and not (injection or quiet)
            and URGENCY_RANK[triage.urgency] >= URGENCY_RANK[self.section.notify_on_urgency]
        )
        # Счёт из PDF — данные, а не действие «по письму», поэтому и для старых писем при
        # подключении; но не для письма с подозрением на инъекцию.
        wants_invoices = category.id in self.section.invoice_category_ids and not injection
        notes: list[str] = []

        drafted = 'draft' in done
        draft_check: list[str] = []
        if wants_draft and not drafted:
            draft = await self._draft(email, triage, stored.draft)
            message = compose_reply(
                email,
                body=draft.body,
                from_addr=self.section.from_address or self.deps.mailbox_user,
                from_name=self.section.company.name,
                now=self.now,
                language=draft.language,
                zone=self.zone,
            )
            self._guard('append', 'drafts', MailboxError('папка черновиков уже отказала в этом запуске'))
            await box.append(self.section.drafts_folder, message, self.now)
            self._ok('drafts')
            store.add_action(mid, 'draft', self.now)
            if draft.unsupported:
                store.add_action(mid, 'draft_check', self.now)
            self.deps.events.record(
                PACKAGE, 'inbox.draft_created', self.now, {'category': category.id, 'language': draft.language}
            )
            drafted = True
            draft_check = draft.unsupported

        copied_to: str | None = None
        if wants_copy and category.move_to is not None:
            if 'copy' not in done:
                target = f'copy:{category.move_to}'
                self._guard(target, target, MailboxError(f'папка {category.move_to} уже отказала в этом запуске'))
                await box.copy(uid, category.move_to)
                self._ok(target)
                store.add_action(mid, 'copy', self.now)
            copied_to = category.move_to

        notified = 'notify' in done
        if wants_notify and not notified:
            self._guard('notify', 'notifier', NotifyError('канал владельца уже отказал в этом запуске'))
            await self.deps.notifier.send(hot_lead_message(self.language, email, triage, category, drafted))
            self._ok('notifier')
            store.add_action(mid, 'notify', self.now)
            notified = True

        invoices: list[str] = []
        if wants_invoices and 'invoice' not in done:
            invoices = await self._invoices(email, raw)

        if injection and self.section.alert_operator_on_injection and 'operator' not in done:
            notes += await self._alert_operator(email, triage)

        outcome = EmailOutcome(
            uid=uid,
            subject=email.subject,
            sender=_sender(email),
            source=triage.source,
            category=triage.category,
            urgency=triage.urgency,
            draft=drafted and wants_draft,
            draft_check=draft_check,
            copied_to=copied_to,
            notified=notified and wants_notify,
            injection=injection,
            quiet=quiet,
            corrected=list(triage.corrected),
            invoices=invoices,
        )
        return outcome, notes

    async def _invoices(self, email: ParsedEmail, raw: bytes) -> list[str]:
        self.stage = 'invoice'
        records = await extract_invoices(
            raw,
            email,
            section=self.section.invoices,
            company=self.section.company.name,
            llm=self.deps.llm,
            sender=sender_cell(email, self.section.mask_pii_in_sheet),
        )
        store, mid = self.store, email.message_id
        if any(record.status != 'manual' for record in records):
            self._ok('model')
        store.save_invoices(mid, records, self.now)
        if records and all(record.status == 'verified' for record in records):
            store.add_action(mid, 'invoice_ok', self.now)
        elif records:
            store.add_action(mid, 'invoice_check', self.now)
        store.add_action(mid, 'invoice', self.now)
        for record in records:
            self._record_invoice(record)
        return [record.status for record in records]

    def _record_invoice(self, record: InvoiceRecord) -> None:
        self.invoice_counts[record.status] = self.invoice_counts.get(record.status, 0) + 1
        if record.status == 'verified':
            payload = {'currency': record.currency or '', 'lines': str(record.line_count)}
            self.deps.events.record(PACKAGE, 'inbox.invoice_extracted', self.now, payload)
            return
        reason = record.reasons[0] if record.reasons else record.status
        self.deps.events.record(PACKAGE, 'inbox.invoice_unverified', self.now, {'reason': reason})

    async def _draft(self, email: ParsedEmail, triage: TriageResult, saved: Draft | None) -> Draft:
        if saved is not None:
            return saved
        self._guard('append', 'drafts', MailboxError('папка черновиков уже отказала в этом запуске'))
        self.stage = 'draft'
        draft = await write_draft(email, triage, self.section, self.language, self.deps.llm)
        self._ok('model')
        self.store.save_draft(email.message_id, draft, self.now)
        return draft

    async def _alert_operator(self, email: ParsedEmail, triage: TriageResult) -> list[str]:
        # Сигнал оператору — служебный: его сбой не должен держать строку клиента вне таблицы.
        if self.deps.operator is None:
            logger.warning('inbox: подозрение на инъекцию, но канал оператора не настроен')
            return []
        try:
            await self.deps.operator.send(
                texts.operator_injection(
                    self.config.client.name, email.sender_domain, email.subject, triage.injection_markers
                )
            )
        except AutoStudioError as exc:
            logger.error('inbox: оператор не уведомлён о подозрительном письме: %s', exc)
            self.errors.append(f'сигнал оператору о подозрительном письме не доставлен: {exc}')
            return [texts.text(self.language, 'flag_operator_failed')]
        self.store.add_action(email.message_id, 'operator', self.now)
        return []

    def _record_events(self, triage: TriageResult) -> None:
        events = self.deps.events
        category = self.section.category(triage.category)
        if triage.source == 'prefilter':
            events.record(PACKAGE, 'inbox.filtered', self.now, {'category': triage.category})
        else:
            events.record(
                PACKAGE,
                'inbox.triaged',
                self.now,
                {
                    'category': triage.category,
                    'urgency': triage.urgency,
                    'needs_reply': 'yes' if triage.needs_reply and not triage.injection_suspected else 'no',
                },
            )
        if triage.injection_suspected:
            source = 'rules' if triage.injection_markers else 'model'
            events.record(PACKAGE, 'inbox.injection_blocked', self.now, {'detected_by': source})
        elif category.is_lead:
            events.record(PACKAGE, 'inbox.lead_found', self.now, {'category': category.id, 'urgency': triage.urgency})

    # --- неудачи, курсор, таблица -------------------------------------------------------

    def _postpone(self, failure: _Failure) -> bool:
        cause = failure.cause
        if cause == 'email':
            return False
        if cause == 'model':
            # Сеть, ключ, лимит, перегрузка — письмо не виновато, даже если минуту назад
            # модель отвечала: лимит запросов как раз и кончается посреди запуска.
            return True
        if cause == 'model_content':
            # Отказ на этом письме — его вина, только если модель в этом же запуске ответила
            # (`_check_model`) и не лежит. Давний успех не в счёт: после смены модели или effort
            # в .env провайдер отвечает 400 на каждый запрос, и списать письма было бы ошибкой.
            return 'model' not in self.healthy or any(other.cause == 'model' for other in self.failures)
        return self.aborted is not None or cause not in self.healthy

    async def _check_model(self) -> None:
        # Модель отказала на письмах и ни разу не ответила в этом запуске: виновато письмо или
        # настройка? Один вызов на безобидном письме отвечает на это прямо — и одинокое
        # «ядовитое» письмо в тихом ящике не ждёт вечно, и поломку оператор видит с первого запуска.
        content = any(failure.cause == 'model_content' for failure in self.failures)
        outage = any(failure.cause == 'model' for failure in self.failures)
        if not content or outage or 'model' in self.healthy:
            return
        self.stage = 'probe'
        try:
            await probe_model(self.section, self.language, self.deps.llm, self.now)
        except LLMError as exc:
            logger.warning('inbox: модель не ответила и на проверочный запрос: %s', exc)
            self.probe_error = exc
            return
        self._ok('model')

    def _probe_report(self, exc: LLMError) -> str:
        # Текст для оператора: что проверить. Давний успех модели подсказывает, что сломала
        # недавняя правка .env, а не провайдер.
        last_ok = self.store.healthy_at('model')
        since = 'модель ещё ни разу не ответила'
        if last_ok is not None:
            since = f'последний успешный ответ модели: {last_ok.astimezone(dt.UTC):%Y-%m-%d %H:%M} UTC'
        if exc.kind == 'config':
            return f'провайдер отклоняет ключ или модель — проверьте ключ и AUTOSTUDIO_LLM_MODEL ({since}): {exc}'
        if model_outage(exc):
            return f'модель недоступна и на проверочном запросе ({since}): {exc}'
        return (
            'модель отклоняет и проверочный запрос с безобидным письмом — проверьте AUTOSTUDIO_LLM_MODEL '
            f'и AUTOSTUDIO_LLM_EFFORT ({since}): {exc}'
        )

    async def _settle_failures(self, state: FolderState) -> None:
        await self._check_model()
        postponed: list[_Failure] = []
        folder = self.section.folder
        for failure in self.failures:
            if self._postpone(failure):
                self.store.record_failure(
                    folder,
                    state.uidvalidity,
                    failure.uid,
                    failure.error,
                    now=self.now,
                    message_id=failure.key,
                    count=False,
                )
                if not await self._grace_over(state, failure):
                    postponed.append(failure)
                continue
            attempts = self.store.record_failure(
                folder, state.uidvalidity, failure.uid, failure.error, now=self.now, message_id=failure.key
            )
            if attempts >= MAX_ATTEMPTS:
                await self._give_up(state, failure)
        self.postponed = len(postponed)
        if postponed:
            # Без счётчика писем и без текста конкретного письма: новое письмо, отложенное по
            # той же причине, не должно поднимать сигнал оператору на каждом запуске. Та же
            # строка и при срабатывании прерывателя: для оператора «модель лежит, письма ждут» —
            # одно состояние, сколько бы писем ни накопилось.
            signatures: dict[str, set[str]] = {}
            for failure in postponed:
                signatures.setdefault(failure.cause, set()).add(failure.signature)
            causes = '; '.join(f'{cause} — {min(signatures[cause])}' for cause in sorted(signatures))
            self.errors.append(f'письма отложены до следующего запуска (не работает: {causes})')
            if self.probe_error is not None:
                self.errors.append(self._probe_report(self.probe_error))
            elif self.config_error is not None:
                self.errors.append(self._probe_report(self.config_error))

    async def _grace_over(self, state: FolderState, failure: _Failure) -> bool:
        if failure.key is None or failure.email is None:
            return False
        stored = self.store.get(failure.key)
        if stored is None or stored.triage is None or stored.created_at > self.now - OUTAGE_GRACE:
            return False
        await self._give_up(state, failure)
        return True

    def _failure_note(self, failure: _Failure) -> str:
        cause = failure.cause
        if cause == 'notifier':
            return texts.text(self.language, 'flag_failed_notify')
        if cause == 'drafts':
            return texts.text(self.language, 'flag_failed_draft')
        if cause in ('model', 'model_content') and failure.stage == 'invoice':
            return texts.text(self.language, 'flag_failed_invoice')
        if cause in ('model', 'model_content'):
            return texts.text(self.language, 'flag_failed_model')
        if cause.startswith('copy:'):
            return texts.text(self.language, 'flag_failed_copy', folder=cause.removeprefix('copy:'))
        return texts.text(self.language, 'flag_failed_other')

    async def _give_up(self, state: FolderState, failure: _Failure) -> None:
        store = self.store
        email = failure.email
        stored = store.get(failure.key) if failure.key is not None else None
        kind = failure.error.split(':', 1)[0]
        if email is not None and stored is not None and stored.triage is not None:
            # Письмо разобрано, не прошёл один шаг: в таблицу идёт сам разбор (категория,
            # телефон, бюджет) и честная пометка, а не пустая строка «прочитайте сами».
            self._finish(state, failure.uid, email, stored.triage, stored.actions, [self._failure_note(failure)])
            message = texts.operator_degraded(self.config.client.name, failure.uid, failure.cause, failure.error)
        else:
            key = failure.key or f'<uid-{state.uidvalidity}-{failure.uid}@{GENERATED_ID_DOMAIN}>'
            row = failure_row(
                email,
                failure.uid,
                now=self.now,
                zone=self.zone,
                language=self.language,
                mask=self.section.mask_pii_in_sheet,
            )
            store.finish(key, status='failed', category=None, urgency=None, sheet_row=row, now=self.now)
            store.mark_uid(self.section.folder, state.uidvalidity, failure.uid, key, self.now)
            message = texts.operator_gave_up(self.config.client.name, failure.uid, failure.error)
        self.deps.events.record(PACKAGE, 'inbox.failed', self.now, {'error': kind, 'step': failure.cause})
        self.given_up += 1
        if self.deps.operator is not None:
            try:
                await self.deps.operator.send(message)
            except AutoStudioError as exc:
                logger.error('inbox: оператор не уведомлён о брошенном письме: %s', exc)

    def _advance_cursor(self, state: FolderState, cursor: int | None, uids: list[int]) -> None:
        store = self.store
        folder = self.section.folder
        if cursor is not None:
            base: int | None = cursor
        elif uids:
            base = uids[0] - 1
        else:
            base = state.uidnext - 1 if state.uidnext is not None else None
        if base is None:
            return
        # Курсор идёт только через непрерывный префикс разобранных: письмо на повторе
        # держит его, и следующий запуск снова его увидит.
        for uid in uids:
            if not store.uid_done(folder, state.uidvalidity, uid):
                break
            base = max(base, uid)
        store.set_cursor(folder, state.uidvalidity, base)

    async def _flush_sheet(self) -> None:
        pending = self.store.pending_rows()
        if not pending:
            return
        try:
            await self.deps.sink.append(
                self.section.sheet_tab, texts.sheet_header(self.language), [row for _, row in pending]
            )
        except AutoStudioError as exc:
            self.errors.append(f'таблица не записана, строки повторятся в следующий запуск: {exc}')
            return
        self.store.rows_written([message_id for message_id, _ in pending])
        self.sheet_rows = len(pending)

    async def _flush_invoices(self) -> None:
        pending = self.store.pending_invoices()
        if not pending:
            return
        rows = [invoice_row(record, zone=self.zone, language=self.language) for _, _, record in pending]
        try:
            await self.deps.sink.append(self.section.invoices.sheet_tab, texts.invoice_header(self.language), rows)
        except AutoStudioError as exc:
            self.errors.append(f'вкладка счетов не записана, строки повторятся в следующий запуск: {exc}')
            return
        self.store.invoices_written([(message_id, position) for message_id, position, _ in pending])
        self.invoice_rows = len(pending)

    def _result(self) -> RunResult:
        done = [outcome for outcome in self.outcomes if outcome.source in ('prefilter', 'model')]
        by_category: dict[str, int] = {}
        for outcome in done:
            by_category[outcome.category] = by_category.get(outcome.category, 0) + 1
        return RunResult(
            folder=self.section.folder,
            uidvalidity=self.uidvalidity,
            fetched=self.fetched,
            processed=len(done),
            skipped=sum(outcome.source == 'skipped' for outcome in self.outcomes),
            failed=len(self.failures),
            given_up=self.given_up,
            drafts=sum(outcome.draft for outcome in done),
            leads=sum(self.section.category(o.category).is_lead and not o.injection for o in done),
            notified=sum(outcome.notified for outcome in done),
            injections=sum(outcome.injection for outcome in done),
            llm_calls=self.deps.llm.usage.calls - self.calls_before,
            sheet_rows=self.sheet_rows,
            invoices_verified=self.invoice_counts.get('verified', 0),
            invoices_unverified=sum(n for status, n in self.invoice_counts.items() if status != 'verified'),
            invoice_rows=self.invoice_rows,
            postponed=self.postponed,
            by_category=by_category,
            outcomes=self.outcomes,
            aborted=self.aborted,
            errors=self.errors,
        )


async def run_once(config: InboxConfig, deps: InboxDeps, now: dt.datetime) -> RunResult:
    """Один запуск разбора: идемпотентен — повтор по тем же письмам ничего не делает.

    Args:
        config: Конфиг клиента.
        deps: Зависимости запуска.
        now: Момент запуска, с часовым поясом.

    Returns:
        Итог запуска.

    Raises:
        MailboxError: Ящик недоступен (сигнал оператору поднимет `supervised_run`).
        RunSkipped: Предыдущий запуск ещё держит блокировку — этот сознательно пропущен
            (`supervised_run` пишет пропуск, а не успех и не сбой).
    """
    if now.tzinfo is None:
        raise ValueError('now должен быть с часовым поясом')
    # Блокировка живёт дольше самого долгого запуска (≈ 2 минуты на письмо с черновиком),
    # но не вечно: упавший процесс не должен остановить разбор надолго.
    ttl = dt.timedelta(minutes=max(LOCK_MINUTES_MIN, 2 * config.inbox.max_per_run))
    token = deps.store.acquire_lock(now, ttl)
    if token is None:
        raise RunSkipped('предыдущий запуск ещё идёт')
    try:
        return await _Run(config, deps, now).execute()
    finally:
        deps.store.release_lock(token)


# --- пробный разбор без действий -------------------------------------------------------


class DryRow(msgspec.Struct, frozen=True, kw_only=True):
    """Строка пробного разбора: что было бы сделано с письмом."""

    date: dt.datetime
    sender: str
    subject: str
    category: str
    urgency: str
    confidence: float
    source: str
    would: list[str]
    notes: list[str]


def _dry_row(config: InboxConfig, email: ParsedEmail, triage: TriageResult) -> DryRow:
    section = config.inbox
    category = section.category(triage.category)
    would: list[str] = []
    if triage.injection_suspected:
        would.append('operator-alert')
    else:
        if category.draft_reply and triage.needs_reply:
            would.append('draft')
        if category.notify and URGENCY_RANK[triage.urgency] >= URGENCY_RANK[section.notify_on_urgency]:
            would.append('notify')
        if category.move_to:
            would.append(f'copy→{category.move_to}')
        if category.id in section.invoice_category_ids:
            would.append('invoice-check')
    notes = ['injection'] if triage.injection_suspected else []
    notes += [f'dropped:{field}' for field in triage.corrected]
    if triage.source == 'model' and triage.confidence < LOW_CONFIDENCE:
        notes.append('low-confidence')
    return DryRow(
        date=email.date,
        sender=email.from_addr,
        subject=email.subject,
        category=triage.category,
        urgency=triage.urgency,
        confidence=triage.confidence,
        source=triage.source,
        would=would,
        notes=notes,
    )


async def dry_run(
    config: InboxConfig,
    mailbox: MailBox,
    llm: LLMClient,
    now: dt.datetime,
    limit: int,
    progress: Callable[[int, int], None] | None = None,
) -> list[DryRow]:
    """Классифицирует последние письма БЕЗ действий: ни черновиков, ни таблицы, ни журнала.

    Для продажи на живом ящике клиента (с его согласия): папка открывается только на
    чтение, письма читаются через PEEK, состояние не пишется. Письмо, которое не удалось
    разобрать, становится строкой с ошибкой — показ на встрече не обрывается.

    Args:
        config: Конфиг клиента.
        mailbox: Ящик.
        llm: Модель.
        now: Текущий момент.
        limit: Сколько последних писем разобрать.
        progress: Вызывается после каждого письма: (сделано, всего).

    Returns:
        Строки от новых к старым.
    """
    section = config.inbox
    language = config.client.language
    rows: list[DryRow] = []
    async with mailbox as box:
        await box.select(section.folder)
        uids = await box.search(since=(now - DRY_RUN_WINDOW).date())
        chosen = list(reversed(uids[-limit:]))
        for index, uid in enumerate(chosen, start=1):
            email: ParsedEmail | None = None
            try:
                raw = await box.fetch(uid)
                if raw is not None:
                    email = parse_email(raw, now=now, max_body_chars=section.max_body_chars)
                    triage = await triage_email(email, section, language, llm)
                    rows.append(_dry_row(config, email, triage))
            except Exception as exc:
                # Граница изоляции, как в боевом запуске: одно письмо не обрывает показ.
                logger.exception('inbox dry-run: письмо UID %s не разобрано', uid)
                rows.append(
                    DryRow(
                        date=email.date if email else now,
                        sender=email.from_addr if email else '',
                        subject=email.subject if email else f'UID {uid}',
                        category='',
                        urgency='',
                        confidence=0.0,
                        source='error',
                        would=[],
                        notes=[type(exc).__name__],
                    )
                )
            if progress is not None:
                progress(index, len(chosen))
    return rows

"""Один запуск разбора почты: новые письма → разбор → действия → таблица → журнал.

Порядок для каждого письма: скачать (PEEK) → разобрать → правила → модель →
проверка кодом → черновик / копия в папку / уведомление владельцу / сигнал оператору.
Каждое выполненное действие отмечается сразу, поэтому повтор после сбоя доделывает
только недостающее. Строки таблицы пишутся одной пачкой в конце запуска (лимиты
Google Sheets) и хранятся в SQLite, пока запись не удалась.

Сбой одного письма не останавливает запуск: письмо повторяется в следующих запусках,
после `MAX_ATTEMPTS` неудач оно попадает в таблицу как «нужна ручная проверка», а
оператор получает сигнал. Сбой инфраструктуры (модель, канал уведомлений, ящик) письму
засчитывается, только если модель в этом же запуске работала: при общей поломке письмо
не виновато и ждёт. Если подряд падают `BREAKER` писем по вине инфраструктуры, запуск
прерывается — оператор узнаёт об этом через `supervised_run`.
"""

import datetime as dt
import logging
from typing import Final, Literal

import msgspec

from autostudio.errors import AutoStudioError, LLMError, NotifyError, SinkError
from autostudio.events import EventLog
from autostudio.inbox import texts
from autostudio.inbox.config import URGENCY_RANK, Category, InboxConfig
from autostudio.inbox.errors import MailboxError
from autostudio.inbox.imap import FolderState, MailBox
from autostudio.inbox.mail import GENERATED_ID_DOMAIN, ParsedEmail, compose_reply, parse_email
from autostudio.inbox.sheet import build_row, failure_row
from autostudio.inbox.store import InboxStore
from autostudio.inbox.triage import TriageResult, prefilter, triage_email, write_draft
from autostudio.llm import LLMClient
from autostudio.notify import Notifier
from autostudio.sinks import TableSink

logger = logging.getLogger(__name__)

PACKAGE: Final = 'inbox'
MAX_ATTEMPTS: Final = 3
BREAKER: Final = 3
LOCK_MINUTES_MIN: Final = 30
RETENTION: Final = dt.timedelta(days=90)
DRY_RUN_WINDOW: Final = dt.timedelta(days=14)

type Source = Literal['prefilter', 'model', 'skipped', 'failed', 'gave_up']


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
    corrected: list[str] = msgspec.field(default_factory=list)
    error: str | None = None


class RunResult(msgspec.Struct, frozen=True, kw_only=True):
    """Итог запуска.

    Attributes:
        locked: Запуск не начался: идёт другой.
        aborted: Причина остановки по серии сбоев или None.
        errors: Общие ошибки (таблица недоступна) — данные не потеряны, повторятся.
    """

    folder: str
    uidvalidity: int | None = None
    locked: bool = False
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
    by_category: dict[str, int] = msgspec.field(default_factory=dict)
    outcomes: list[EmailOutcome] = msgspec.field(default_factory=list)
    aborted: str | None = None
    errors: list[str] = msgspec.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.aborted is None and not self.errors


# Ошибки инфраструктуры: виновато не письмо, а модель, канал или ящик.
_INFRA_ERRORS: Final = (LLMError, NotifyError, SinkError, MailboxError)


class _Failure(msgspec.Struct, frozen=True):
    uid: int
    email: ParsedEmail | None
    error: str
    infra: bool


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
            category=category.id,
            urgency=texts.text(language, f'urgency_{triage.urgency}'),
            who=who,
        ),
        triage.summary,
    ]
    if triage.phone:
        lines.append(texts.text(language, 'hot_phone', phone=triage.phone))
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
        self.uidvalidity: int | None = None
        self.calls_before = deps.llm.usage.calls

    # --- запуск -------------------------------------------------------------------------

    async def execute(self) -> RunResult:
        folder = self.section.folder
        async with self.deps.mailbox as box:
            state = await box.select(folder)
            self.uidvalidity = state.uidvalidity
            cursor = self.deps.store.cursor(folder, state.uidvalidity)
            if cursor is None:
                # Первый запуск или сменилась UIDVALIDITY: берём окно по дате, а уже
                # разобранное узнаём по Message-ID — заново ничего не обрабатывается.
                since = (self.now.astimezone(self.zone) - dt.timedelta(days=self.section.first_run_since_days)).date()
                uids = await box.search(since=since)
            else:
                uids = await box.search(after_uid=cursor)
            pending = [uid for uid in uids if not self.deps.store.uid_done(folder, state.uidvalidity, uid)]
            await self._process_batch(box, state, pending[: self.section.max_per_run])
            await self._settle_failures(state)
            self._advance_cursor(state, cursor, uids)
        await self._flush_sheet()
        self.deps.store.purge(self.now - RETENTION)
        return self._result()

    async def _process_batch(self, box: MailBox, state: FolderState, batch: list[int]) -> None:
        consecutive = 0
        for uid in batch:
            email: ParsedEmail | None = None
            try:
                raw = await box.fetch(uid)
                self.fetched += 1
                if raw is None:
                    # Письмо удалили между SEARCH и FETCH — разбирать нечего.
                    self.deps.store.mark_uid(self.section.folder, state.uidvalidity, uid, None, self.now)
                    continue
                email = parse_email(raw, now=self.now, max_body_chars=self.section.max_body_chars)
                await self._handle(box, state, uid, email)
                consecutive = 0
            except Exception as exc:
                # Граница изоляции: письмо от постороннего может уронить что угодно, но не
                # весь запуск. Трассировка уходит в лог, письмо — на повтор.
                logger.exception('inbox: письмо UID %s не разобрано', uid)
                error = f'{type(exc).__name__}: {exc}'[:300]
                infra = isinstance(exc, _INFRA_ERRORS)
                self.failures.append(_Failure(uid, email, error, infra))
                self.outcomes.append(
                    EmailOutcome(
                        uid=uid,
                        subject=email.subject if email else '',
                        sender=_sender(email),
                        source='failed',
                        error=error,
                    )
                )
                # Битое письмо ничего не говорит о модели: серию считают только сбои инфраструктуры.
                consecutive = consecutive + 1 if infra else 0
                if consecutive >= BREAKER:
                    self.aborted = f'{consecutive} писем подряд не разобраны, последняя ошибка: {error}'
                    break

    async def _handle(self, box: MailBox, state: FolderState, uid: int, email: ParsedEmail) -> None:
        store = self.deps.store
        folder = self.section.folder
        stored = store.get(email.message_id)
        if stored is not None and stored.status in ('done', 'failed'):
            # То же письмо уже разобрано: под другим UID (смена UIDVALIDITY) или дубль доставки.
            store.mark_uid(folder, state.uidvalidity, uid, email.message_id, self.now)
            self.outcomes.append(EmailOutcome(uid=uid, subject=email.subject, sender=_sender(email), source='skipped'))
            return
        if stored is not None and stored.triage is not None:
            triage = stored.triage
        else:
            triage = await triage_email(email, self.section, self.language, self.deps.llm)
            store.save_triage(email.message_id, triage, self.now)
        actions = stored.actions if stored is not None else frozenset()
        outcome = await self._act(box, uid, email, triage, actions)
        draft_cell = 'no'
        if outcome.draft:
            draft_cell = 'draft_check' if outcome.draft_check or 'draft_check' in actions else 'yes'
        row = build_row(
            email,
            triage,
            zone=self.zone,
            language=self.language,
            mask=self.section.mask_pii_in_sheet,
            draft=texts.text(self.language, draft_cell),
            notes=self._notes(triage),
        )
        store.finish(
            email.message_id,
            status='done',
            category=triage.category,
            urgency=triage.urgency,
            sheet_row=row,
            now=self.now,
        )
        store.mark_uid(folder, state.uidvalidity, uid, email.message_id, self.now)
        self._record_events(triage)
        self.outcomes.append(outcome)

    def _notes(self, triage: TriageResult) -> list[str]:
        notes: list[str] = []
        if triage.injection_suspected:
            notes.append(texts.text(self.language, 'flag_injection'))
        if triage.source == 'prefilter':
            notes.append(texts.text(self.language, 'flag_prefilter'))
        if triage.corrected:
            notes.append(texts.text(self.language, 'flag_dropped', fields=', '.join(triage.corrected)))
        return notes

    async def _act(
        self, box: MailBox, uid: int, email: ParsedEmail, triage: TriageResult, done: frozenset[str]
    ) -> EmailOutcome:
        store = self.deps.store
        category = self.section.category(triage.category)
        injection = triage.injection_suspected
        # Подозрение на инъекцию отключает всё, что делается «по письму»: ни черновика,
        # ни перекладывания, ни уведомления владельцу. Остаются строка с пометкой и сигнал оператору.
        wants_draft = category.draft_reply and triage.needs_reply and not injection and email.reply_address.isascii()
        wants_copy = category.move_to is not None and not injection
        wants_notify = (
            category.notify
            and not injection
            and URGENCY_RANK[triage.urgency] >= URGENCY_RANK[self.section.notify_on_urgency]
        )
        wants_operator = injection and self.section.alert_operator_on_injection

        drafted = 'draft' in done
        draft_check: list[str] = []
        if wants_draft and not drafted:
            draft = await write_draft(email, triage, self.section, self.language, self.deps.llm)
            message = compose_reply(
                email,
                body=draft.body,
                from_addr=self.section.from_address or self.deps.mailbox_user,
                from_name=self.section.company.name,
                now=self.now,
                language=draft.language,
                zone=self.zone,
            )
            await box.append(self.section.drafts_folder, message, self.now)
            store.add_action(email.message_id, 'draft', self.now)
            if draft.unsupported:
                store.add_action(email.message_id, 'draft_check', self.now)
            self.deps.events.record(
                PACKAGE, 'inbox.draft_created', self.now, {'category': category.id, 'language': draft.language}
            )
            drafted = True
            draft_check = draft.unsupported

        copied_to: str | None = None
        if wants_copy and category.move_to is not None:
            if 'copy' not in done:
                await box.copy(uid, category.move_to)
                store.add_action(email.message_id, 'copy', self.now)
            copied_to = category.move_to

        notified = 'notify' in done
        if wants_notify and not notified:
            await self.deps.notifier.send(hot_lead_message(self.language, email, triage, category, drafted))
            store.add_action(email.message_id, 'notify', self.now)
            notified = True

        if wants_operator and 'operator' not in done:
            if self.deps.operator is None:
                logger.warning('inbox: подозрение на инъекцию, но канал оператора не настроен')
            else:
                await self.deps.operator.send(
                    texts.operator_injection(
                        self.config.client.name, email.sender_domain, email.subject, triage.injection_markers
                    )
                )
                store.add_action(email.message_id, 'operator', self.now)

        return EmailOutcome(
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
            corrected=list(triage.corrected),
        )

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
        elif category.notify:
            events.record(PACKAGE, 'inbox.lead_found', self.now, {'category': category.id, 'urgency': triage.urgency})

    # --- неудачи, курсор, таблица -------------------------------------------------------

    async def _settle_failures(self, state: FolderState) -> None:
        model_worked = any(outcome.source == 'model' for outcome in self.outcomes)
        postponed: list[_Failure] = []
        for failure in self.failures:
            if failure.infra and (self.aborted is not None or not model_worked):
                # Общая поломка: письмо не виновато, попытку не засчитываем — оно подождёт.
                postponed.append(failure)
                continue
            attempts = self.deps.store.record_failure(
                self.section.folder, state.uidvalidity, failure.uid, failure.error
            )
            if attempts >= MAX_ATTEMPTS:
                await self._give_up(state, failure)
        if postponed and self.aborted is None:
            self.errors.append(f'писем отложено до следующего запуска: {len(postponed)} ({postponed[-1].error})')

    async def _give_up(self, state: FolderState, failure: _Failure) -> None:
        store = self.deps.store
        email = failure.email
        key = (
            email.message_id if email is not None else f'<uid-{state.uidvalidity}-{failure.uid}@{GENERATED_ID_DOMAIN}>'
        )
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
        self.deps.events.record(PACKAGE, 'inbox.failed', self.now, {'error': failure.error.split(':', 1)[0]})
        self.given_up += 1
        if self.deps.operator is not None:
            try:
                await self.deps.operator.send(
                    texts.operator_gave_up(self.config.client.name, failure.uid, failure.error)
                )
            except AutoStudioError as exc:
                logger.error('inbox: оператор не уведомлён о брошенном письме: %s', exc)

    def _advance_cursor(self, state: FolderState, cursor: int | None, uids: list[int]) -> None:
        store = self.deps.store
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
        pending = self.deps.store.pending_rows()
        if not pending:
            return
        try:
            await self.deps.sink.append(
                self.section.sheet_tab, texts.sheet_header(self.language), [row for _, row in pending]
            )
        except AutoStudioError as exc:
            self.errors.append(f'таблица не записана, строки повторятся в следующий запуск: {exc}')
            return
        self.deps.store.rows_written([message_id for message_id, _ in pending])
        self.sheet_rows = len(pending)

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
            leads=sum(self.section.category(o.category).notify and not o.injection for o in done),
            notified=sum(outcome.notified for outcome in done),
            injections=sum(outcome.injection for outcome in done),
            llm_calls=self.deps.llm.usage.calls - self.calls_before,
            sheet_rows=self.sheet_rows,
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
    """
    if now.tzinfo is None:
        raise ValueError('now должен быть с часовым поясом')
    # Блокировка живёт дольше самого долгого запуска (≈ 2 минуты на письмо с черновиком),
    # но не вечно: упавший процесс не должен остановить разбор надолго.
    ttl = dt.timedelta(minutes=max(LOCK_MINUTES_MIN, 2 * config.inbox.max_per_run))
    if not deps.store.acquire_lock(now, ttl):
        logger.warning('inbox: предыдущий запуск ещё идёт — этот пропущен')
        return RunResult(folder=config.inbox.folder, locked=True)
    try:
        return await _Run(config, deps, now).execute()
    finally:
        deps.store.release_lock()


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


async def dry_run(config: InboxConfig, mailbox: MailBox, llm: LLMClient, now: dt.datetime, limit: int) -> list[DryRow]:
    """Классифицирует последние письма БЕЗ действий: ни черновиков, ни таблицы, ни журнала.

    Для продажи на живом ящике клиента (с его согласия): папка открывается только на
    чтение, письма читаются через PEEK, состояние не пишется.

    Args:
        config: Конфиг клиента.
        mailbox: Ящик.
        llm: Модель.
        now: Текущий момент.
        limit: Сколько последних писем разобрать.

    Returns:
        Строки от новых к старым.
    """
    section = config.inbox
    language = config.client.language
    rows: list[DryRow] = []
    async with mailbox as box:
        await box.select(section.folder)
        uids = await box.search(since=(now - DRY_RUN_WINDOW).date())
        for uid in reversed(uids[-limit:]):
            raw = await box.fetch(uid)
            if raw is None:
                continue
            email = parse_email(raw, now=now, max_body_chars=section.max_body_chars)
            early = prefilter(email, section, language)
            triage = early or await triage_email(email, section, language, llm)
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
            notes = ['injection'] if triage.injection_suspected else []
            notes += [f'dropped:{field}' for field in triage.corrected]
            rows.append(
                DryRow(
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
            )
    return rows

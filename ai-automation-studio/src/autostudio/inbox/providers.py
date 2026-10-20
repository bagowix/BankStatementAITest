"""dishka-провайдер пакета inbox поверх `CoreProvider`.

Контейнер собирает команда CLI: в контекст кладутся `Settings` и `InboxConfig`.
Секреты IMAP читаются только при запросе ящика — `check` без `--connect` их не трогает.
"""

import os

import httpx
from dishka import Provider, Scope, from_context, provide

from autostudio.clientconf import client_data_dir, require_env
from autostudio.events import EventLog
from autostudio.inbox.config import InboxConfig
from autostudio.inbox.heuristics import make_fake_llm
from autostudio.inbox.imap import ImapMailBox, MailBox
from autostudio.inbox.pipeline import InboxDeps
from autostudio.inbox.store import InboxStore
from autostudio.llm import LLMClient, make_llm
from autostudio.notify import Notifier, make_notifier
from autostudio.providers import OperatorAlerts
from autostudio.settings import Settings
from autostudio.sinks import TableSink, make_sink


class InboxProvider(Provider):
    """Ящик, модель, таблица, уведомления, состояние и журнал клиента."""

    scope = Scope.APP

    config = from_context(provides=InboxConfig, scope=Scope.APP)

    @provide
    def imap(self, config: InboxConfig) -> ImapMailBox:
        section = config.inbox
        return ImapMailBox(
            host=section.imap_host,
            port=section.imap_port,
            user=require_env(section.username_env, os.environ),
            password=require_env(section.password_env, os.environ),
            timeout=section.timeout_seconds,
        )

    @provide
    def mailbox(self, imap: ImapMailBox) -> MailBox:
        return imap

    @provide
    def llm(self, settings: Settings) -> LLMClient:
        return make_llm(settings, fake=make_fake_llm())

    @provide
    def sink(self, config: InboxConfig) -> TableSink:
        return make_sink(config.sink)

    @provide
    def notifier(self, config: InboxConfig, http: httpx.AsyncClient) -> Notifier:
        return make_notifier(config.notify, http)

    @provide
    def events(self, config: InboxConfig, settings: Settings) -> EventLog:
        return EventLog(client_data_dir(settings.data_dir, config.client) / 'events.sqlite')

    @provide
    def store(self, config: InboxConfig, settings: Settings) -> InboxStore:
        return InboxStore(client_data_dir(settings.data_dir, config.client) / 'inbox.sqlite')

    @provide
    def deps(
        self,
        imap: ImapMailBox,
        llm: LLMClient,
        sink: TableSink,
        notifier: Notifier,
        operator: OperatorAlerts,
        store: InboxStore,
        events: EventLog,
    ) -> InboxDeps:
        return InboxDeps(
            mailbox=imap,
            mailbox_user=imap.user,
            llm=llm,
            sink=sink,
            notifier=notifier,
            operator=operator.notifier,
            store=store,
            events=events,
        )

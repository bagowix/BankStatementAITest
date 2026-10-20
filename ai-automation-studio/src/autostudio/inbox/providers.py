"""dishka-провайдер пакета inbox поверх `CoreProvider`.

Контейнер собирает команда CLI: в контекст кладутся `Settings` и `InboxConfig`.
Секреты IMAP читаются только при запросе ящика — `check` без `--connect` их не трогает.
"""

import os
from collections.abc import AsyncIterator, Iterator, Mapping

import httpx
from dishka import Provider, Scope, from_context, provide

from autostudio.clientconf import client_data_dir, events_path, require_env
from autostudio.events import EventLog
from autostudio.inbox.config import InboxConfig, InboxSection
from autostudio.inbox.heuristics import make_fake_llm
from autostudio.inbox.imap import ImapMailBox, MailBox
from autostudio.inbox.oauth import TIMEOUT, MicrosoftClientCredentialsProvider, TokenProvider
from autostudio.inbox.pipeline import InboxDeps
from autostudio.inbox.store import InboxStore
from autostudio.llm import LLMClient, make_llm
from autostudio.notify import Notifier, make_notifier
from autostudio.providers import OperatorAlerts
from autostudio.settings import LLMProvider, Settings
from autostudio.sinks import TableSink, make_sink


def build_llm(settings: Settings) -> LLMClient:
    """Модель для разбора почты — без запасного выхода на эвристики у настоящего провайдера.

    Ядро умеет подменять упавшую модель эвристиками пакета (`make_llm(..., fake=...,
    on_fallback=...)`). Для почты это вредно: при лимите расходов или сбое провайдера письма
    молча сортировались бы по ключевым словам, в «Черновики» клиента ложились бы шаблоны, а
    счета «сверялись» бы регулярными выражениями. Поэтому сбой модели доходит до пайплайна:
    письмо ждёт, запуск сообщает об ошибке, оператор узнаёт. Эвристики работают, только
    когда заглушка выбрана явно (`AUTOSTUDIO_LLM_PROVIDER=fake`).

    Args:
        settings: Настройки процесса.

    Returns:
        Клиент модели.
    """
    if settings.llm_provider is LLMProvider.FAKE:
        return make_llm(settings, fake=make_fake_llm())
    return make_llm(settings)


def build_token_provider(
    section: InboxSection,
    environ: Mapping[str, str],
    http: httpx.AsyncClient,
    google_http: httpx.Client | None = None,
) -> TokenProvider | None:
    """Источник OAuth-токена для выбранного способа входа (None — вход паролем).

    Args:
        section: Секция `[inbox]`.
        environ: Окружение процесса.
        http: Общий асинхронный httpx-клиент (токен Microsoft).
        google_http: Синхронный httpx-клиент для google-auth (обновление идёт в потоке).

    Returns:
        Провайдер токена или None.

    Raises:
        ConfigError: Не задана нужная переменная окружения.
    """
    match section.auth:
        case 'password':
            return None
        case 'google_oauth':
            # Импорт здесь: google-auth стоит в extras `sheets` и нужен только этим клиентам.
            from autostudio.inbox.google_oauth import GoogleRefreshTokenProvider

            return GoogleRefreshTokenProvider(
                client_id=require_env(section.oauth_client_id_env, environ),
                client_secret=require_env(section.oauth_client_secret_env, environ),
                refresh_token=require_env(section.oauth_refresh_token_env, environ),
                http=google_http,
            )
        case 'microsoft_oauth':
            return MicrosoftClientCredentialsProvider(
                tenant_id=require_env(section.oauth_tenant_id_env, environ),
                client_id=require_env(section.oauth_client_id_env, environ),
                client_secret=require_env(section.oauth_client_secret_env, environ),
                http=http,
            )


def build_mailbox(
    section: InboxSection,
    environ: Mapping[str, str],
    http: httpx.AsyncClient,
    google_http: httpx.Client | None = None,
) -> ImapMailBox:
    """IMAP-ящик клиента с выбранным способом входа.

    Args:
        section: Секция `[inbox]`.
        environ: Окружение процесса.
        http: Общий асинхронный httpx-клиент.
        google_http: Синхронный httpx-клиент для google-auth.

    Returns:
        Ящик (соединение открывается в `async with`).

    Raises:
        ConfigError: Не задана нужная переменная окружения.
    """
    tokens = build_token_provider(section, environ, http, google_http)
    return ImapMailBox(
        host=section.imap_host,
        port=section.imap_port,
        user=require_env(section.username_env, environ),
        password=require_env(section.password_env, environ) if tokens is None else None,
        tokens=tokens,
        timeout=section.timeout_seconds,
    )


class InboxProvider(Provider):
    """Ящик, модель, таблица, уведомления, состояние и журнал клиента."""

    scope = Scope.APP

    config = from_context(provides=InboxConfig, scope=Scope.APP)

    @provide
    def google_http(self) -> Iterator[httpx.Client]:
        # Синхронный клиент для google-auth: обновление токена идёт в потоке. Создаётся без
        # соединений и закрывается вместе с контейнером.
        with httpx.Client(timeout=TIMEOUT) as client:
            yield client

    @provide
    def imap(self, config: InboxConfig, http: httpx.AsyncClient, google_http: httpx.Client) -> ImapMailBox:
        return build_mailbox(config.inbox, os.environ, http, google_http)

    @provide
    def mailbox(self, imap: ImapMailBox) -> MailBox:
        return imap

    @provide
    async def llm(self, settings: Settings) -> AsyncIterator[LLMClient]:
        # Асинхронный HTTP-клиент провайдера закрывает тот, кто его создал, — контейнер.
        client = build_llm(settings)
        yield client
        await client.aclose()

    @provide
    def sink(self, config: InboxConfig) -> TableSink:
        return make_sink(config.sink)

    @provide
    def notifier(self, config: InboxConfig, http: httpx.AsyncClient) -> Notifier:
        return make_notifier(config.notify, http)

    @provide
    def events(self, config: InboxConfig, settings: Settings) -> EventLog:
        return EventLog(events_path(settings.data_dir, config.client))

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

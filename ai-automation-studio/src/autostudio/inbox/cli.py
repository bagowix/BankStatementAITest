"""Команды оператора: `autostudio inbox check | run | dry-run | demo | oauth-google`."""

import asyncio
import datetime as dt
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import cyclopts
import httpx
from dishka import make_async_container

from autostudio.clientconf import client_data_dir, load_client_config, load_sibling_env
from autostudio.clock import Clock, system_now
from autostudio.errors import ConfigError
from autostudio.inbox.config import ImapAuth, InboxConfig, InboxSection
from autostudio.inbox.demo import run_demo
from autostudio.inbox.errors import InboxError, MailboxError
from autostudio.inbox.oauth import GOOGLE_HINT, MICROSOFT_HINT, TIMEOUT, run_google_consent
from autostudio.inbox.pipeline import DryRow, InboxDeps, RunResult, dry_run, run_once
from autostudio.inbox.providers import InboxProvider, build_llm, build_mailbox
from autostudio.llm import LLMClient
from autostudio.providers import CoreProvider, OperatorAlerts
from autostudio.runlog import Heartbeat, RunSkipped, supervised_run
from autostudio.settings import LLMProvider, Settings

app = cyclopts.App(name='inbox', help='Разбор входящей почты: категории, таблица, черновики, горячие лиды.')

# Подсказки по провайдерам почты: ветвимся по адресу сервера и способу входа, а не по клиенту.
_GMAIL: Final = ('gmail', 'googlemail')
_MICROSOFT: Final = ('outlook', 'office365', 'hotmail')
_HINTS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (
        _GMAIL,
        'Gmail: личный @gmail.com — двухэтапная проверка и пароль приложения '
        '(myaccount.google.com/apppasswords), IMAP включён в настройках Gmail. Google Workspace: вход по '
        'паролю в IMAP отключён с 01.05.2025 — auth = "google_oauth" (Internal OAuth-клиент в GCP-проекте '
        'организации клиента и `autostudio inbox oauth-google`), см. docs/packages/inbox.md.',
    ),
    (
        ('yandex',),
        'Яндекс: в настройках почты включите доступ по IMAP «с помощью пароля приложения» и создайте '
        'пароль приложения (id.yandex.ru → Безопасность → Пароли приложений).',
    ),
    (
        ('zoho',),
        'Zoho: включите IMAP Access в настройках почты; при двухфакторной защите нужен Application-Specific Password.',
    ),
    (
        ('mail.ru',),
        'Mail.ru: создайте «пароль для внешнего приложения» в настройках безопасности.',
    ),
    (
        _MICROSOFT,
        'Microsoft 365: вход в IMAP по паролю отключён — auth = "microsoft_oauth": приложение Entra в тенанте '
        'клиента с разрешением IMAP.AccessAsApp, New-ServicePrincipal и Add-MailboxPermission на ящик '
        '(пошагово — docs/packages/inbox.md).',
    ),
)
_DEFAULT_HINT: Final = 'Проверьте, что IMAP включён, а в .env — логин и пароль приложения (не основной пароль).'
_AUTH_LABELS: Final[dict[ImapAuth, str]] = {
    'password': 'пароль приложения (LOGIN)',
    'google_oauth': 'OAuth Google Workspace (XOAUTH2, refresh token)',
    'microsoft_oauth': 'OAuth Microsoft 365 (XOAUTH2, приложение тенанта клиента)',
}
_ENV_HINT: Final = (
    'положите её в .env рядом с конфигом клиента (clients/<id>/.env подхватывается сам) '
    'или в окружение процесса (в Docker это делает env_file)'
)
_FAKE_WARNING: Final = '⚠ модель-заглушка (ключевые слова): для боевой работы задайте AUTOSTUDIO_LLM_PROVIDER и ключ'


def provider_hint(host: str, auth: ImapAuth = 'password') -> str:
    """Подсказка по настройке входа для сервера почты и способа входа."""
    low = host.lower()
    if auth == 'google_oauth':
        return f'Google OAuth: {GOOGLE_HINT}'
    if auth == 'microsoft_oauth':
        return f'Microsoft 365 OAuth: {MICROSOFT_HINT}'
    for needles, hint in _HINTS:
        if any(needle in low for needle in needles):
            return hint
    return _DEFAULT_HINT


def auth_problems(section: InboxSection) -> tuple[list[str], list[str]]:
    """Несовместимость способа входа и сервера: ошибки и предупреждения.

    Args:
        section: Секция `[inbox]`.

    Returns:
        Проблемы (запуск невозможен) и предупреждения (скорее всего, опечатка).
    """
    low = section.imap_host.lower()
    microsoft = any(needle in low for needle in _MICROSOFT)
    problems: list[str] = []
    warnings: list[str] = []
    if microsoft and section.auth == 'password':
        problems.append(provider_hint(section.imap_host))
    if section.auth == 'microsoft_oauth' and not microsoft:
        warnings.append(
            f'auth = "microsoft_oauth", но сервер {section.imap_host}: у Exchange Online это outlook.office365.com'
        )
    if section.auth == 'google_oauth' and not any(needle in low for needle in _GMAIL):
        warnings.append(f'auth = "google_oauth", но сервер {section.imap_host}: у Gmail и Workspace это imap.gmail.com')
    return problems, warnings


def _env_problems(config: InboxConfig) -> list[str]:
    problems: list[str] = []
    section = config.inbox
    for var in section.auth_env:
        if not os.environ.get(var):
            problems.append(f'переменная {var} не задана — {_ENV_HINT}')
    if config.sink.backend == 'google' and not os.environ.get(config.sink.credentials_env):
        problems.append(f'sink.backend = "google", но {config.sink.credentials_env} не задана')
    if config.notify.telegram_chat_id and not os.environ.get(config.notify.telegram_token_env):
        problems.append(f'notify.telegram_chat_id задан, но {config.notify.telegram_token_env} не задана')
    if config.notify.webhook_url_env and not os.environ.get(config.notify.webhook_url_env):
        problems.append(f'notify.webhook_url_env = {config.notify.webhook_url_env}, но переменная пуста')
    return problems


def _channel_report(config: InboxConfig, settings: Settings | None) -> tuple[list[str], list[str]]:
    """Куда уйдут сигналы владельцу и оператору — и предупреждения, если никуда."""
    notify = config.notify
    channels: list[str] = []
    if notify.telegram_chat_id:
        channels.append(f'telegram {notify.telegram_chat_id}')
    if notify.webhook_url_env:
        channels.append(f'вебхук ({notify.webhook_url_env})')
    console_only = not channels
    if notify.console or console_only:
        channels.append('консоль сервера')
    lines = [f'  сигналы владельцу: {", ".join(channels)}']
    warnings: list[str] = []
    if console_only and any(category.notify for category in config.inbox.categories):
        warnings.append(
            '⚠ сигналы о горячих лидах уйдут только в лог сервера: задайте [notify] telegram_chat_id '
            '(и AUTOSTUDIO_NOTIFY_BOT_TOKEN) или webhook_url_env — или уберите notify у категорий'
        )
    if settings is not None:
        token = os.environ.get(settings.operator_token_env, '')
        if settings.operator_chat_id and token:
            lines.append(f'  сигналы оператору: telegram {settings.operator_chat_id}')
        else:
            what = 'сбои, брошенные письма' + (
                ' и подозрительные письма' if config.inbox.alert_operator_on_injection else ''
            )
            warnings.append(
                f'⚠ канал оператора не настроен (AUTOSTUDIO_OPERATOR_CHAT_ID и {settings.operator_token_env}): '
                f'{what} будут видны только в логе'
            )
        lines.append(f'  данные клиента: {client_data_dir(settings.data_dir, config.client).resolve()}')
    return lines, warnings


async def _check_connection(config: InboxConfig) -> tuple[list[str], list[str]]:
    section = config.inbox
    try:
        with httpx.Client(timeout=TIMEOUT) as google_http:
            async with httpx.AsyncClient(timeout=TIMEOUT) as http:
                box = build_mailbox(section, os.environ, http, google_http)
                async with box:
                    folders = await box.folders()
                    state = await box.select(section.folder)
    except MailboxError as exc:
        raise ConfigError(f'{exc}\nподсказка: {provider_hint(section.imap_host, section.auth)}') from None
    names = {folder.name for folder in folders}
    lines = [
        f'✓ вход в {section.imap_host} выполнен, папок: {len(folders)}',
        f'✓ {section.folder}: писем {state.exists}, UIDVALIDITY {state.uidvalidity}',
    ]
    problems: list[str] = []
    if section.drafts_folder in names:
        lines.append(f'✓ папка черновиков: {section.drafts_folder}')
    else:
        marked = [folder.name for folder in folders if '\\drafts' in folder.flags]
        suggestion = (
            f' — сервер помечает черновики папкой «{marked[0]}»: inbox.drafts_folder = "{marked[0]}"' if marked else ''
        )
        problems.append(f'папки черновиков «{section.drafts_folder}» нет{suggestion}')
    for category in section.categories:
        if category.move_to and category.move_to not in names:
            problems.append(
                f'категория {category.id}: папки «{category.move_to}» нет — '
                'создайте её (в Gmail — ярлык) или уберите move_to'
            )
    return lines, problems


@app.command
def check(config: Path, *, connect: bool = False, skip_env: bool = False) -> None:
    """Проверяет конфиг и окружение без побочных эффектов; с --connect — вход в ящик и папки.

    Args:
        config: Конфиг клиента (TOML).
        connect: Войти по IMAP, перечислить папки и проверить папку черновиков (только чтение).
        skip_env: Проверить только файл конфига, без переменных окружения.
    """
    loaded = load_sibling_env(config)
    cfg = load_client_config(config, InboxConfig)
    section = cfg.inbox
    invoice_ids = section.invoice_category_ids
    invoices = (
        f'из PDF во вкладку «{section.invoices.sheet_tab}» для категорий {", ".join(invoice_ids)} '
        f'(до {section.invoices.max_pdf_kb} КБ и {section.invoices.max_pages} стр., без OCR)'
        if invoice_ids
        else 'не извлекаются'
    )
    lines = [
        f'✓ конфиг {config}: клиент {cfg.client.id} ({cfg.client.name}), пояс {cfg.client.timezone}, '
        f'язык {cfg.client.language}',
        f'  ящик: {section.imap_host}:{section.imap_port}, папка {section.folder}, черновики {section.drafts_folder}',
        f'  вход: {_AUTH_LABELS[section.auth]}',
        '  категории: '
        + ', '.join(
            f'{c.id}{" 🔔" if c.notify else ""}{" ✍" if c.draft_reply else ""}{f" →{c.move_to}" if c.move_to else ""}'
            for c in section.categories
        ),
        f'  факты для черновиков: {len(section.company.facts)}, язык ответа: {section.company.reply_language}',
        f'  таблица: {cfg.sink.backend}, вкладка «{section.sheet_tab}», маскирование: {section.mask_pii_in_sheet}',
        f'  счета: {invoices}',
        f'  телефоны: регион {section.phone_region}, запасные: {", ".join(section.phone_fallback_regions) or "—"}',
    ]
    if loaded:
        lines.append(f'  .env рядом с конфигом: загружено переменных {len(loaded)}')
    problems, auth_warnings = auth_problems(section)
    lines += [f'  ⚠ {warning}' for warning in auth_warnings]
    if not section.company.facts and any(c.draft_reply for c in section.categories):
        lines.append('  ⚠ фактов о компании нет: черновики будут только «спасибо, коллега свяжется»')
    settings: Settings | None = None
    if not skip_env:
        try:
            settings = Settings.from_env(os.environ)
        except ConfigError as exc:
            problems.append(str(exc))
        else:
            lines.append(f'  модель: {settings.llm_provider.value} / {settings.llm_model}')
            if settings.llm_provider is LLMProvider.FAKE:
                lines.append(f'  {_FAKE_WARNING}')
        problems += _env_problems(cfg)
    channel_lines, warnings = _channel_report(cfg, settings)
    lines += channel_lines + [f'  {warning}' for warning in warnings]
    if connect and not problems:
        connected, connect_problems = asyncio.run(_check_connection(cfg))
        lines += connected
        problems += connect_problems
    for line in lines:
        print(line)
    if problems:
        for problem in problems:
            print(f'✗ {problem}')
        raise ConfigError(f'проверка не пройдена: проблем {len(problems)}')
    print('✓ готово к запуску' if connect else '✓ конфиг в порядке (вход в ящик: --connect)')


def format_result(result: RunResult) -> str:
    """Сводка запуска для консоли и журнала cron."""
    categories = ', '.join(f'{name}: {count}' for name, count in sorted(result.by_category.items())) or '—'
    lines = [
        f'inbox: новых {result.processed} (скачано {result.fetched}, уже было {result.skipped}), '
        f'ошибок {result.failed}, брошено {result.given_up}',
        f'  категории: {categories}',
        f'  черновиков {result.drafts}, лидов {result.leads}, уведомлений {result.notified}, '
        f'инъекций {result.injections}, вызовов модели {result.llm_calls}, строк в таблицу {result.sheet_rows}',
    ]
    if result.invoices_verified or result.invoices_unverified or result.invoice_rows:
        lines.append(
            f'  счета: сверено {result.invoices_verified}, на ручную проверку {result.invoices_unverified}, '
            f'строк во вкладку счетов {result.invoice_rows}'
        )
    if result.aborted:
        lines.append(f'  ✗ остановлено: {result.aborted}')
    lines += [f'  ✗ {error}' for error in result.errors]
    return '\n'.join(lines)


def require_model(settings: Settings, environ: Mapping[str, str]) -> None:
    """Не даёт боевому разбору молча работать на заглушке.

    Пустой AUTOSTUDIO_LLM_PROVIDER в ядре значит `fake`: забытый ключ превратил бы разбор
    почты клиента в сортировку по ключевым словам и шаблонные черновики. Заглушка в боевом
    запуске — только если она выбрана явно (для проверки развёртывания).

    Args:
        settings: Настройки процесса.
        environ: Окружение процесса.

    Raises:
        ConfigError: Провайдер модели не задан.
    """
    if settings.llm_provider is not LLMProvider.FAKE:
        return
    if environ.get('AUTOSTUDIO_LLM_PROVIDER', '').strip().lower() != LLMProvider.FAKE.value:
        raise ConfigError(
            'AUTOSTUDIO_LLM_PROVIDER не задан: боевой разбор не запускается на заглушке. '
            'Задайте openai|anthropic и ключ (или явно fake — только для проверки развёртывания)'
        )
    print(_FAKE_WARNING, flush=True)


async def _serve(config: InboxConfig, settings: Settings, every_minutes: int | None) -> None:
    container = make_async_container(CoreProvider(), InboxProvider(), context={Settings: settings, InboxConfig: config})
    try:
        deps = await container.get(InboxDeps)
        heartbeat = await container.get(Heartbeat)
        operator = await container.get(OperatorAlerts)
        clock = await container.get(Clock)

        skipped: list[RunSkipped] = []

        async def job() -> RunResult:
            try:
                result = await run_once(config, deps, clock())
            except RunSkipped as exc:
                # Пропуск — не успех и не сбой: supervised_run пишет его отдельно и оператора не тревожит.
                skipped.append(exc)
                print(f'inbox: {exc} — пропуск', flush=True)
                raise
            print(format_result(result), flush=True)
            if not result.ok:
                raise InboxError(result.aborted or '; '.join(result.errors))
            return result

        while True:
            skipped.clear()
            outcome = await supervised_run(
                package='inbox',
                job=job,
                events=deps.events,
                heartbeat=heartbeat,
                operator=operator.notifier,
                client_name=config.client.name,
                now=clock,
            )
            if every_minutes is None:
                if outcome is None and not skipped:
                    raise InboxError('запуск завершился ошибкой — подробности выше, оператор уведомлён')
                return
            await asyncio.sleep(every_minutes * 60)
    finally:
        await container.close()


@app.command
def run(config: Path, *, every_minutes: int | None = None) -> None:
    """Боевой разбор почты: один запуск (для cron/systemd timer) или цикл.

    Args:
        config: Конфиг клиента (TOML).
        every_minutes: Повторять каждые N минут, не выходя (вместо cron).
    """
    if every_minutes is not None and every_minutes < 1:
        raise ConfigError('--every-minutes: не меньше 1')
    load_sibling_env(config)
    cfg = load_client_config(config, InboxConfig)
    settings = Settings.from_env(os.environ)
    require_model(settings, os.environ)
    try:
        asyncio.run(_serve(cfg, settings, every_minutes))
    except KeyboardInterrupt:
        print('inbox: остановлено')


_VIA: Final[dict[str, str]] = {'prefilter': 'rules', 'error': 'ERROR'}


def format_dry_rows(rows: list[DryRow], zone: dt.tzinfo, model_label: str = 'AI') -> str:
    """Таблица пробного разбора для консоли.

    Args:
        rows: Строки пробного разбора.
        zone: Часовой пояс клиента.
        model_label: Как подписать разбор моделью (`stub` для заглушки — показ не должен
            выдавать ключевые слова за ИИ).

    Returns:
        Таблица текстом.
    """
    header = ['Received', 'From', 'Subject', 'Category', 'Urgency', 'Conf', 'Via', 'Would do', 'Notes']
    table = [header] + [
        [
            row.date.astimezone(zone).strftime('%m-%d %H:%M'),
            row.sender[:28],
            row.subject[:40],
            row.category,
            row.urgency,
            f'{row.confidence:.2f}',
            _VIA.get(row.source, model_label),
            ', '.join(row.would) or '—',
            ', '.join(row.notes),
        ]
        for row in rows
    ]
    widths = [max(len(line[index]) for line in table) for index in range(len(header))]
    return '\n'.join(
        '  '.join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip() for line in table
    )


@app.command(name='dry-run')
def dry_run_command(config: Path, *, limit: int = 20) -> None:
    """Классифицирует последние письма БЕЗ действий — показать клиенту на его ящике (с согласия).

    Ни черновиков, ни таблицы, ни журнала, письма не помечаются прочитанными.

    Args:
        config: Конфиг клиента (TOML).
        limit: Сколько последних писем (за 14 дней) разобрать.
    """
    if not 1 <= limit <= 200:
        raise ConfigError('--limit: от 1 до 200')
    load_sibling_env(config)
    cfg = load_client_config(config, InboxConfig)
    settings = Settings.from_env(os.environ)
    stub = settings.llm_provider is LLMProvider.FAKE
    if stub:
        print(_FAKE_WARNING)
    llm = build_llm(settings)
    rows = asyncio.run(_dry(cfg, llm, limit))
    print(format_dry_rows(rows, cfg.client.zone, model_label='stub' if stub else 'AI'))
    by_rules = sum(row.source == 'prefilter' for row in rows)
    errors = sum(row.source == 'error' for row in rows)
    print(
        f'\nписем: {len(rows)}, из них правилами без модели: {by_rules}, с ошибкой: {errors}, '
        f'вызовов модели: {llm.usage.calls}'
    )


def _progress(done: int, total: int) -> None:
    # Прогресс — в stderr: на встрече видно, что разбор идёт, а таблицу в stdout можно сохранить.
    print(f'\r  разобрано {done}/{total}…', end='\n' if done == total else '', file=sys.stderr, flush=True)


async def _dry(config: InboxConfig, llm: LLMClient, limit: int) -> list[DryRow]:
    try:
        with httpx.Client(timeout=TIMEOUT) as google_http:
            async with httpx.AsyncClient(timeout=TIMEOUT) as http:
                box = build_mailbox(config.inbox, os.environ, http, google_http)
                return await dry_run(config, box, llm, system_now(), limit, progress=_progress)
    finally:
        await llm.aclose()


@app.command
def demo(*, out: Path = Path('var/demo/inbox')) -> None:
    """Офлайн-демо на фикстурах examples/inbox/emails: без сети, ключей и настоящего ящика.

    Args:
        out: Каталог для таблицы (CSV), черновиков (.eml) и состояния демо.
    """
    print(asyncio.run(run_demo(out)))


@app.command(name='oauth-google')
def oauth_google(*, client_secrets: Path, config: Path | None = None, port: int = 0, no_browser: bool = False) -> None:
    """Разовое согласие Google Workspace: выдаёт refresh token для IMAP (XOAUTH2).

    Запускать на компьютере с браузером (не на сервере): откроется окно входа Google, войдите
    в ящик КЛИЕНТА. OAuth-клиент «Desktop app» создаётся в GCP-проекте организации клиента
    как Internal-приложение — не в проекте оператора (почему — docs/packages/inbox.md).

    Args:
        client_secrets: JSON OAuth-клиента «Desktop app» (Google Cloud Console → Credentials).
        config: Конфиг клиента — взять из него имена переменных (по умолчанию INBOX_OAUTH_*).
        port: Порт петлевого адреса localhost; 0 — любой свободный.
        no_browser: Не открывать браузер, а только напечатать ссылку.
    """
    section = load_client_config(config, InboxConfig).inbox if config is not None else None
    consent = run_google_consent(client_secrets, port=port, open_browser=not no_browser)
    names = (
        (section.oauth_client_id_env, section.oauth_client_secret_env, section.oauth_refresh_token_env)
        if section is not None
        else ('INBOX_OAUTH_CLIENT_ID', 'INBOX_OAUTH_CLIENT_SECRET', 'INBOX_OAUTH_REFRESH_TOKEN')
    )
    values = (consent.client_id, consent.client_secret, consent.refresh_token)
    # Токен печатается один раз и больше нигде не хранится: ни в файле, ни в логе.
    print('✓ согласие получено. Добавьте эти строки в clients/<id>/.env клиента (и в менеджер паролей):\n')
    print('\n'.join(f'{name}={value}' for name, value in zip(names, values, strict=True)))
    print(
        '\nв конфиге клиента: [inbox] auth = "google_oauth", imap_host = "imap.gmail.com".\n'
        'Проверка: autostudio inbox check clients/<id>/inbox.toml --connect\n'
        'Очистите историю терминала: refresh token даёт полный доступ к ящику.'
    )

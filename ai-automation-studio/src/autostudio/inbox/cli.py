"""Команды оператора: `autostudio inbox check | run | dry-run | demo`."""

import asyncio
import datetime as dt
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import cyclopts
from dishka import make_async_container

from autostudio.clientconf import client_data_dir, load_client_config, require_env
from autostudio.clock import Clock, system_now
from autostudio.errors import ConfigError
from autostudio.inbox.config import InboxConfig
from autostudio.inbox.demo import run_demo
from autostudio.inbox.errors import InboxError, MailboxError
from autostudio.inbox.imap import ImapMailBox
from autostudio.inbox.pipeline import DryRow, InboxDeps, RunResult, dry_run, run_once
from autostudio.inbox.providers import InboxProvider, build_llm
from autostudio.llm import LLMClient
from autostudio.providers import CoreProvider, OperatorAlerts
from autostudio.runlog import Heartbeat, supervised_run
from autostudio.settings import LLMProvider, Settings

app = cyclopts.App(name='inbox', help='Разбор входящей почты: категории, таблица, черновики, горячие лиды.')

# Подсказки по провайдерам почты: ветвимся по адресу сервера, а не по клиенту.
_HINTS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (
        ('gmail', 'googlemail'),
        'Gmail / Google Workspace: включите двухэтапную проверку и создайте пароль приложения '
        '(myaccount.google.com/apppasswords), IMAP должен быть включён в настройках Gmail. '
        'Если администратор Workspace запретил пароли приложений — см. docs/packages/inbox.md, «Альтернативы».',
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
        ('outlook', 'office365', 'hotmail'),
        'Outlook / Microsoft 365: Microsoft отключила вход по паролю для IMAP — v1 это не поддерживает. '
        'Настройте автопересылку на отдельный ящик Gmail или Zoho и подключите его.',
    ),
)
_DEFAULT_HINT: Final = 'Проверьте, что IMAP включён, а в .env — логин и пароль приложения (не основной пароль).'
_UNSUPPORTED_HOSTS: Final = ('outlook', 'office365', 'hotmail')
_ENV_HINT: Final = (
    'переменные берутся из окружения процесса: загрузите .env клиента — '
    '`uv run --env-file clients/<id>/.env autostudio inbox …` (в Docker это делает env_file)'
)
_FAKE_WARNING: Final = '⚠ модель-заглушка (ключевые слова): для боевой работы задайте AUTOSTUDIO_LLM_PROVIDER и ключ'


def provider_hint(host: str) -> str:
    """Подсказка по настройке IMAP для сервера почты."""
    low = host.lower()
    for needles, hint in _HINTS:
        if any(needle in low for needle in needles):
            return hint
    return _DEFAULT_HINT


def _env_problems(config: InboxConfig) -> list[str]:
    problems: list[str] = []
    section = config.inbox
    for var in (section.username_env, section.password_env):
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
    box = ImapMailBox(
        host=section.imap_host,
        port=section.imap_port,
        user=require_env(section.username_env, os.environ),
        password=require_env(section.password_env, os.environ),
        timeout=section.timeout_seconds,
    )
    try:
        async with box:
            folders = await box.folders()
            state = await box.select(section.folder)
    except MailboxError as exc:
        raise ConfigError(f'{exc}\nподсказка: {provider_hint(section.imap_host)}') from None
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
    cfg = load_client_config(config, InboxConfig)
    section = cfg.inbox
    lines = [
        f'✓ конфиг {config}: клиент {cfg.client.id} ({cfg.client.name}), пояс {cfg.client.timezone}, '
        f'язык {cfg.client.language}',
        f'  ящик: {section.imap_host}:{section.imap_port}, папка {section.folder}, черновики {section.drafts_folder}',
        '  категории: '
        + ', '.join(
            f'{c.id}{" 🔔" if c.notify else ""}{" ✍" if c.draft_reply else ""}{f" →{c.move_to}" if c.move_to else ""}'
            for c in section.categories
        ),
        f'  факты для черновиков: {len(section.company.facts)}, язык ответа: {section.company.reply_language}',
        f'  таблица: {cfg.sink.backend}, вкладка «{section.sheet_tab}», маскирование: {section.mask_pii_in_sheet}',
    ]
    problems: list[str] = []
    if any(host in section.imap_host.lower() for host in _UNSUPPORTED_HOSTS):
        problems.append(provider_hint(section.imap_host))
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
    if result.locked:
        return 'inbox: предыдущий запуск ещё идёт — пропуск'
    categories = ', '.join(f'{name}: {count}' for name, count in sorted(result.by_category.items())) or '—'
    lines = [
        f'inbox: новых {result.processed} (скачано {result.fetched}, уже было {result.skipped}), '
        f'ошибок {result.failed}, брошено {result.given_up}',
        f'  категории: {categories}',
        f'  черновиков {result.drafts}, лидов {result.leads}, уведомлений {result.notified}, '
        f'инъекций {result.injections}, вызовов модели {result.llm_calls}, строк в таблицу {result.sheet_rows}',
    ]
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

        async def job() -> RunResult:
            result = await run_once(config, deps, clock())
            print(format_result(result), flush=True)
            if not result.ok:
                raise InboxError(result.aborted or '; '.join(result.errors))
            return result

        while True:
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
                if outcome is None:
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
    cfg = load_client_config(config, InboxConfig)
    settings = Settings.from_env(os.environ)
    stub = settings.llm_provider is LLMProvider.FAKE
    if stub:
        print(_FAKE_WARNING)
    section = cfg.inbox
    box = ImapMailBox(
        host=section.imap_host,
        port=section.imap_port,
        user=require_env(section.username_env, os.environ),
        password=require_env(section.password_env, os.environ),
        timeout=section.timeout_seconds,
    )
    llm = build_llm(settings)
    rows = asyncio.run(_dry(cfg, box, llm, limit))
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


async def _dry(config: InboxConfig, box: ImapMailBox, llm: LLMClient, limit: int) -> list[DryRow]:
    try:
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

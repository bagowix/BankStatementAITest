"""Команды пакета: `autostudio booking check | run | chat | agenda | demo`.

Команды оператора — на русском, как и в ядре; тексты бота для покупателей — в `texts`.

Команды, которые ходят в сеть (`check --online`, `run`, `chat`), глушат INFO-лог httpx:
он пишет полный URL каждого запроса, а в URL Bot API — токен бота (свой и общий бот
оператора), в URL вебхука `[notify]` — его секрет. Корневой `main()` делает это для всего
процесса (`autostudio.cli.quiet_http_loggers`); пакет повторяет это на время своей команды,
потому что её вызывают и в обход `main()` (тесты, встраивание), и возвращает уровни назад.

`check`, `run`, `chat` и `agenda` сначала подхватывают `clients/<id>/.env` рядом с конфигом
(`load_sibling_env`): вне Docker секреты клиента не надо экспортировать руками.
"""

import asyncio
import contextlib
import csv
import datetime as dt
import logging
import os
import re
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Final

import cyclopts
import httpx
import msgspec
from aiogram import Bot
from dishka import AsyncContainer, make_async_container

from autostudio.booking.admin import render_agenda
from autostudio.booking.config import BookingConfig, load_booking_config
from autostudio.booking.console import repl
from autostudio.booking.demo import run_demo
from autostudio.booking.domain import day_bounds, local_today, next_free_slots, working_intervals
from autostudio.booking.engine import Engine
from autostudio.booking.gcal import CalendarApi, CalendarError, GoogleCalendar, service_account_email
from autostudio.booking.providers import (
    STORE_FILE,
    BookingProvider,
    ChannelProvider,
    WhatsAppEndpoint,
    csv_dir_problem,
    resolve_sink,
)
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.server import TELEGRAM_PATH, WHATSAPP_PATH, serve
from autostudio.booking.service import BOOKINGS_TAB, BookingService
from autostudio.booking.store import BookingStore
from autostudio.booking.telegram import BookingBot
from autostudio.booking.texts import BOOKINGS_HEADER, Texts
from autostudio.booking.whatsapp import GRAPH_API
from autostudio.cli import quiet_http_loggers
from autostudio.clientconf import NotifyConfig, SinkConfig, client_data_dir, load_sibling_env, require_env
from autostudio.clock import system_now
from autostudio.errors import ConfigError
from autostudio.providers import CoreProvider
from autostudio.settings import Settings
from autostudio.sinks import CsvSink

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name='booking',
    help='Бот записи (Telegram, WhatsApp, Google Calendar): проверка, запуск, консольный чат, расписание, демо.',
)

_TOKEN_SHAPE: Final = re.compile(r'^\d{5,}:[A-Za-z0-9_-]{30,}$')
# Секрет вебхука Telegram: 1–256 символов A-Z, a-z, 0-9, `_` и `-` (Bot API, setWebhook).
_WEBHOOK_SECRET: Final = re.compile(r'^[A-Za-z0-9_-]{1,256}$')
_PREVIEW_SLOTS: Final = 3
# Переменные ключей моделей (как в `Settings.from_env`): при `--offline` их отсутствие не ошибка.
_MODEL_KEY_VARS: Final = ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY')
_OFFLINE_KEY: Final = 'offline-check'  # заглушка вместо ключа модели, а не секрет
_TELEGRAM_API: Final = 'https://api.telegram.org'
_PROBE_TIMEOUT_S: Final = 15.0
_DRAIN_TIMEOUT_S: Final = 30.0
# Логгеры, уровни которых команда возвращает после себя (ядро может глушить и другие — те
# остаются на WARNING, это безопасная сторона).
TRANSPORT_LOGGERS: Final = ('httpx', 'httpcore', 'hpack')


@contextlib.contextmanager
def quiet_transport_logs() -> Generator[None]:
    """Глушит HTTP-логгеры ядром (`quiet_http_loggers`) на время команды и возвращает уровни.

    Уровень логгера, а не фильтр обработчика: запись с URL даже не создаётся, при любом
    `AUTOSTUDIO_LOG_LEVEL` (и DEBUG тоже). Уровень, заданный выше WARNING, не понижается.
    """
    loggers = [logging.getLogger(name) for name in TRANSPORT_LOGGERS]
    saved = [item.level for item in loggers]
    quiet_http_loggers()
    for item, level in zip(loggers, saved, strict=True):
        item.setLevel(max(level, logging.WARNING))
    try:
        yield
    finally:
        for item, level in zip(loggers, saved, strict=True):
            item.setLevel(level)


def _container(settings: Settings, config: BookingConfig, *, channels: bool) -> AsyncContainer:
    providers = [CoreProvider(), BookingProvider(), *([ChannelProvider()] if channels else [])]
    return make_async_container(*providers, context={Settings: settings, BookingConfig: config})


def _readonly_store(settings: Settings, config: BookingConfig) -> BookingStore:
    return BookingStore(client_data_dir(settings.data_dir, config.client) / STORE_FILE, readonly=True)


def require_owner_channel(config: BookingConfig) -> None:
    """Уведомления о записях и вопросы покупателей должны до кого-то доходить.

    Args:
        config: Конфиг клиента.

    Raises:
        ConfigError: Нет ни `admin_chat_ids`, ни настоящего канала `[notify]`.
    """
    if not config.has_owner_channel:
        raise ConfigError(
            'admin_chat_ids пуст, а в [notify] нет telegram_chat_id или webhook_url_env: о записях и вопросах '
            'покупателей никто не узнает, хотя бот обещает «вам ответят здесь». Впишите id владельца в admin_chat_ids'
        )


def check_environment(config: BookingConfig, environ: dict[str, str]) -> list[str]:
    """Проверяет секреты, нужные боевому запуску (значения не печатаются).

    Args:
        config: Конфиг клиента.
        environ: Окружение.

    Returns:
        Строки отчёта.

    Raises:
        ConfigError: Чего-то не хватает или токен не похож на токен бота.
    """
    token = require_env(config.booking.bot_token_env, environ)
    if not _TOKEN_SHAPE.match(token):
        raise ConfigError(f'{config.booking.bot_token_env}: значение не похоже на токен от @BotFather')
    lines = [f'токен бота: {config.booking.bot_token_env} задан']
    if config.sink.backend == 'google':
        credentials = Path(require_env(config.sink.credentials_env, environ))
        if not credentials.is_file():
            raise ConfigError(f'{config.sink.credentials_env}: файл ключа сервисного аккаунта не найден')
        lines.append(f'Google Sheets: ключ {config.sink.credentials_env} на месте')
    if config.notify.telegram_chat_id:
        require_env(config.notify.telegram_token_env, environ)
    if config.notify.webhook_url_env:
        require_env(config.notify.webhook_url_env, environ)
    section = config.booking
    if section.telegram.mode == 'webhook':
        secret = require_env(section.telegram.webhook_secret_env, environ)
        if not _WEBHOOK_SECRET.match(secret):
            raise ConfigError(f'{section.telegram.webhook_secret_env}: 1–256 символов A-Z, a-z, 0-9, _ и -')
        lines.append(f'Telegram: вебхук {section.server.base_url}{TELEGRAM_PATH}, секрет задан')
    if section.calendar is not None:
        key = Path(require_env(section.calendar.credentials_env, environ))
        if not key.is_file():
            raise ConfigError(f'{section.calendar.credentials_env}: файл ключа сервисного аккаунта не найден')
        email = service_account_email(key) or '(e-mail не прочитан из ключа)'
        lines.append(f'Google Calendar: ключ на месте; клиент делится календарями с {email} (право «Изменять события»)')
    if section.whatsapp is not None:
        wa = section.whatsapp
        for name in (wa.token_env, wa.app_secret_env, wa.verify_token_env):
            require_env(name, environ)
        where = (
            f'{section.server.base_url}{WHATSAPP_PATH}' if section.server.base_url else f'<https-домен>{WHATSAPP_PATH}'
        )
        lines.append(f'WhatsApp: токен, App Secret и verify token заданы; Callback URL в кабинете Meta — {where}')
    return lines


def describe(config: BookingConfig, settings: Settings, now: dt.datetime) -> list[str]:
    """Сводка конфига и ближайшие свободные слоты по каждой услуге.

    Args:
        config: Конфиг клиента.
        settings: Настройки процесса.
        now: Текущий момент.

    Returns:
        Строки для терминала.
    """
    section = config.booking
    texts = Texts(section, config.client.zone)
    lang = config.owner_language
    bookings = _readonly_store(settings, config).overlapping(
        now - dt.timedelta(days=1), now + dt.timedelta(days=section.horizon_days + 1)
    )
    sink = resolve_sink(config.sink, settings, config)
    lines = [
        f'клиент: {config.client.name} ({config.client.id}), {config.client.timezone}; языки покупателей: '
        f'{", ".join(section.languages)}',
        f'услуг: {len(section.services)}, мастеров: {len(section.staff)}, FAQ: {len(section.faq)}, '
        f'администраторов: {len(section.admin_chat_ids)}',
        f'модель: {settings.llm_provider.value} ({settings.llm_model})',
        f'таблица: {sink.backend}' + (f' → {sink.csv_dir}' if sink.backend == 'csv' else ''),
        *_channel_lines(config),
        f'активных записей на горизонте: {len(bookings)}',
        'ближайшие свободные слоты (без занятости из календаря):' if section.calendar else 'ближайшие свободные слоты:',
    ]
    for service in section.services:
        slots = next_free_slots(config, service.id, bookings, now, limit=_PREVIEW_SLOTS)
        shown = ', '.join(f'{texts.when(slot.start, lang)} ({texts.staff_name(slot.staff_id)})' for slot in slots)
        lines.append(f'  {texts.pick(service.name, lang)}: {shown or "нет свободного времени на горизонте"}')
    warnings = [*_schedule_warnings(config)]
    if not section.admin_chat_ids:
        warnings.append('admin_chat_ids пуст: уведомления о записях получит только канал [notify]')
    if config.notify.console:
        warnings.append('[notify] console = true: в боевом run уведомления в консоль не пишутся (только demo и chat)')
    problem = csv_dir_problem(sink.csv_dir) if sink.backend == 'csv' else None
    if problem is not None:
        warnings.append(f'CSV-выгрузка не сможет писать: {problem} (в контейнере писать можно только в /data)')
    stale = stale_bookings_header(sink.csv_dir, BOOKINGS_HEADER[lang]) if sink.backend == 'csv' else None
    if stale is not None:
        warnings.append(stale)
    if not section.faq:
        warnings.append('FAQ пуст: все вопросы будут передаваться человеку')
    if not section.contact_phone:
        warnings.append('contact_phone не задан: покупателю некуда позвонить, если бот не помог')
    if settings.llm_provider.value == 'fake':
        warnings.append('модель fake: свободный текст разбирается простыми правилами (тариф Basic)')
    warnings += _channel_warnings(config)
    return lines + [f'внимание: {item}' for item in warnings]


def stale_bookings_header(csv_dir: Path, header: Sequence[str]) -> str | None:
    """Предупреждение, если `Bookings.csv` начат с другим заголовком (прежняя версия пакета или язык).

    Таблица пишет заголовок только в новую вкладку, а строки дописывает в конец: после смены
    колонок (11 → 13: валюта отдельно, канал) строки нового формата съехали бы под чужие заголовки.

    Args:
        csv_dir: Каталог CSV-выгрузок.
        header: Заголовок, с которым пишет текущая версия.

    Returns:
        Текст предупреждения или None (файла нет, он пуст или заголовок совпадает).
    """
    path = CsvSink(csv_dir).path_for(BOOKINGS_TAB)
    try:
        with path.open(encoding='utf-8', newline='') as fh:
            first = next(csv.reader(fh), None)
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    if first is None or first == list(header):
        return None
    return (
        f'{path}: заголовок от прежней версии или другого языка ({len(first)} колонок, сейчас {len(header)}) — '
        'переименуйте файл в архивный до запуска, иначе новые строки съедут под чужие колонки'
    )


def _channel_lines(config: BookingConfig) -> list[str]:
    section = config.booking
    lines = [f'Telegram: {"вебхук" if section.telegram.mode == "webhook" else "long polling"}']
    if section.calendar is not None:
        calendar = section.calendar
        mapped = {member.id: calendar.calendar_for(member.id) for member in section.staff}
        lines.append(
            f'Google Calendar: календарей {len(calendar.calendars)}, кеш {calendar.cache_ttl_s} с, '
            f'события записей: {"пишутся" if calendar.write_events else "не пишутся"}; '
            + ', '.join(f'{staff} → {cal or "нет"}' for staff, cal in mapped.items())
        )
    if section.whatsapp is not None:
        wa = section.whatsapp
        template = wa.reminder_template or 'не задан'
        lines.append(
            f'WhatsApp: номер {wa.phone_number_id}, Graph API {wa.graph_version}, шаблон напоминания {template}'
        )
    return lines


def _channel_warnings(config: BookingConfig) -> list[str]:
    section = config.booking
    warnings: list[str] = []
    if section.calendar is not None:
        idle = [member.id for member in section.staff if section.calendar.calendar_for(member.id) is None]
        if idle:
            warnings.append(f'у мастеров {", ".join(idle)} нет календаря: их занятость — только по базе бота')
    if section.whatsapp is not None:
        if section.whatsapp.reminder_template is None:
            warnings.append(
                'WhatsApp без reminder_template: напоминание уйдёт только тем, кто писал боту за последние 24 часа'
            )
        if section.server.base_url is None:
            warnings.append(
                '[booking.server] public_url не задан: Callback URL для Meta составьте из своего HTTPS-домена'
            )
    return warnings


def _schedule_warnings(config: BookingConfig) -> list[str]:
    section = config.booking
    idle = {
        member.id
        for member in section.staff
        if not any(working_intervals(section, member, weekday) for weekday in range(7))
    }
    warnings = [
        f'мастер {member.name} ({member.id}) не работает ни в один день: его часы не пересекаются с часами '
        'салона — к нему нельзя записаться'
        for member in section.staff
        if member.id in idle
    ]
    for service in section.services:
        if all(member.id in idle for member in section.eligible_staff(service)):
            warnings.append(f'услуга {service.id}: никто из её мастеров не работает — записаться на неё нельзя')
    return warnings


class _TelegramReply(msgspec.Struct):
    ok: bool
    description: str = ''


class _BotUser(msgspec.Struct):
    username: str = ''


class _GetMe(msgspec.Struct):
    ok: bool
    result: _BotUser | None = None
    description: str = ''


async def probe_telegram(http: httpx.AsyncClient, token: str, admin_chat_ids: Sequence[int]) -> list[str]:
    """Спрашивает Telegram, жив ли токен и видит ли бот чаты администраторов.

    Telegram не даёт боту написать первым: администратор, ни разу не нажавший `/start`,
    не получит ни одного уведомления. `getChat` для такого чата отвечает «chat not found».

    Args:
        http: httpx-клиент.
        token: Токен бота.
        admin_chat_ids: Чаты администраторов.

    Returns:
        Строки отчёта.

    Raises:
        ConfigError: Токен отозван, Telegram недоступен или бот не видит чат администратора.
    """
    base = f'{_TELEGRAM_API}/bot{token}'
    try:
        response = await http.get(f'{base}/getMe')
        me = msgspec.json.decode(response.content, type=_GetMe)
        if not me.ok or me.result is None:
            raise ConfigError(f'Telegram не принял токен: {me.description or response.status_code}')
        username = me.result.username
        lines = [
            f'Telegram: бот @{username} отвечает',
            f'ссылки с меткой источника (видны в отчёте): https://t.me/{username}?start=site, ?start=instagram, …',
        ]
        missing: list[str] = []
        for chat_id in admin_chat_ids:
            answer = await http.get(f'{base}/getChat', params={'chat_id': chat_id})
            if not msgspec.json.decode(answer.content, type=_TelegramReply).ok:
                missing.append(str(chat_id))
    except httpx.HTTPError as exc:
        # В тексте исключения httpx есть URL, а в URL — токен: показываем только тип.
        raise ConfigError(f'Telegram недоступен: {type(exc).__name__}') from None
    except msgspec.DecodeError:
        raise ConfigError('Telegram ответил не JSON — проверьте сеть и прокси') from None
    if missing:
        raise ConfigError(
            f'бот не видит чаты администраторов {", ".join(missing)}: каждый должен нажать /start в боте '
            '(для группы — добавить бота в неё); иначе уведомления не дойдут'
        )
    lines.append(f'Telegram: чаты администраторов доступны ({len(admin_chat_ids)})')
    return lines


class _PhoneNumber(msgspec.Struct):
    display_phone_number: str = ''
    verified_name: str = ''
    quality_rating: str = ''


async def probe_whatsapp(http: httpx.AsyncClient, config: BookingConfig, token: str) -> list[str]:
    """Спрашивает Graph API, виден ли номер WhatsApp с этим токеном.

    Args:
        http: httpx-клиент.
        config: Конфиг клиента (секция `[booking.whatsapp]`).
        token: Токен system user.

    Returns:
        Строки отчёта (пусто, если WhatsApp не настроен).

    Raises:
        ConfigError: Токен не подходит, номер не найден или Graph API недоступен.
    """
    section = config.booking.whatsapp
    if section is None:
        return []
    url = f'{GRAPH_API}/{section.graph_version}/{section.phone_number_id}'
    params = {'fields': 'display_phone_number,verified_name,quality_rating'}
    try:
        response = await http.get(url, params=params, headers={'Authorization': f'Bearer {token}'})
    except httpx.HTTPError as exc:
        raise ConfigError(f'WhatsApp Graph API недоступен: {type(exc).__name__}') from None
    if response.is_error:
        raise ConfigError(
            f'WhatsApp: номер {section.phone_number_id} не виден с этим токеном (HTTP {response.status_code}) — '
            'проверьте токен system user и что номер в WABA клиента'
        )
    try:
        number = msgspec.json.decode(response.content, type=_PhoneNumber)
    except msgspec.DecodeError:
        raise ConfigError('WhatsApp ответил не JSON — проверьте сеть и прокси') from None
    quality = number.quality_rating or '—'
    return [f'WhatsApp: номер {number.display_phone_number} ({number.verified_name}), качество {quality}']


async def probe_calendar(api: CalendarApi, config: BookingConfig, now: dt.datetime) -> list[str]:
    """Проверяет, читаются ли календари клиента (freeBusy на сутки вперёд).

    Args:
        api: Календарь.
        config: Конфиг клиента.
        now: Текущий момент.

    Returns:
        Строки отчёта (пусто без `[booking.calendar]`).

    Raises:
        ConfigError: Google недоступен, ключ не подходит или календарь не расшарен.
    """
    section = config.booking.calendar
    if section is None:
        return []
    try:
        answer = await api.free_busy(section.calendars, now, now + dt.timedelta(days=1))
    except CalendarError as exc:
        raise ConfigError(f'Google Calendar: {exc}') from None
    if answer.errors:
        problems = ', '.join(f'{calendar} ({reason})' for calendar, reason in sorted(answer.errors.items()))
        raise ConfigError(
            f'календари не читаются: {problems}. notFound — календарь не расшарен на сервисный аккаунт '
            '(«Настройки и общий доступ» → «Поделиться» → e-mail сервисного аккаунта, «Изменять события»)'
        )
    return [f'Google Calendar: календари читаются ({len(section.calendars)})']


async def _probe(config: BookingConfig, environ: dict[str, str], now: dt.datetime) -> list[str]:
    token = require_env(config.booking.bot_token_env, environ)
    async with httpx.AsyncClient(timeout=httpx.Timeout(_PROBE_TIMEOUT_S)) as http:
        lines = await probe_telegram(http, token, config.booking.admin_chat_ids)
        if config.booking.whatsapp is not None:
            lines += await probe_whatsapp(http, config, require_env(config.booking.whatsapp.token_env, environ))
        if config.booking.calendar is not None:
            from autostudio.booking.gauth import ServiceAccountTokens

            key = Path(require_env(config.booking.calendar.credentials_env, environ))
            lines += await probe_calendar(GoogleCalendar(http, ServiceAccountTokens(key)), config, now)
    return lines


def _check_settings(*, offline: bool) -> Settings:
    environ = dict(os.environ)
    if offline:
        # До получения секретов конфиг всё равно надо проверить: отсутствие ключа модели — не ошибка.
        for name in _MODEL_KEY_VARS:
            environ[name] = environ.get(name) or _OFFLINE_KEY
    return Settings.from_env(environ)


@app.command
def check(config: Path, *, offline: bool = False, online: bool = False) -> None:
    """Проверяет конфиг и окружение без побочных эффектов и показывает ближайшие слоты.

    Args:
        config: Конфиг клиента (TOML).
        offline: Не проверять токен бота и ключи (для проверки конфига до получения секретов).
        online: Ещё и спросить Telegram: жив ли токен и нажал ли каждый администратор /start.
    """
    if offline and online:
        raise ConfigError('--offline и --online взаимоисключающи')
    with quiet_transport_logs():
        _check(config, offline=offline, online=online)


def _check(config: Path, *, offline: bool, online: bool) -> None:
    load_sibling_env(config)
    settings = _check_settings(offline=offline)
    loaded = load_booking_config(config)
    require_owner_channel(loaded)
    print(f'конфиг: {config} — ок')
    if offline:
        print('секреты и ключ модели: не проверялись (--offline)')
    else:
        for line in check_environment(loaded, dict(os.environ)):
            print(line)
    if online:
        for line in asyncio.run(_probe(loaded, dict(os.environ), system_now())):
            print(line)
    for line in describe(loaded, settings, system_now()):
        print(line)


async def _drain(container: AsyncContainer) -> None:
    # Строки, ещё не дописанные в таблицу владельца, дописываются до закрытия контейнера.
    service = await container.get(BookingService)
    try:
        await asyncio.wait_for(service.drain(), timeout=_DRAIN_TIMEOUT_S)
    except TimeoutError:
        logger.error('таблица владельца не дописана за %.0f с при остановке', _DRAIN_TIMEOUT_S)


def server_port(config: BookingConfig, environ: dict[str, str]) -> int:
    """Порт сервера вебхуков: переменная `[booking.server] port_env`, если задана, иначе конфиг.

    Raises:
        ConfigError: В переменной не номер порта.
    """
    server = config.booking.server
    raw = environ.get(server.port_env, '').strip()
    if not raw:
        return server.port
    if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ConfigError(f'{server.port_env}={raw!r}: ожидается номер порта 1–65535')
    return int(raw)


async def _run(config_path: Path) -> None:
    load_sibling_env(config_path)
    settings = Settings.from_env(os.environ)
    config = load_booking_config(config_path)
    require_owner_channel(config)
    environ = dict(os.environ)
    section = config.booking
    secret = require_env(section.telegram.webhook_secret_env, environ) if section.telegram.mode == 'webhook' else None
    port = server_port(config, environ)
    container = _container(settings, config, channels=True)
    try:
        bot = await container.get(Bot)
        booking_bot = await container.get(BookingBot)
        loop = await container.get(ReminderLoop)
        whatsapp = (await container.get(WhatsAppEndpoint)).bot
        channels = 'Telegram' + (' и WhatsApp' if whatsapp is not None else '')
        print(f'{config.client.name}: бот записи запущен ({channels}; Ctrl+C — остановка)')
        await serve(
            config=config,
            bot=bot,
            booking_bot=booking_bot,
            loop=loop,
            whatsapp=whatsapp,
            telegram_secret=secret,
            port=port,
        )
        await _drain(container)
    finally:
        await container.close()


@app.command
def run(config: Path) -> None:
    """Боевой запуск: Telegram (polling или вебхук), WhatsApp, напоминания; Ctrl+C или SIGTERM — остановка.

    Args:
        config: Конфиг клиента (TOML).
    """
    with quiet_transport_logs():
        asyncio.run(_run(config))
    print('бот остановлен')


async def _chat(config_path: Path, *, admin: bool, language: str | None, data_dir: Path) -> int:
    load_sibling_env(config_path)
    base = Settings.from_env(os.environ)
    settings = msgspec.structs.replace(base, data_dir=data_dir)
    loaded = load_booking_config(config_path)
    # Песочница: свои SQLite и CSV (`out` — относительно каталога песочницы), без таблицы,
    # уведомлений и календаря клиента — тест не попадёт в боевые данные и в календарь мастера.
    booking = msgspec.structs.replace(loaded.booking, calendar=None, whatsapp=None)
    sandbox = msgspec.structs.replace(
        loaded, sink=SinkConfig(csv_dir=Path('out')), notify=NotifyConfig(console=False), booking=booking
    )
    container = _container(settings, sandbox, channels=False)
    try:
        engine = await container.get(Engine)
        handled = await repl(engine, admin=admin, language=language)
        await _drain(container)
        return handled
    finally:
        await container.close()


@app.command
def chat(config: Path, *, admin: bool = False, language: str | None = None, data_dir: Path = Path('var/chat')) -> None:
    """Консольный чат с ботом по конфигу клиента — проверка перед запуском и запись демо без Telegram.

    Модель — как в окружении (AUTOSTUDIO_LLM_PROVIDER), без ключа — эвристики FakeLLM.

    Args:
        config: Конфиг клиента (TOML).
        admin: Говорить от имени администратора (команды /today, /cancel_booking …).
        language: Язык «клиента Telegram», например ru.
        data_dir: Каталог песочницы (не каталог боевых данных клиента).
    """
    # Модель и сигнал оператору о её сбое тоже ходят через httpx.
    with quiet_transport_logs():
        asyncio.run(_chat(config, admin=admin, language=language, data_dir=data_dir))


@app.command
def agenda(config: Path, *, day: str | None = None, days: int = 1) -> None:
    """Расписание из базы клиента (только чтение).

    Args:
        config: Конфиг клиента (TOML).
        day: Первый день ГГГГ-ММ-ДД; по умолчанию сегодня.
        days: Сколько дней показать.
    """
    load_sibling_env(config)
    settings = Settings.from_env(os.environ)
    loaded = load_booking_config(config)
    try:
        first = dt.date.fromisoformat(day) if day else local_today(loaded, system_now())
    except ValueError:
        raise ConfigError(f'--day={day!r}: ожидается ГГГГ-ММ-ДД') from None
    if not 1 <= days <= 31:
        raise ConfigError('--days: от 1 до 31')
    zone = loaded.client.zone
    start = day_bounds(first, zone)[0]
    end = day_bounds(first + dt.timedelta(days=days - 1), zone)[1]
    bookings = _readonly_store(settings, loaded).upcoming(start, end)
    texts = Texts(loaded.booking, zone)
    print(render_agenda(loaded, texts, bookings, first, days, loaded.owner_language))


@app.command
def demo(*, out: Path = Path('var/demo/booking')) -> None:
    """Офлайн-демо: сценарий с записью, вопросами, отменой и напоминаниями — без сети и ключей.

    Args:
        out: Каталог для стенограммы, CSV и отчёта.
    """
    print(asyncio.run(run_demo(out)))

"""Команды пакета: `autostudio booking check | run | chat | agenda | demo`.

Команды оператора — на русском, как и в ядре; тексты бота для покупателей — в `texts`.
"""

import asyncio
import datetime as dt
import os
import re
from pathlib import Path
from typing import Final

import cyclopts
import msgspec
from aiogram import Bot
from dishka import AsyncContainer, make_async_container

from autostudio.booking.admin import render_agenda
from autostudio.booking.config import BookingConfig, load_booking_config
from autostudio.booking.console import repl
from autostudio.booking.demo import run_demo
from autostudio.booking.domain import day_bounds, local_today, next_free_slots
from autostudio.booking.engine import Engine
from autostudio.booking.providers import STORE_FILE, BookingProvider, TelegramProvider
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.store import BookingStore
from autostudio.booking.telegram import BookingBot, run_polling
from autostudio.booking.texts import Texts
from autostudio.clientconf import NotifyConfig, SinkConfig, client_data_dir, require_env
from autostudio.clock import system_now
from autostudio.errors import ConfigError
from autostudio.providers import CoreProvider
from autostudio.settings import Settings

app = cyclopts.App(
    name='booking', help='Бот записи в Telegram: проверка конфига, запуск, консольный чат, расписание, демо.'
)

_TOKEN_SHAPE: Final = re.compile(r'^\d{5,}:[A-Za-z0-9_-]{30,}$')
_PREVIEW_SLOTS: Final = 3


def _container(settings: Settings, config: BookingConfig, *, telegram: bool) -> AsyncContainer:
    providers = [CoreProvider(), BookingProvider(), *([TelegramProvider()] if telegram else [])]
    return make_async_container(*providers, context={Settings: settings, BookingConfig: config})


def _readonly_store(settings: Settings, config: BookingConfig) -> BookingStore:
    return BookingStore(client_data_dir(settings.data_dir, config.client) / STORE_FILE, readonly=True)


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
    lines = [
        f'клиент: {config.client.name} ({config.client.id}), {config.client.timezone}; языки покупателей: '
        f'{", ".join(section.languages)}',
        f'услуг: {len(section.services)}, мастеров: {len(section.staff)}, FAQ: {len(section.faq)}, '
        f'администраторов: {len(section.admin_chat_ids)}',
        f'модель: {settings.llm_provider.value} ({settings.llm_model})',
        f'таблица: {config.sink.backend}' + (f' → {config.sink.csv_dir}' if config.sink.backend == 'csv' else ''),
        f'активных записей на горизонте: {len(bookings)}',
        'ближайшие свободные слоты:',
    ]
    for service in section.services:
        slots = next_free_slots(config, service.id, bookings, now, limit=_PREVIEW_SLOTS)
        shown = ', '.join(f'{texts.when(slot.start, lang)} ({texts.staff_name(slot.staff_id)})' for slot in slots)
        lines.append(f'  {texts.pick(service.name, lang)}: {shown or "нет свободного времени на горизонте"}')
    warnings = []
    if not section.admin_chat_ids:
        warnings.append('admin_chat_ids пуст: уведомления о записях получит только канал [notify]')
    if not section.faq:
        warnings.append('FAQ пуст: все вопросы будут передаваться человеку')
    if not section.contact_phone:
        warnings.append('contact_phone не задан: покупателю некуда позвонить, если бот не помог')
    if settings.llm_provider.value == 'fake':
        warnings.append('модель fake: свободный текст разбирается простыми правилами (тариф Basic)')
    return lines + [f'внимание: {item}' for item in warnings]


@app.command
def check(config: Path, *, offline: bool = False) -> None:
    """Проверяет конфиг и окружение без побочных эффектов и показывает ближайшие слоты.

    Args:
        config: Конфиг клиента (TOML).
        offline: Не проверять токен бота и ключи (для проверки конфига до получения секретов).
    """
    settings = Settings.from_env(os.environ)
    loaded = load_booking_config(config)
    print(f'конфиг: {config} — ок')
    if offline:
        print('секреты: не проверялись (--offline)')
    else:
        for line in check_environment(loaded, dict(os.environ)):
            print(line)
    for line in describe(loaded, settings, system_now()):
        print(line)


async def _run(config_path: Path) -> None:
    settings = Settings.from_env(os.environ)
    config = load_booking_config(config_path)
    container = _container(settings, config, telegram=True)
    try:
        bot = await container.get(Bot)
        booking_bot = await container.get(BookingBot)
        loop = await container.get(ReminderLoop)
        print(f'{config.client.name}: бот записи запущен (Ctrl+C — остановка)')
        await run_polling(bot, booking_bot, loop, config.booking.languages)
    finally:
        await container.close()


@app.command
def run(config: Path) -> None:
    """Боевой запуск: long polling Telegram и цикл напоминаний; Ctrl+C или SIGTERM — штатная остановка.

    Args:
        config: Конфиг клиента (TOML).
    """
    asyncio.run(_run(config))
    print('бот остановлен')


async def _chat(config_path: Path, *, admin: bool, language: str | None, data_dir: Path) -> int:
    base = Settings.from_env(os.environ)
    settings = msgspec.structs.replace(base, data_dir=data_dir)
    loaded = load_booking_config(config_path)
    # Песочница: свои SQLite и CSV, без таблицы и уведомлений клиента — тест не попадёт в боевые данные.
    sandbox = msgspec.structs.replace(
        loaded,
        sink=SinkConfig(csv_dir=client_data_dir(data_dir, loaded.client) / 'out'),
        notify=NotifyConfig(console=False),
    )
    container = _container(settings, sandbox, telegram=False)
    try:
        engine = await container.get(Engine)
        return await repl(engine, admin=admin, language=language)
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
    asyncio.run(_chat(config, admin=admin, language=language, data_dir=data_dir))


@app.command
def agenda(config: Path, *, day: str | None = None, days: int = 1) -> None:
    """Расписание из базы клиента (только чтение).

    Args:
        config: Конфиг клиента (TOML).
        day: Первый день ГГГГ-ММ-ДД; по умолчанию сегодня.
        days: Сколько дней показать.
    """
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

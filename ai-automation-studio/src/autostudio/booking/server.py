"""Боевой режим: Telegram (long polling или вебхук), вебхук WhatsApp и цикл напоминаний в одном процессе.

- **Telegram по умолчанию — long polling:** не нужен ни домен, ни сертификат. Режим
  `webhook` (`[booking.telegram] mode`) регистрирует `setWebhook` с `secret_token`, и aiogram
  (`SimpleRequestHandler`) отбрасывает запросы без верного заголовка
  `X-Telegram-Bot-Api-Secret-Token` (сравнение за постоянное время). Вебхук и getUpdates
  взаимоисключающи: при polling бот сам снимает вебхук.
- **HTTP-сервер** (aiohttp) поднимается, только если он нужен (WhatsApp или вебхук Telegram).
  Сам он слушает обычный HTTP; HTTPS с сертификатом даёт обратный прокси (Caddy, nginx),
  который пересылает `https://<домен>/whatsapp/webhook` и `/telegram/webhook` сюда. Telegram
  принимает вебхуки только на портах 443, 80, 88 и 8443 — это порт прокси, не этого сервера.
- **Журнал доступа aiohttp выключен:** в строке запроса проверки WhatsApp лежит
  `hub.verify_token`, и в логах контейнера ему не место.
- Остановка — SIGINT/SIGTERM: polling и сервер останавливаются, цикл напоминаний
  дорабатывает текущий проход.
"""

import asyncio
import contextlib
import logging
import signal
from typing import Final

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

from autostudio.booking.config import BookingConfig
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.telegram import ALLOWED_UPDATES, BookingBot, set_commands
from autostudio.booking.whatsapp import WhatsAppBot

logger = logging.getLogger(__name__)

TELEGRAM_PATH: Final = '/telegram/webhook'
WHATSAPP_PATH: Final = '/whatsapp/webhook'
HEALTH_PATH: Final = '/healthz'
# Публичный бот: поток апдейтов не должен порождать неограниченное число задач.
TASKS_LIMIT: Final = 64


async def _health(request: web.Request) -> web.Response:
    del request
    return web.Response(text='ok')


def web_app(
    *,
    dispatcher: Dispatcher | None = None,
    bot: Bot | None = None,
    telegram_secret: str | None = None,
    whatsapp: WhatsAppBot | None = None,
) -> web.Application:
    """aiohttp-приложение вебхуков.

    Args:
        dispatcher: Диспетчер aiogram (для вебхука Telegram) или None.
        bot: Бот aiogram (для вебхука Telegram) или None.
        telegram_secret: Секрет вебхука Telegram (обязателен вместе с ботом).
        whatsapp: Вебхук WhatsApp или None.

    Returns:
        Приложение с маршрутами `/telegram/webhook`, `/whatsapp/webhook` и `/healthz`.
    """
    app = web.Application()
    app.router.add_get(HEALTH_PATH, _health)
    if dispatcher is not None and bot is not None:
        if not telegram_secret:
            raise ValueError('вебхук Telegram без secret_token принял бы апдейты от кого угодно')
        SimpleRequestHandler(dispatcher, bot, secret_token=telegram_secret).register(app, path=TELEGRAM_PATH)
    if whatsapp is not None:
        whatsapp.routes(app, WHATSAPP_PATH)
    return app


def _install_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)


async def serve(
    *,
    config: BookingConfig,
    bot: Bot,
    booking_bot: BookingBot,
    loop: ReminderLoop,
    whatsapp: WhatsAppBot | None = None,
    telegram_secret: str | None = None,
    port: int | None = None,
    stop: asyncio.Event | None = None,
    install_signals: bool = True,
) -> None:
    """Запускает каналы и цикл напоминаний до остановки.

    Args:
        config: Конфиг клиента.
        bot: Бот aiogram (сессию закрывает провайдер).
        booking_bot: Склейка Telegram с движком.
        loop: Цикл напоминаний.
        whatsapp: Вебхук WhatsApp или None.
        telegram_secret: Секрет вебхука Telegram (режим webhook).
        port: Порт HTTP-сервера; None — из конфига.
        stop: Событие остановки (тесты); по умолчанию — своё, по SIGINT/SIGTERM.
        install_signals: Ставить ли обработчики сигналов.
    """
    section = config.booking
    stop = stop or asyncio.Event()
    if install_signals:
        _install_signals(stop)
    dispatcher = Dispatcher()
    dispatcher.include_router(booking_bot.router())
    await set_commands(bot, booking_bot, section.languages)
    webhook = section.telegram.mode == 'webhook'
    background = asyncio.create_task(loop.run(stop))
    polling: asyncio.Task[None] | None = None
    runner: web.AppRunner | None = None
    try:
        if webhook:
            url = f'{section.server.base_url}{TELEGRAM_PATH}'
            await bot.set_webhook(
                url, secret_token=telegram_secret, allowed_updates=ALLOWED_UPDATES, drop_pending_updates=False
            )
            logger.info('Telegram: вебхук %s', TELEGRAM_PATH)
        else:
            # Вебхук и long polling взаимоисключающи: если кто-то включал вебхук, getUpdates упадёт.
            await bot.delete_webhook(drop_pending_updates=False)
            polling = asyncio.create_task(
                dispatcher.start_polling(
                    bot,
                    handle_signals=False,
                    close_bot_session=False,
                    allowed_updates=ALLOWED_UPDATES,
                    tasks_concurrency_limit=TASKS_LIMIT,
                )
            )
        if section.needs_server:
            app = web_app(
                dispatcher=dispatcher if webhook else None,
                bot=bot if webhook else None,
                telegram_secret=telegram_secret,
                whatsapp=whatsapp,
            )
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, section.server.host, port or section.server.port).start()
            logger.info('сервер вебхуков слушает порт %s', port or section.server.port)
        stopped = asyncio.create_task(stop.wait())
        waiters: set[asyncio.Task[None] | asyncio.Task[bool]] = {stopped}
        if polling is not None:
            waiters.add(polling)
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        stopped.cancel()
        if polling is not None and polling.done():
            # Polling кончился сам (токен отозван, сеть) — это сбой, а не остановка.
            polling.result()
    finally:
        stop.set()
        if polling is not None and not polling.done():
            with contextlib.suppress(RuntimeError):
                await dispatcher.stop_polling()
            await polling
        if runner is not None:
            await runner.cleanup()
        if whatsapp is not None:
            await whatsapp.drain()
        await background

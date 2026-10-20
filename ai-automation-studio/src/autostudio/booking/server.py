"""Боевой режим: Telegram (long polling или вебхук), вебхук WhatsApp и цикл напоминаний в одном процессе.

- **Telegram по умолчанию — long polling:** не нужен ни домен, ни сертификат. Режим
  `webhook` (`[booking.telegram] mode`) регистрирует `setWebhook` с `secret_token`, а
  `TelegramWebhook` отбрасывает запросы без верного заголовка `X-Telegram-Bot-Api-Secret-Token`
  (сравнение за постоянное время). Вебхук и getUpdates взаимоисключающи: при polling бот сам
  снимает вебхук.
- **Вебхук Telegram — с той же гигиеной, что polling:** ответ 200 сразу, обработка в фоне, но
  не больше `TASKS_LIMIT` апдейтов одновременно (как `tasks_concurrency_limit` у polling), и при
  остановке сервер дожидается начатых апдейтов. Telegram уже получил 200 и повторять апдейт не
  будет: прерванное на SIGTERM нажатие «Подтвердить» потеряло бы ответ покупателю.
  `SimpleRequestHandler` aiogram этого не умеет (задача на каждый апдейт без предела, при
  остановке задачи не ждутся, а сессия бота закрывается раньше, чем доработает цикл напоминаний).
- **HTTP-сервер** (aiohttp) поднимается, только если он нужен (WhatsApp или вебхук Telegram).
  Сам он слушает обычный HTTP; HTTPS с сертификатом даёт обратный прокси (Caddy, nginx),
  который пересылает `https://<домен>/whatsapp/webhook` и `/telegram/webhook` сюда. Telegram
  принимает вебхуки только на портах 443, 80, 88 и 8443 — это порт прокси, не этого сервера.
- **Предел тела запроса — 4 МиБ:** Meta шлёт вебхуки WhatsApp до 3 МБ, а у aiohttp по
  умолчанию 1 МиБ. Больший вебхук получил бы 413 ещё до проверки подписи, Meta повторяла бы
  его 7 дней и выбросила — сообщения покупателей пропали бы.
- **Журнал доступа aiohttp выключен:** в строке запроса проверки WhatsApp лежит
  `hub.verify_token`, и в логах контейнера ему не место.
- Остановка — SIGINT/SIGTERM: polling и сервер останавливаются, начатые апдейты и сообщения
  дорабатываются, цикл напоминаний дорабатывает текущий проход.
"""

import asyncio
import contextlib
import hmac
import logging
import signal
from typing import Final

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiohttp import web

from autostudio.booking.config import BookingConfig
from autostudio.booking.reminders import ReminderLoop
from autostudio.booking.telegram import ALLOWED_UPDATES, BookingBot, set_commands
from autostudio.booking.whatsapp import WhatsAppBot

logger = logging.getLogger(__name__)

TELEGRAM_PATH: Final = '/telegram/webhook'
WHATSAPP_PATH: Final = '/whatsapp/webhook'
HEALTH_PATH: Final = '/healthz'
# Заголовок с секретом setWebhook (сам секрет — в окружении).
TELEGRAM_AUTH_HEADER: Final = 'X-Telegram-Bot-Api-Secret-Token'
# Публичный бот: поток апдейтов не должен порождать неограниченное число задач.
TASKS_LIMIT: Final = 64
# Meta: «Webhook payloads can be up to 3 MB»; у aiohttp по умолчанию 1 МиБ — запас сверху.
WEBHOOK_BODY_MAX: Final = 4 * 1024**2


async def _health(request: web.Request) -> web.Response:
    del request
    return web.Response(text='ok')


class TelegramWebhook:
    """Вебхук Telegram: секрет, апдейт в фоне не больше `limit` одновременно, дожидание при остановке.

    Слот берётся до ответа 200: когда в работе уже `limit` апдейтов, следующий запрос ждёт
    свободного слота, и Telegram (не больше `max_connections` соединений) притормаживает сам —
    обратное давление вместо очереди задач без предела в памяти.

    Args:
        dispatcher: Диспетчер aiogram с роутером бота.
        bot: Бот aiogram.
        secret: Секрет `setWebhook` (заголовок `X-Telegram-Bot-Api-Secret-Token`).
        limit: Сколько апдейтов обрабатывается одновременно.

    Raises:
        ValueError: Секрет пуст — вебхук принял бы апдейты от кого угодно.
    """

    def __init__(self, dispatcher: Dispatcher, bot: Bot, secret: str | None, *, limit: int = TASKS_LIMIT) -> None:
        if not secret:
            raise ValueError('вебхук Telegram без secret_token принял бы апдейты от кого угодно')
        self._dispatcher = dispatcher
        self._bot = bot
        self._secret = secret.encode()
        self._slots = asyncio.Semaphore(limit)
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def in_flight(self) -> int:
        """Сколько апдейтов сейчас в обработке."""
        return len(self._tasks)

    def routes(self, app: web.Application, path: str) -> None:
        """Регистрирует POST `path`."""
        app.router.add_post(path, self.receive)

    async def receive(self, request: web.Request) -> web.Response:
        """POST от Telegram: секрет, разбор апдейта, обработка в фоне, ответ 200."""
        given = request.headers.get(TELEGRAM_AUTH_HEADER, '').encode()
        if not hmac.compare_digest(given, self._secret):
            return web.Response(status=401, text='Unauthorized')
        try:
            update = Update.model_validate_json(await request.read(), context={'bot': self._bot})
        except ValueError:
            # Ошибка разбора pydantic — подкласс ValueError; повтор того же тела не поможет.
            logger.warning('вебхук Telegram: апдейт не разобран')
            return web.Response(status=400)
        await self._slots.acquire()
        task = asyncio.get_running_loop().create_task(self._feed(update))
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return web.json_response({})

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._slots.release()

    async def _feed(self, update: Update) -> None:
        try:
            await self._dispatcher.feed_update(self._bot, update)
        except Exception:
            # Как у polling: сбой одного апдейта — в лог; движок сам извиняется перед покупателем.
            logger.exception('апдейт Telegram %s не обработан', update.update_id)

    async def drain(self) -> None:
        """Дожидается начатых апдейтов (остановка, тесты)."""
        while self._tasks:
            await asyncio.wait(set(self._tasks))


def web_app(*, telegram: TelegramWebhook | None = None, whatsapp: WhatsAppBot | None = None) -> web.Application:
    """aiohttp-приложение вебхуков.

    Args:
        telegram: Вебхук Telegram или None.
        whatsapp: Вебхук WhatsApp или None.

    Returns:
        Приложение с маршрутами `/telegram/webhook`, `/whatsapp/webhook` и `/healthz`.
    """
    app = web.Application(client_max_size=WEBHOOK_BODY_MAX)
    app.router.add_get(HEALTH_PATH, _health)
    if telegram is not None:
        telegram.routes(app, TELEGRAM_PATH)
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
    telegram = TelegramWebhook(dispatcher, bot, telegram_secret) if webhook else None
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
            app = web_app(telegram=telegram, whatsapp=whatsapp)
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
        # Новых запросов больше нет; начатые апдейты дорабатываются, пока жива сессия бота.
        if telegram is not None:
            await telegram.drain()
        if whatsapp is not None:
            await whatsapp.drain()
        await background

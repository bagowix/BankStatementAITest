"""Команды монитора цен: `autostudio prices check | run | probe | demo`."""

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Final

import cyclopts
import httpx
from dishka import Provider, make_async_container

from autostudio.clientconf import load_client_config
from autostudio.clock import Clock
from autostudio.errors import AutoStudioError, ConfigError
from autostudio.llm import make_llm
from autostudio.prices.config import Offer, PricesConfig, PricesSection, Product
from autostudio.prices.demo import run_demo
from autostudio.prices.extract import (
    ExtractionFailure,
    PriceReading,
    explain,
    extract_price,
    read_with_llm,
    suggest_selectors,
)
from autostudio.prices.fetch import FetchFailure, FetchSettings, PoliteFetcher, Resolver, system_resolver
from autostudio.prices.heuristics import make_fake_llm
from autostudio.prices.monitor import PricesDeps, RunResult, run_once
from autostudio.prices.providers import PricesProvider
from autostudio.prices.urls import redact
from autostudio.providers import CoreProvider, OperatorAlerts
from autostudio.runlog import Heartbeat, supervised_run
from autostudio.settings import LLMProvider, Settings

app = cyclopts.App(name='prices', help='Монитор цен конкурентов → Google Sheets + уведомления.')

_CONTACT_ENV: Final = 'AUTOSTUDIO_PRICES_CONTACT_EMAIL'
_HOUR_S: Final = 3600.0
_MIN_EVERY_HOURS: Final = 1.0
_MAX_EVERY_HOURS: Final = 24.0 * 7

type Sleeper = Callable[[float], Awaitable[None]]


def offer_table(config: PricesConfig) -> str:
    """Таблица ссылок клиента: что и как будет читаться."""
    rows = [('product', 'own price', 'competitor', 'site', 'rung', 'currency')]
    for product in config.prices.products:
        own = f'{product.own_price} {product.currency}' if product.own_price is not None else '—'
        for offer in product.offers:
            rung = (
                f'selector {offer.selector}' + (f' @{offer.attr}' if offer.attr else '') if offer.selector else 'auto'
            )
            host = httpx.URL(offer.url).host
            rows.append((product.id, own, offer.competitor, host, rung, offer.currency or 'from page'))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return '\n'.join('  '.join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows)


def environment_report(config: PricesConfig, environ: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Чего не хватает в окружении для боевого запуска — без сети и без записи.

    Args:
        config: Конфиг клиента.
        environ: Окружение процесса.

    Returns:
        Проблемы (запуск упадёт) и замечания (запуск пройдёт, но стоит знать).
    """
    problems: list[str] = []
    notes: list[str] = []
    try:
        settings = Settings.from_env(environ)
    except ConfigError as exc:
        problems.append(str(exc))
    else:
        if config.prices.llm_fallback and settings.llm_provider is LLMProvider.FAKE:
            notes.append('llm_fallback включён, но AUTOSTUDIO_LLM_PROVIDER=fake: вместо модели — эвристика без ИИ')
    if config.sink.backend == 'google':
        path = environ.get(config.sink.credentials_env, '')
        if not path:
            problems.append(f'{config.sink.credentials_env} не задан: нужен JSON-ключ сервисного аккаунта Google')
        elif not Path(path).is_file():
            problems.append(f'{config.sink.credentials_env}={path}: файла нет')
    if config.notify.telegram_chat_id and not environ.get(config.notify.telegram_token_env):
        problems.append(f'{config.notify.telegram_token_env} не задан: нечем слать уведомления в Telegram')
    if config.notify.webhook_url_env and not environ.get(config.notify.webhook_url_env):
        problems.append(f'{config.notify.webhook_url_env} не задан: URL вебхука')
    if not config.notify.telegram_chat_id and not config.notify.webhook_url_env:
        notes.append('уведомления владельцу идут только в консоль: задайте notify.telegram_chat_id')
    if not config.prices.respect_robots:
        notes.append('respect_robots = false: только с письменного разрешения владельцев сайтов')
    return problems, notes


@app.command
def check(config: Path) -> None:
    """Проверяет конфиг и окружение без сети и без записи; печатает таблицу ссылок.

    Args:
        config: TOML-конфиг клиента.
    """
    loaded = load_client_config(config, PricesConfig)
    print(offer_table(loaded))
    print(f'\n{len(loaded.prices.products)} товаров, {loaded.prices.offer_count} ссылок, клиент {loaded.client.id}')
    problems, notes = environment_report(loaded, os.environ)
    for note in notes:
        print(f'! {note}')
    for problem in problems:
        print(f'✗ {problem}')
    if problems:
        raise ConfigError(f'окружение не готово: {len(problems)} проблем(ы), см. выше')
    print('✓ конфиг и окружение в порядке')


def summary(result: RunResult) -> str:
    """Одна строка итога запуска для лога cron/systemd."""
    failures = ', '.join(f'{reason} {count}' for reason, count in sorted(result.failures.items()))
    return (
        f'prices: прочитано {result.checked}, не прочитано {result.failed}'
        + (f' ({failures})' if failures else '')
        + f', событий {len(result.changes)}, страниц модели {result.llm_pages}'
    )


async def serve(
    config_path: Path,
    *,
    every_hours: float | None,
    provider: Callable[[], Provider] = PricesProvider,
    sleep: Sleeper = asyncio.sleep,
    max_cycles: int | None = None,
) -> bool:
    """Боевой цикл: проход под `supervised_run` (журнал, healthcheck, сигнал оператору).

    Args:
        config_path: Конфиг клиента.
        every_hours: Пауза между проходами или None — один проход (для cron/systemd timer).
        provider: Фабрика провайдера пакета (тесты подставляют свой резолвер).
        sleep: Пауза между проходами.
        max_cycles: Ограничение числа проходов (для тестов).

    Returns:
        True, если последний проход завершился без ошибки пакета.
    """
    settings = Settings.from_env(os.environ)
    config = load_client_config(config_path, PricesConfig)
    container = make_async_container(CoreProvider(), provider(), context={Settings: settings, PricesConfig: config})
    try:
        deps = await container.get(PricesDeps)
        heartbeat = await container.get(Heartbeat)
        operator = await container.get(OperatorAlerts)
        clock = await container.get(Clock)
        cycles = 0
        while True:
            result = await supervised_run(
                package='prices',
                job=lambda: run_once(config, deps, clock()),
                events=deps.events,
                heartbeat=heartbeat,
                operator=operator.notifier,
                client_name=config.client.name,
                now=clock,
            )
            if result is not None:
                print(summary(result), flush=True)
            cycles += 1
            if every_hours is None or (max_cycles is not None and cycles >= max_cycles):
                return result is not None
            await sleep(every_hours * _HOUR_S)
    finally:
        await container.close()


@app.command
def run(config: Path, *, every_hours: float | None = None) -> None:
    """Боевой запуск: один проход (для cron или systemd timer) или цикл каждые N часов.

    Args:
        config: TOML-конфиг клиента.
        every_hours: Повторять каждые N часов (1–168); без флага — один проход.
    """
    if every_hours is not None and not _MIN_EVERY_HOURS <= every_hours <= _MAX_EVERY_HOURS:
        raise ConfigError(f'--every-hours={every_hours}: допустимо от 1 до 168 (чаще раза в час сайты не мучаем)')
    if not asyncio.run(serve(config, every_hours=every_hours)):
        raise AutoStudioError('проход монитора завершился ошибкой — подробности в журнале, оператор уведомлён')


def _show(reading: PriceReading | None, note: str = '') -> str:
    if reading is None:
        return f'— ({note})' if note else '—'
    extra = [reading.availability.value] if reading.availability.value != 'unknown' else []
    if reading.qualifier.value != 'exact':
        extra.append(reading.qualifier.value)
    if reading.warnings:
        extra.append('warnings: ' + ', '.join(reading.warnings))
    tail = f' [{"; ".join(extra)}]' if extra else ''
    return f'{reading.amount} {reading.currency or "(валюта не найдена)"} ← {reading.raw!r}{tail}'


async def probe_report(
    url: str,
    *,
    section: PricesSection,
    offer: Offer,
    http: httpx.AsyncClient,
    resolver: Resolver = system_resolver,
    use_llm: bool = False,
    settings: Settings | None = None,
) -> str:
    """Отчёт `probe`: что видит каждая ступень на одной странице и какой TOML вставить.

    Args:
        url: Адрес страницы конкурента.
        section: Настройки вежливости (из конфига клиента или по умолчанию).
        offer: Страница с селектором из флагов.
        http: httpx-клиент.
        resolver: DNS-резолвер.
        use_llm: Спросить и модель (платно, если провайдер не fake).
        settings: Настройки процесса для модели.

    Returns:
        Текст отчёта.
    """
    fetcher = PoliteFetcher(http, FetchSettings.from_section(section), resolver=resolver)
    page = await fetcher.fetch(url)
    out = [f'URL: {redact(url)}']
    if isinstance(page, FetchFailure):
        out.append(f'✗ загрузка: {page.reason} {page.detail}'.rstrip())
        return '\n'.join(out)
    out.append(f'✓ загрузка: HTTP {page.status}, {len(page.html):,} символов, итоговый адрес {redact(page.final_url)}')
    for report in explain(page.html, offer, allow_free=section.allow_free):
        out.append(f'  {report.rung:<10} {_show(report.reading, report.note)}')
    if use_llm:
        llm = make_llm(settings or Settings.from_env(os.environ), fake=make_fake_llm())
        guess = await read_with_llm(page.html, offer, product_name='product on this page', llm=llm)
        shown = _show(guess) if isinstance(guess, PriceReading) else f'✗ {guess.reason}: {guess.detail}'
        out.append(f'  {"llm":<10} {shown} (модель {llm.model}, токенов на входе {llm.usage.input_tokens})')
    else:
        out.append(f'  {"llm":<10} — (не спрашивали: добавьте --llm)')
    final = extract_price(page.html, offer, allow_free=section.allow_free)
    verdict = _show(final) if isinstance(final, PriceReading) else f'✗ {final.reason}'
    out.append(f'Итог бесплатного каскада: {verdict}')
    suggestions = suggest_selectors(page.html)
    if suggestions:
        out.append('Кандидаты в селекторы (проверены: находят тот же текст):')
        out.extend(
            f'  {s.selector:<40} {s.text!r} → {s.amount} {s.currency}'
            + ('  ← похоже на старую цену' if s.likely_old else '')
            for s in suggestions
        )
    usable = [s for s in suggestions if not s.likely_old]
    chosen = offer.selector or (usable[0].selector if usable and isinstance(final, ExtractionFailure) else None)
    out += ['', 'Строки для конфига клиента:', '  [[prices.products.offers]]', '  competitor = "…"', f'  url = "{url}"']
    if chosen:
        out.append(f'  selector = "{chosen}"')
        if not offer.selector and len({s.amount for s in usable}) > 1:
            # Селектор — самая доверенная ступень; угаданный неверно, он молча врал бы каждый день.
            out.append('  # ⚠ на странице несколько разных цен — сверьте с сайтом, что это текущая цена')
    if offer.attr:
        out.append(f'  attr = "{offer.attr}"')
    if offer.currency:
        out.append(f'  currency = "{offer.currency}"')
    return '\n'.join(out)


@app.command
def probe(
    url: str,
    *,
    selector: str | None = None,
    attr: str | None = None,
    currency: str | None = None,
    config: Path | None = None,
    contact_email: str | None = None,
    llm: bool = False,
) -> None:
    """Онбординг конкурента: вежливо загрузить одну страницу и показать, что читает каждая ступень.

    Args:
        url: Страница товара у конкурента.
        selector: Проверить CSS-селектор цены.
        attr: Читать атрибут элемента вместо текста.
        currency: Валюта магазина (если на странице её нет или это «$»).
        config: Конфиг клиента — взять из него User-Agent, паузы и robots.
        contact_email: Адрес для User-Agent, если конфига нет (или AUTOSTUDIO_PRICES_CONTACT_EMAIL).
        llm: Спросить и модель (платная ступень; с провайдером fake — эвристика).
    """
    offer = Offer(competitor='probe', url=url, selector=selector, attr=attr, currency=currency)
    if config is not None:
        section = load_client_config(config, PricesConfig).prices
    else:
        email = contact_email or os.environ.get(_CONTACT_ENV, '')
        if not email:
            raise ConfigError(f'нужен --contact-email, --config или {_CONTACT_ENV}: адрес уходит в User-Agent')
        section = PricesSection(products=[Product(id='probe', name='probe')], contact_email=email)

    async def go() -> str:
        async with httpx.AsyncClient() as http:
            return await probe_report(url, section=section, offer=offer, http=http, use_llm=llm)

    print(asyncio.run(go()))


@app.command
def demo(*, out: Path = Path('var/demo/prices')) -> None:
    """Офлайн-демо: три магазина, два дня, без сети и ключей.

    Args:
        out: Каталог для CSV-вкладок и состояния демо.
    """
    print(asyncio.run(run_demo(out)))

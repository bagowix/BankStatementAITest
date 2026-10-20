"""Команды монитора цен: `autostudio prices check | run | probe | demo`."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Final

import cyclopts
import httpx
from dishka import Provider, make_async_container

from autostudio.clientconf import load_client_config, load_sibling_env
from autostudio.clock import Clock
from autostudio.errors import AutoStudioError, ConfigError
from autostudio.llm import LLMClient, make_llm
from autostudio.prices import texts
from autostudio.prices.capacity import Capacity, capacity, duration_text
from autostudio.prices.config import PARALLEL_SITES, Offer, PricesConfig, PricesSection, Product
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
from autostudio.runlog import Heartbeat, RunSkipped, supervised_run
from autostudio.settings import LLMProvider, Settings

logger = logging.getLogger(__name__)

app = cyclopts.App(name='prices', help='Монитор цен конкурентов → Google Sheets + уведомления.')

_CONTACT_ENV: Final = 'AUTOSTUDIO_PRICES_CONTACT_EMAIL'
# Зарезервированные домены (RFC 2606/6761): адреса из образца, которые не ведут никуда.
_RESERVED_SUFFIXES: Final = ('.example', '.test', '.invalid', '.localhost')
_RESERVED_DOMAINS: Final = frozenset({'example.com', 'example.org', 'example.net'})
_HOUR_S: Final = 3600.0
_MIN_EVERY_HOURS: Final = 1.0
_MAX_EVERY_HOURS: Final = 24.0 * 7
_SHOWN_SITES: Final = 10
SKIPPED: Final = 'prices: пропущено — предыдущий проход по этому клиенту ещё идёт'

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
            if config.prices.paused_site(offer.url) is not None:
                rung = f'PAUSED ({rung})'
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
            # Эвристика в боевом запуске не работает (её «цена» выглядела бы ответом ИИ), так что
            # ступень модели молча выключилась бы — это ошибка конфигурации, а не замечание.
            problems.append(
                'llm_fallback включён, но AUTOSTUDIO_LLM_PROVIDER=fake: ступень модели в боевом запуске выключится '
                '— задайте openai/anthropic и ключ или llm_fallback = false'
            )
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


def _check_every_hours(every_hours: float | None) -> None:
    if every_hours is not None and not _MIN_EVERY_HOURS <= every_hours <= _MAX_EVERY_HOURS:
        raise ConfigError(f'--every-hours={every_hours}: допустимо от 1 до 168 (чаще раза в час сайты не мучаем)')


def capacity_report(load: Capacity, section: PricesSection) -> list[str]:
    """Нагрузка на сайты для `check`: самые загруженные сайты и длина прохода.

    Args:
        load: Результат `capacity`.
        section: Секция `[prices]` (пауза между запросами и robots.txt — для подписи).

    Returns:
        Строки отчёта (предупреждения — отдельно, в `load.warnings`).
    """
    robots = ', плюс robots.txt' if section.respect_robots else ''
    out = [f'Нагрузка на сайты (пауза {section.min_delay_s:g} с между запросами{robots}; без Crawl-delay):']
    for site in load.sites[:_SHOWN_SITES]:
        out.append(
            f'  {site.site:<32} ссылок {site.offers}, запросов за проход {site.requests}, '
            f'≈ {duration_text(site.pass_s)}, до {site.per_hour} запросов/ч'
        )
    if len(load.sites) > _SHOWN_SITES:
        out.append(f'  … и ещё сайтов: {len(load.sites) - _SHOWN_SITES}')
    schedule = (
        f'расписание — раз в {load.interval_s / _HOUR_S:g} ч'
        if load.interval_s is not None
        else 'расписание не задано (check --every-hours N сверит его)'
    )
    out.append(
        f'Весь проход ≈ {duration_text(load.total_s)} (сайтов одновременно до {PARALLEL_SITES}); '
        f'без наложений — не чаще раза в {load.min_every_hours} ч; {schedule}'
    )
    return out


def paused_notes(config: PricesConfig) -> list[str]:
    """Сайты на паузе: что не проверяется и почему (замечания `check`, не ошибки).

    Пауза без ссылок на этот сайт — тоже замечание, а не ошибка: так выглядит конфиг после
    претензии (ссылки удалены, запрет остался). Опечатку в домене оператор видит здесь же.
    """
    section = config.prices
    counts = dict.fromkeys(sorted(section.paused_sites), 0)
    for product in section.products:
        for offer in product.offers:
            site = section.paused_site(offer.url)
            if site is not None:
                counts[site] += 1
    notes: list[str] = []
    for site, count in counts.items():
        head = f'сайт {site} на паузе ({section.sites[site].paused_reason or "причина не указана"}): '
        if count:
            notes.append(f'{head}ссылок не проверяется: {count}')
        else:
            notes.append(
                f'{head}ссылок на него в конфиге нет — действует как запрет, в том числе для редиректов. '
                'Ставили паузу сайту, на который есть ссылки, — проверьте домен на опечатку'
            )
    return notes


def _reserved(host: str) -> bool:
    name = host.lower().rstrip('.')
    return name.endswith(_RESERVED_SUFFIXES) or any(
        name == domain or name.endswith(f'.{domain}') for domain in _RESERVED_DOMAINS
    )


def readiness_report(config: PricesConfig) -> list[str]:
    """Что в конфиге не даст клиенту пользы, хотя запуск и пройдёт (образец не дозаполнен).

    Args:
        config: Конфиг клиента.

    Returns:
        Замечания (с `check --strict` — проблемы).
    """
    out: list[str] = []
    section = config.prices
    if config.sink.backend == 'csv':
        out.append(f'sink.backend = "csv": таблица пишется в {config.sink.csv_dir}, клиент её не увидит без Google')
    if _reserved(section.contact_email.rsplit('@', 1)[-1]):
        out.append(
            f'prices.contact_email = {section.contact_email}: адрес из образца — владельцы сайтов не смогут написать'
        )
    demo = [
        f'{product.id} / {offer.competitor}'
        for product in section.products
        for offer in product.offers
        if _reserved(httpx.URL(offer.url).host)
    ]
    if demo:
        out.append(
            f'ссылок на демо-доменах (*.example и т.п.): {len(demo)}, например {demo[0]} — это не страницы конкурентов'
        )
    for product in section.products:
        if not product.offers:
            out.append(f'product {product.id}: нет ни одной ссылки конкурента — товар не проверяется')
        if product.own_price is None:
            out.append(f'product {product.id}: нет own_price — сравнения «дешевле вас» не будет')
    if config.client.language not in texts.LANGUAGES:
        out.append(
            f'client.language = "{config.client.language}": перевода нет, таблица и сообщения будут на английском'
        )
    return out


@app.command
def check(config: Path, *, strict: bool = False, every_hours: float | None = None) -> None:
    """Проверяет конфиг и окружение без сети и без записи; печатает таблицу ссылок и нагрузку на сайты.

    Args:
        config: TOML-конфиг клиента (`.env` клиента рядом с ним подхватывается).
        strict: Считать проблемой и то, что не даст клиенту пользы (csv, адреса из образца,
            товары без ссылок и без своей цены, проход не укладывается в пределы) — перед
            первым боевым запуском.
        every_hours: Плановый интервал проходов (таймер или `run --every-hours`): с ним
            сверяется длительность прохода.
    """
    _check_every_hours(every_hours)
    # До Settings.from_env: вне Docker окружение клиента лежит в clients/<id>/.env.
    load_sibling_env(config)
    loaded = load_client_config(config, PricesConfig)
    print(offer_table(loaded))
    print(f'\n{len(loaded.prices.products)} товаров, {loaded.prices.offer_count} ссылок, клиент {loaded.client.id}')
    load = capacity(loaded.prices, every_hours=every_hours)
    print('\n'.join(['', *capacity_report(load, loaded.prices), '']))
    problems, notes = environment_report(loaded, os.environ)
    notes += paused_notes(loaded)
    # Перегруз сайта — как недозаполненный образец: запуск пройдёт, но клиенту (и сайту) хуже.
    readiness = readiness_report(loaded) + list(load.warnings)
    if strict:
        problems += readiness
    else:
        notes += readiness
    for note in notes:
        print(f'! {note}')
    for problem in problems:
        print(f'✗ {problem}')
    if problems:
        raise ConfigError(f'не готово к боевому запуску: {len(problems)} проблем(ы), см. выше')
    if readiness:
        print(f'✓ конфиг и окружение читаются; к боевому запуску замечаний: {len(readiness)} (см. «!»; check --strict)')
    else:
        print('✓ конфиг и окружение в порядке')


def summary(result: RunResult) -> str:
    """Одна строка итога запуска для лога cron/systemd."""
    failures = ', '.join(f'{reason} {count}' for reason, count in sorted(result.failures.items()))
    tokens = result.llm_input_tokens + result.llm_output_tokens
    return (
        f'prices: прочитано {result.checked}, не прочитано {result.failed}'
        + (f' ({failures})' if failures else '')
        + (f', на паузе {result.paused}' if result.paused else '')
        + f', событий {len(result.changes)}, страниц модели {result.llm_pages}'
        # Себестоимость клиента для оператора: токены модели за проход.
        + (f', токенов модели {result.llm_input_tokens} вх. / {result.llm_output_tokens} вых.' if tokens else '')
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

    Конфиг читается перед каждым проходом, как и при запуске по таймеру: пауза сайта,
    добавленная в день претензии, действует со следующего прохода `run --every-hours` без
    перезапуска процесса. Сломанный между проходами конфиг — сбой прохода (оператору — через
    `supervised_run`), и ни один сайт не запрашивается, пока его не починят: со старым
    конфигом цикл мог бы собирать цены с сайта, который только что поставили на паузу.

    Args:
        config_path: Конфиг клиента.
        every_hours: Пауза между проходами или None — один проход (для cron/systemd timer).
        provider: Фабрика провайдера пакета (тесты подставляют свой резолвер).
        sleep: Пауза между проходами.
        max_cycles: Ограничение числа проходов (для тестов).

    Returns:
        True, если последний проход завершился без ошибки пакета (или был сознательно
        пропущен: другой проход ещё держит замок клиента).
    """
    settings = Settings.from_env(os.environ)
    # Первое чтение — без поблажек: сломанный конфиг на старте останавливает команду сразу.
    config = load_client_config(config_path, PricesConfig)
    cycles = 0
    while True:
        broken: ConfigError | None = None
        if cycles:
            try:
                config = load_client_config(config_path, PricesConfig)
            except ConfigError as exc:
                # Журнал, пинг и канал оператора — по последнему исправному конфигу.
                broken = exc
        ok = await _cycle(config, settings, every_hours=every_hours, provider=provider, broken=broken)
        cycles += 1
        if every_hours is None or (max_cycles is not None and cycles >= max_cycles):
            return ok
        await sleep(every_hours * _HOUR_S)


async def _cycle(
    config: PricesConfig,
    settings: Settings,
    *,
    every_hours: float | None,
    provider: Callable[[], Provider],
    broken: ConfigError | None,
) -> bool:
    """Один проход в своём контейнере: зависимости собираются по конфигу этого прохода."""
    if broken is None:
        for warning in capacity(config.prices, every_hours=every_hours).warnings:
            # Не стоп: оператор узнаёт из лога, что сайт перегружен или проход не успеет.
            logger.warning('prices %s: ёмкость: %s', config.client.id, warning)
    container = make_async_container(CoreProvider(), provider(), context={Settings: settings, PricesConfig: config})
    try:
        deps = await container.get(PricesDeps)
        heartbeat = await container.get(Heartbeat)
        operator = await container.get(OperatorAlerts)
        clock = await container.get(Clock)
        skipped = False

        async def job() -> RunResult:
            nonlocal skipped
            if broken is not None:
                raise broken
            try:
                return await run_once(config, deps, clock())
            except RunSkipped:
                # supervised_run вернёт None и на пропуск, и на сбой — а это разные исходы.
                skipped = True
                raise

        result = await supervised_run(
            package='prices',
            job=job,
            events=deps.events,
            heartbeat=heartbeat,
            operator=operator.notifier,
            client_name=config.client.name,
            now=clock,
        )
        if result is not None:
            print(summary(result), flush=True)
        elif skipped:
            print(SKIPPED, flush=True)
        return result is not None or skipped
    finally:
        await container.close()


@app.command
def run(config: Path, *, every_hours: float | None = None) -> None:
    """Боевой запуск: один проход (для cron или systemd timer) или цикл каждые N часов.

    Args:
        config: TOML-конфиг клиента (`.env` клиента рядом с ним подхватывается).
        every_hours: Повторять каждые N часов (1–168); без флага — один проход.
    """
    _check_every_hours(every_hours)
    load_sibling_env(config)
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
    for report in explain(page.html, offer, allow_free=section.allow_free, page_url=page.final_url):
        out.append(f'  {report.rung:<10} {_show(report.reading, report.note)}')
    if use_llm:
        out.append(await _probe_model(page.html, offer, settings or Settings.from_env(os.environ)))
    else:
        out.append(f'  {"llm":<10} — (не спрашивали: добавьте --llm)')
    final = extract_price(page.html, offer, allow_free=section.allow_free, page_url=page.final_url)
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


async def _probe_model(html: str, offer: Offer, settings: Settings) -> str:
    # Оператору честно: с провайдером fake отвечает эвристика, а не ИИ. Настоящая модель —
    # без запасной эвристики, иначе сбой ключа выглядел бы как ответ модели.
    fake = settings.llm_provider is LLMProvider.FAKE
    llm: LLMClient = make_fake_llm() if fake else make_llm(settings)
    try:
        guess = await read_with_llm(html, offer, product_name='product on this page', llm=llm)
    finally:
        await llm.aclose()
    shown = _show(guess) if isinstance(guess, PriceReading) else f'✗ {guess.reason}: {guess.detail}'
    who = 'эвристика fake — не ИИ, в боевой запуск не попадает' if fake else f'модель {llm.model}'
    return f'  {"llm":<10} {shown} ({who}, токенов на входе {llm.usage.input_tokens})'


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
        config: Конфиг клиента — взять из него User-Agent, паузы, robots и сайты на паузе
            (и `.env` клиента рядом с ним).
        contact_email: Адрес для User-Agent, если конфига нет (или AUTOSTUDIO_PRICES_CONTACT_EMAIL).
        llm: Спросить и модель (платная ступень; с провайдером fake — эвристика).
    """
    offer = Offer(competitor='probe', url=url, selector=selector, attr=attr, currency=currency)
    if config is not None:
        load_sibling_env(config)
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

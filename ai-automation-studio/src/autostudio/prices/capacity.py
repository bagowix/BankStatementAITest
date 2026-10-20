"""Ёмкость расписания: сколько запросов получит каждый сайт и успевает ли проход (бриф 5.8).

Вежливость ограничивает скорость: к одному сайту — не чаще раза в `min_delay_s`, так что при
паузе 4–5 с сайт получает от нас не больше 720–900 запросов в час. Отсюда два вывода, которые
оператор должен увидеть до продажи тарифа, а не по жалобе клиента:

- 2 000 карточек одного магазина не проходят ни за час, ни за `max_run_minutes`: ежечасный
  Premium рассчитан на страницы категорий или фиды, а не на весь каталог одного домена;
- пауза меньше 4 с — это больше 900 запросов в час к чужому сайту: за такое банят, и за
  это же приходят претензии.

Оценка — нижняя граница: `запросы × min_delay_s` (страницы плюс robots.txt). Время ответа
сайта и `Crawl-delay` из его robots.txt (до 60 с) проход только удлиняют, а в конфиге их не
видно. Сайты на паузе не считаются: к ним запросов нет.
"""

import heapq
import math
from typing import Final

import httpx
import msgspec

from autostudio.prices.config import PARALLEL_SITES, PricesSection
from autostudio.prices.urls import origin

_HOUR_S: Final = 3600.0
# Бережный предел запросов к одному сайту в час: пауза 4 с (бриф 5.8: 720–900 при 4–5 с).
MAX_REQUESTS_PER_HOUR: Final = 900


class SiteLoad(msgspec.Struct, frozen=True, kw_only=True):
    """Нагрузка одного прохода на один сайт.

    Attributes:
        site: Адрес сайта (`https://shop.ge`).
        offers: Ссылок на этом сайте.
        requests: Запросов за проход: страницы и robots.txt.
        pass_s: Сколько длится проход по сайту, не меньше (секунды).
        per_hour: Сколько запросов сайт получает за самый загруженный час.
    """

    site: str
    offers: int
    requests: int
    pass_s: float
    per_hour: int


class Capacity(msgspec.Struct, frozen=True, kw_only=True):
    """Ёмкость конфига при данном расписании.

    Attributes:
        sites: Сайты по убыванию нагрузки.
        total_s: Весь проход: сайты параллельно (`PARALLEL_SITES`), страницы сайта — по очереди.
        interval_s: Интервал между проходами или None, если расписание неизвестно.
        min_every_hours: Самое частое расписание, при котором проходы не наслаиваются (часы).
        warnings: Что не сходится, с советом, как исправить.
    """

    sites: tuple[SiteLoad, ...]
    total_s: float
    interval_s: float | None
    min_every_hours: int
    warnings: tuple[str, ...]


def _site_loads(section: PricesSection) -> list[SiteLoad]:
    # Ключ сайта — тот же, что у пауз загрузчика и очередей монитора: scheme://host[:port].
    counts: dict[str, int] = {}
    for product in section.products:
        for offer in product.offers:
            if section.paused_site(offer.url) is None:
                site = origin(offer.url)
                counts[site] = counts.get(site, 0) + 1
    robots = 1 if section.respect_robots else 0
    delay = float(section.min_delay_s)
    cap = math.floor(_HOUR_S / delay)
    loads: list[SiteLoad] = []
    for site, offers in counts.items():
        requests = offers + robots
        loads.append(
            SiteLoad(site=site, offers=offers, requests=requests, pass_s=requests * delay, per_hour=min(requests, cap))
        )
    return loads


def _makespan(durations: list[float], workers: int) -> float:
    """Длина прохода, когда сайты берутся по порядку конфига на `workers` мест."""
    slots = [0.0] * workers
    for duration in durations:
        start = heapq.heappop(slots)
        heapq.heappush(slots, start + duration)
    return max(slots)


def duration_text(seconds: float) -> str:
    """Длительность для оператора: «40 с», «12 мин», «2.5 ч»."""
    if seconds < 120:
        return f'{seconds:.0f} с'
    if seconds < 2 * _HOUR_S:
        return f'{seconds / 60:.0f} мин'
    return f'{seconds / _HOUR_S:.1f} ч'


def _host(site: str) -> str:
    return httpx.URL(site).host


def capacity(section: PricesSection, *, every_hours: float | None = None) -> Capacity:
    """Считает нагрузку на сайты и проверяет, укладывается ли проход в расписание и пределы.

    Предупреждения:

    - сайт получит больше `MAX_REQUESTS_PER_HOUR` запросов в час (пауза меньше 4 с);
    - проход по сайту или весь проход длиннее `max_run_minutes`: хвост ссылок каждый раз
      будет «в этот раз не проверено»;
    - проход длиннее интервала расписания: следующий запуск застанет его незаконченным и
      будет пропущен.

    Args:
        section: Секция `[prices]` конфига.
        every_hours: Интервал между проходами (`run --every-hours`, таймер) или None.

    Returns:
        Нагрузка по сайтам, длина прохода и предупреждения.
    """
    loads = _site_loads(section)
    total = _makespan([load.pass_s for load in loads], PARALLEL_SITES) if loads else 0.0
    interval = every_hours * _HOUR_S if every_hours is not None else None
    run_limit = float(section.max_run_minutes) * 60
    delay = float(section.min_delay_s)
    advice = 'страницы категорий или фиды вместо карточек, меньше ссылок на этот сайт'
    warnings: list[str] = []
    for load in sorted(loads, key=lambda item: -item.requests):
        host = _host(load.site)
        if load.per_hour > MAX_REQUESTS_PER_HOUR:
            warnings.append(
                f'{host}: до {load.per_hour} запросов в час при паузе {delay:g} с — больше бережного предела '
                f'≈ 720–{MAX_REQUESTS_PER_HOUR} в час (пауза 4–5 с): поднимите min_delay_s до 4–5 с'
            )
        if load.pass_s > run_limit:
            fits = max(0, math.floor(run_limit / delay) - (load.requests - load.offers))
            warnings.append(
                f'{host}: проход по сайту ≈ {duration_text(load.pass_s)} — не уложится в max_run_minutes = '
                f'{section.max_run_minutes:g}: около {load.offers - fits} из {load.offers} ссылок каждый раз '
                f'будут «в этот раз не проверено». Нужно {advice}'
            )
        if interval is not None and load.pass_s > interval:
            warnings.append(
                f'{host}: проход по сайту ≈ {duration_text(load.pass_s)} — дольше интервала {every_hours:g} ч: '
                f'следующий запуск будет пропущен. Нужно {advice}, или реже: '
                f'--every-hours {math.ceil(load.pass_s / _HOUR_S)}'
            )
    slowest = max((load.pass_s for load in loads), default=0.0)
    # Весь проход — отдельное предупреждение, только если его не объясняет один сайт.
    if total > run_limit >= slowest:
        warnings.append(
            f'весь проход ≈ {duration_text(total)} (сайтов одновременно: {PARALLEL_SITES}) — не уложится в '
            f'max_run_minutes = {section.max_run_minutes:g}: поднимите предел или сократите ссылки'
        )
    if interval is not None and total > interval >= slowest:
        warnings.append(
            f'весь проход ≈ {duration_text(total)} (сайтов одновременно: {PARALLEL_SITES}) — дольше интервала '
            f'{every_hours:g} ч: --every-hours {math.ceil(total / _HOUR_S)} или меньше ссылок'
        )
    return Capacity(
        sites=tuple(sorted(loads, key=lambda item: (-item.requests, item.site))),
        total_s=total,
        interval_s=interval,
        min_every_hours=max(1, math.ceil(total / _HOUR_S)),
        warnings=tuple(warnings),
    )

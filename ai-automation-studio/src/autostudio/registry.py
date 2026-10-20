"""Каталог пакетов: что продаётся и где лежат их части.

Ядро не импортирует пакеты напрямую — только по строкам отсюда, лениво. Так
команда одного пакета не тянет зависимости остальных, а новый пакет добавляется
строкой в этот словарь.
"""

import importlib
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Final, cast

import msgspec

from autostudio.events import Event
from autostudio.report import MetricSpec

type Highlights = Callable[[Sequence[Event], str], list[str]]


class PackageInfo(msgspec.Struct, frozen=True, kw_only=True):
    """Где лежат части пакета.

    Attributes:
        name: Код пакета (`booking`) — он же префикс событий в журнале.
        title: Название для людей.
        cli: Путь `модуль:атрибут` к cyclopts-приложению пакета.
        metrics: Путь к списку `MetricSpec` для месячного отчёта.
        highlights: Путь к функции «главного за месяц» или None.
        demo: Путь к `async def run_demo(out_dir: Path) -> str` — офлайн-сценарий пакета.
        example: Путь к образцу конфига клиента относительно корня репозитория.
    """

    name: str
    title: str
    cli: str
    metrics: str
    highlights: str | None = None
    demo: str
    example: str


PACKAGES: Final[dict[str, PackageInfo]] = {
    'booking': PackageInfo(
        name='booking',
        title='Booking bot (Telegram)',
        cli='autostudio.booking.cli:app',
        metrics='autostudio.booking.metrics:METRICS',
        highlights='autostudio.booking.metrics:highlights',
        demo='autostudio.booking.demo:run_demo',
        example='examples/booking/client.toml',
    ),
    'prices': PackageInfo(
        name='prices',
        title='Competitor price monitor',
        cli='autostudio.prices.cli:app',
        metrics='autostudio.prices.metrics:METRICS',
        highlights='autostudio.prices.metrics:highlights',
        demo='autostudio.prices.demo:run_demo',
        example='examples/prices/client.toml',
    ),
    'inbox': PackageInfo(
        name='inbox',
        title='AI inbox triage',
        cli='autostudio.inbox.cli:app',
        metrics='autostudio.inbox.metrics:METRICS',
        highlights='autostudio.inbox.metrics:highlights',
        demo='autostudio.inbox.demo:run_demo',
        example='examples/inbox/client.toml',
    ),
}


def resolve(path: str) -> object:
    """Импортирует `модуль:атрибут`."""
    module_name, _, attr = path.partition(':')
    return getattr(importlib.import_module(module_name), attr)


def metrics_for(info: PackageInfo) -> list[MetricSpec]:
    """Подписи событий пакета для отчёта."""
    return list(cast(Sequence[MetricSpec], resolve(info.metrics)))


type DemoRunner = Callable[[Path], Coroutine[None, None, str]]


def demo_for(info: PackageInfo) -> DemoRunner:
    """Офлайн-демо пакета: без ключей, без сети, с заглушкой модели."""
    return cast(DemoRunner, resolve(info.demo))


def highlights_for(info: PackageInfo) -> Highlights | None:
    """Функция «главного за месяц» пакета, если она есть."""
    return None if info.highlights is None else cast(Highlights, resolve(info.highlights))

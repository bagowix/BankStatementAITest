"""Зависимости монитора цен (dishka) поверх `CoreProvider`.

Ничего не строится при импорте: контейнер собирает команда `autostudio prices run`
(или тест), отдавая в контекст `Settings` и `PricesConfig`.
"""

import httpx
from dishka import Provider, Scope, from_context, provide

from autostudio.clientconf import client_data_dir
from autostudio.events import EventLog
from autostudio.llm import make_llm
from autostudio.notify import make_notifier
from autostudio.prices.config import PricesConfig
from autostudio.prices.fetch import FetchSettings, PoliteFetcher, Resolver, system_resolver
from autostudio.prices.heuristics import make_fake_llm
from autostudio.prices.monitor import Fetcher, PricesDeps
from autostudio.prices.store import PriceStore
from autostudio.providers import OperatorAlerts
from autostudio.settings import Settings
from autostudio.sinks import make_sink


class PricesProvider(Provider):
    """Хранилище, журнал, таблица, уведомления, модель и фабрика загрузчиков.

    Args:
        resolver: DNS-резолвер загрузчика (тесты подставляют словарь вместо сети).
    """

    scope = Scope.APP

    config = from_context(provides=PricesConfig, scope=Scope.APP)

    def __init__(self, resolver: Resolver = system_resolver) -> None:
        super().__init__()
        self._resolver = resolver

    @provide
    def deps(
        self, config: PricesConfig, settings: Settings, http: httpx.AsyncClient, operator: OperatorAlerts
    ) -> PricesDeps:
        base = client_data_dir(settings.data_dir, config.client)
        fetch_settings = FetchSettings.from_section(config.prices)
        resolver = self._resolver

        def new_fetcher() -> Fetcher:
            return PoliteFetcher(http, fetch_settings, resolver=resolver)

        return PricesDeps(
            new_fetcher=new_fetcher,
            store=PriceStore(base / 'prices.sqlite'),
            # Тот же файл, что читает `autostudio report`: отчёт клиенту строится из него.
            events=EventLog(base / 'events.sqlite'),
            sink=make_sink(config.sink),
            notifier=make_notifier(config.notify, http),
            operator=operator.notifier,
            # Без платной ступени модель не создаётся вовсе — и ключ не нужен.
            llm=make_llm(settings, fake=make_fake_llm()) if config.prices.llm_fallback else None,
        )

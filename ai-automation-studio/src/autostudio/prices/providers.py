"""Зависимости монитора цен (dishka) поверх `CoreProvider`.

Ничего не строится при импорте: контейнер собирает команда `autostudio prices run`
(или тест), отдавая в контекст `Settings` и `PricesConfig`.
"""

import logging
from collections.abc import AsyncGenerator

import httpx
from dishka import Provider, Scope, from_context, provide

from autostudio.clientconf import client_data_dir, events_path
from autostudio.events import EventLog
from autostudio.llm import LLMClient, make_llm
from autostudio.notify import make_notifier
from autostudio.prices.config import PricesConfig
from autostudio.prices.fetch import FetchSettings, PoliteFetcher, Resolver, system_resolver
from autostudio.prices.monitor import Fetcher, PricesDeps
from autostudio.prices.store import PriceStore
from autostudio.providers import OperatorAlerts
from autostudio.settings import LLMProvider, Settings
from autostudio.sinks import make_sink

logger = logging.getLogger(__name__)


def price_model(settings: Settings, config: PricesConfig) -> LLMClient | None:
    """Модель для страниц без разметки — или None, если ступень выключена.

    Эвристика `FakeLLM` (первое число с валютой на странице) в боевой запуск не попадает
    никогда: в таблице клиента её ответ выглядел бы «ИИ, проверенная цитата», а это часто
    порог бесплатной доставки или зачёркнутая старая цена. По той же причине настоящая модель
    подключается без запасных эвристик — `make_llm(settings)` без `fake`, а не `ResilientLLM`:
    сбой провайдера — это `llm_outage`/`llm_config`/`llm_error` по `LLMError.kind`, видимый
    оператору, а клиенту — «в этот раз не проверено», а не тихая подмена догадкой.

    Args:
        settings: Настройки процесса.
        config: Конфиг клиента.

    Returns:
        Клиент модели или None.
    """
    if not config.prices.llm_fallback:
        return None
    if settings.llm_provider is LLMProvider.FAKE:
        logger.warning(
            'prices %s: llm_fallback включён, но AUTOSTUDIO_LLM_PROVIDER=fake — ступень модели выключена',
            config.client.id,
        )
        return None
    return make_llm(settings)


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
    async def deps(
        self, config: PricesConfig, settings: Settings, http: httpx.AsyncClient, operator: OperatorAlerts
    ) -> AsyncGenerator[PricesDeps]:
        base = client_data_dir(settings.data_dir, config.client)
        fetch_settings = FetchSettings.from_section(config.prices)
        resolver = self._resolver

        def new_fetcher() -> Fetcher:
            return PoliteFetcher(http, fetch_settings, resolver=resolver)

        llm = price_model(settings, config)
        try:
            yield PricesDeps(
                new_fetcher=new_fetcher,
                store=PriceStore(base / 'prices.sqlite'),
                # Тот же файл, что читает `autostudio report`: отчёт клиенту строится из него.
                events=EventLog(events_path(settings.data_dir, config.client)),
                sink=make_sink(config.sink),
                notifier=make_notifier(config.notify, http),
                operator=operator.notifier,
                llm=llm,
            )
        finally:
            # HTTP-клиент SDK закрывает тот, кто его создал (контракт `LLMClient.aclose`).
            if llm is not None:
                await llm.aclose()

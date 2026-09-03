"""Брокер задач и выбор режима работы.

Два режима, и переключение между ними — одна переменная окружения:

* без `BANKSTATEMENT_REDIS` всё живёт в одном процессе: брокер в памяти,
  состояние в словаре. Стенд запускается одной командой и ничего не требует;
* с `BANKSTATEMENT_REDIS` очередь и состояние уезжают в Redis, а разбор ведёт
  отдельный процесс `taskiq worker`. Тогда рестарт API не теряет работу, а
  воркеров можно поставить сколько нужно.

Taskiq, а не Celery: у Celery синхронное ядро и конфигурация, унаследованная из
времён до asyncio, — рядом с ASGI-приложением это лишний слой. Нам нужна ровно очередь задач
с результатом и адресом, по которому о задаче можно спросить.
"""

import os
from typing import Final

from taskiq import AsyncBroker, InMemoryBroker

from bankstatement.logs import configure_logging
from bankstatement.worker.store import JobStore, MemoryStore

__all__ = ['IDLE_TIMEOUT_MS', 'REDIS_URL_ENV', 'XREAD_BLOCK_MS', 'broker', 'configure_logging', 'redis_url', 'store']

REDIS_URL_ENV: Final[str] = 'BANKSTATEMENT_REDIS'

XREAD_BLOCK_MS: Final[int] = 2000
"""Насколько воркер блокируется в ожидании задачи. Две секунды — компромисс:
достаточно редко, чтобы не молотить сеть вхолостую, и достаточно часто, чтобы
остановка воркера не повисала."""

IDLE_TIMEOUT_MS: Final[int] = 30 * 60 * 1000
"""Через сколько неподтверждённый документ забирает себе другой воркер.

Отсчёт идёт от выдачи, а не от последней активности, поэтому порог обязан быть
больше самого долгого законного разбора: иначе живой воркер, занятый большой
подшивкой, потеряет её на полпути в пользу второго. Полчаса против двух минут на
99 страницах — запас пятнадцатикратный. Умножение работы при этом не портит
результат: воркеры пишут в одну и ту же запись, побеждает закончивший последним."""


def redis_url() -> str | None:
    return os.environ.get(REDIS_URL_ENV) or None


def build_broker() -> AsyncBroker:
    url = redis_url()

    if url is None:
        return InMemoryBroker()

    from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

    # Стрим, а не список: у стрима есть подтверждение после выполнения, и
    # документ, на котором воркер умер, вернётся другому вместо того, чтобы
    # исчезнуть. Разбор идёт минутами, так что случай не гипотетический.
    #
    # Оговорка, установленная опытом, а не документацией: перевыдачу taskiq-redis
    # делает в `listen()` после успешного чтения новых сообщений, а на пустой
    # выборке цикл уходит на следующий круг раньше. Значит неподтверждённый
    # документ вернётся не по истечении срока, а когда в очередь придёт следующая
    # задача. Для стенда это приемлемо, для настоящей очереди нужен свой обход
    # зависших — но обещать самоисцеление, которого нет, нельзя.
    #
    # socket_timeout заведомо больше блокирующего чтения: иначе клиент рвёт
    # соединение ровно на ожидании следующей задачи, и воркер падает на старте.
    # Тип объявлен явно: без установленной группы `queue` mypy видит здесь Any, и
    # возврат Any из функции с объявленным типом он справедливо запрещает.
    stream: AsyncBroker = RedisStreamBroker(
        url,
        xread_block=XREAD_BLOCK_MS,
        idle_timeout=IDLE_TIMEOUT_MS,
        socket_timeout=XREAD_BLOCK_MS / 1000 * 3,
        retry_on_timeout=True,
        health_check_interval=30,
    ).with_result_backend(RedisAsyncResultBackend(url))

    return stream


def build_store() -> JobStore:
    url = redis_url()

    if url is None:
        return MemoryStore()

    import redis

    from bankstatement.worker.store import RedisStore

    return RedisStore(redis.Redis.from_url(url))


# Единственные объекты в проекте, которые собираются на импорте. Это не выбор:
# `taskiq worker` принимает адресом брокера именно `модуль:имя` и создаёт его
# сам, до нашего кода. Остальное строится фабриками — см. `web.factory`.
broker: Final[AsyncBroker] = build_broker()
store: Final[JobStore] = build_store()

"""Выбор режима: одна переменная окружения — две разные системы.

Проверяется именно развилка, а не Redis: клиенты создаются лениво, поэтому
адрес может указывать в никуда — соединение никто не открывает.
"""

import logging

import pytest

from bankstatement.worker import broker as module
from bankstatement.worker.store import MemoryStore, RedisStore

FAKE_URL = 'redis://localhost:63999/0'


def test__build__without_the_variable__everything_stays_in_this_process(monkeypatch):
    monkeypatch.delenv(module.REDIS_URL_ENV, raising=False)

    assert type(module.build_broker()).__name__ == 'InMemoryBroker'
    assert isinstance(module.build_store(), MemoryStore)


def test__build__with_the_variable__queue_and_shared_state(monkeypatch):
    """Стрим, а не список: у стрима есть подтверждение, и умерший воркер не теряет документ."""
    pytest.importorskip('taskiq_redis', reason='нужны зависимости группы queue')
    monkeypatch.setenv(module.REDIS_URL_ENV, FAKE_URL)

    assert type(module.build_broker()).__name__ == 'RedisStreamBroker'
    assert isinstance(module.build_store(), RedisStore)


def test__redis_url__empty_value__counts_as_absent(monkeypatch):
    """Пустая переменная в окружении — обычное дело, и она не должна включать Redis."""
    monkeypatch.setenv(module.REDIS_URL_ENV, '')

    assert module.redis_url() is None


def test__configure_logging__nothing_configured__our_logger_starts_talking():
    """Воркер запускает не наш код, и `--log-level` у taskiq трогает только его логгеры."""
    ours = logging.getLogger('bankstatement')
    root = logging.getLogger()
    kept, kept_root = ours.handlers[:], root.handlers[:]
    ours.handlers.clear()
    root.handlers.clear()

    try:
        module.configure_logging()

        assert ours.handlers
        assert ours.level == logging.INFO
    finally:
        ours.handlers[:] = kept
        root.handlers[:] = kept_root


def test__configure_logging__host_already_configured__left_alone():
    """Для библиотеки перебить настройку хоста недопустимо."""
    ours = logging.getLogger('bankstatement')
    kept = ours.handlers[:]
    ours.handlers.clear()
    logging.getLogger().addHandler(logging.NullHandler())

    try:
        module.configure_logging()

        assert not ours.handlers
    finally:
        ours.handlers[:] = kept
        logging.getLogger().handlers.pop()

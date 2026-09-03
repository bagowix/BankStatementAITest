"""Фабрика: приложение собирается функцией, а не импортом.

Ради этого рефакторинг и делался — здесь это проверяется буквально: два
приложения с разными настройками в одном процессе, и подменённая зависимость
без единого monkeypatch.
"""

import pytest

pytest.importorskip('fastapi', reason='нужны зависимости группы ui')

from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.fastapi import FastapiProvider
from fastapi.testclient import TestClient

from bankstatement.web.factory import create_app, create_container
from bankstatement.web.jobs import JobRegistry, QueueDispatcher, ThreadDispatcher
from bankstatement.web.providers import AppProvider, Samples
from bankstatement.web.settings import Settings
from bankstatement.worker.store import JobStore, MemoryStore

pytestmark = pytest.mark.ui


def test__create_app__two_apps_with_different_settings__coexist(tmp_path):
    """Один процесс, две конфигурации: на модульных синглтонах это невозможно."""
    first = Settings(samples_dir=str(tmp_path / 'a'), max_batch=3)
    second = Settings(samples_dir=str(tmp_path / 'b'), max_batch=7)

    with TestClient(create_app(first)) as left, TestClient(create_app(second)) as right:
        assert left.get('/health').status_code == 200
        assert right.get('/health').status_code == 200


def test__create_container__without_redis__memory_store_and_thread_dispatch():
    container = create_container(Settings())

    store = _sync_get(container, JobStore)
    registry = _sync_get(container, JobRegistry)

    assert isinstance(store, MemoryStore)
    assert isinstance(registry._dispatch, ThreadDispatcher)


def test__create_container__with_redis__queue_dispatch():
    """Режим выбирается настройками, а не тем, какой модуль импортировали раньше."""
    container = create_container(Settings(redis_url='redis://localhost:6399'))

    registry = _sync_get(container, JobRegistry)

    assert isinstance(registry._dispatch, QueueDispatcher)


def test__samples__name_outside_the_list__not_resolved(tmp_path):
    """Имя из формы сверяется со списком, а не подставляется в путь."""
    (tmp_path / 'выписка.pdf').write_bytes(b'%PDF-1.7')
    samples = Samples(tmp_path)

    assert samples.resolve('выписка.pdf') is not None
    assert samples.resolve('../../etc/passwd') is None
    assert samples.names() == ['выписка.pdf']


def test__create_app__container_overridden__own_dependency_is_used(tmp_path):
    """Тест подменяет зависимость контейнером, а не атрибутом модуля."""
    marker = JobRegistry(store=MemoryStore(), dispatch=lambda job: None)

    class OverrideProvider(Provider):
        scope = Scope.APP

        @provide
        def registry(self) -> JobRegistry:
            return marker

    settings = Settings(samples_dir=str(tmp_path))
    container = make_async_container(
        AppProvider(),
        OverrideProvider(),
        FastapiProvider(),
        context={Settings: settings},
    )

    with TestClient(create_app(settings, container)) as client:
        assert client.get('/metrics').status_code == 200

    assert _sync_get(container, JobRegistry) is marker


def test__app_module__entry_point_for_uvicorn__serves():
    """`uvicorn bankstatement.web.app:app` — то, чем это запускают в контейнере.

    Модуль состоит из одного вызова фабрики, и проверять там нечего, кроме
    самого главного: что после импорта приложение поднимается и отвечает.
    """
    from bankstatement.web.app import app

    with TestClient(app) as client:
        assert client.get('/health').status_code == 200


def _sync_get(container, kind):
    import asyncio

    return asyncio.run(container.get(kind))

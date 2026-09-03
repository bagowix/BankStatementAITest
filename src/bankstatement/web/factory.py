"""Фабрика приложения.

Приложение собирается функцией, а не появляется побочным эффектом импорта. Из
этого следует всё остальное: два приложения с разными настройками в одном
процессе, тест без подмены атрибутов модуля, честный порядок инициализации.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from dishka import AsyncContainer, make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from bankstatement.web.providers import STATIC_DIR, AppProvider
from bankstatement.web.routes import router
from bankstatement.web.settings import Settings

log = logging.getLogger(__name__)

BODY_SLACK: Final[int] = 1024 * 1024
"""Запас на служебные части multipart сверх суммы файлов."""


def create_container(settings: Settings) -> AsyncContainer:
    """Контейнер приложения. Настройки приходят контекстом, остальное — из них."""
    return make_async_container(AppProvider(), FastapiProvider(), context={Settings: settings})


def create_app(settings: Settings | None = None, container: AsyncContainer | None = None) -> FastAPI:
    """Собрать приложение.

    Оба аргумента необязательны и оба переопределяемы: в проде не передают
    ничего, в тесте передают контейнер с подменённым хранилищем.
    """
    if settings is None:
        settings = Settings.from_env()

    if container is None:
        container = create_container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info(
            'стенд запущен: режим %s',
            'очередь и отдельный воркер' if settings.redis_url else 'один процесс',
        )

        yield

        await container.close()

    app = FastAPI(
        title='Bank Statement Extraction',
        docs_url='/api',
        lifespan=lifespan,
    )
    app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
    app.include_router(router)
    setup_dishka(container, app)

    limit = settings.max_upload * settings.max_batch + BODY_SLACK

    @app.middleware('http')
    async def reject_oversized_body(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Отказать по заявленной длине тела до того, как оно будет прочитано.

        Проверки размера в обработчике идут после разбора multipart, то есть
        после того, как тело целиком легло во временные файлы. Заявленная длина
        известна заранее, и тело, которое не пройдёт ни при каком раскладе,
        отбивается здесь, не тронув диск.
        """
        declared = request.headers.get('content-length', '')

        if request.method == 'POST' and declared.isdigit() and int(declared) > limit:
            return JSONResponse({'detail': 'тело запроса больше допустимого'}, status_code=413)

        return await call_next(request)

    return app

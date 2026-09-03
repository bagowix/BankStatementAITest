"""Ручки стенда.

Всё, что нужно обработчику, приходит параметром: реестр, шаблоны, настройки.
Модульных синглтонов здесь нет — иначе тест приходилось бы писать не про
поведение, а про подмену атрибутов.
"""

import re
from pathlib import Path
from typing import Annotated, Final
from uuid import uuid4

import msgspec
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

from bankstatement.ingest import default_workers
from bankstatement.models import ReconStatus
from bankstatement.web.jobs import JobRegistry
from bankstatement.web.limits import TokenBucket
from bankstatement.web.preview import crop
from bankstatement.web.providers import Samples, Uploads
from bankstatement.web.settings import Settings
from bankstatement.worker.store import JobRecord, JobState

router = APIRouter()

PDF_MAGIC: Final[bytes] = b'%PDF'
CHUNK: Final[int] = 1024 * 1024
SAFE_NAME: Final[re.Pattern[str]] = re.compile(r'[^\w .()\[\]-]+', re.UNICODE)
NAME_LIMIT: Final[int] = 80


@router.get('/', response_class=HTMLResponse)
@inject
def index(
    request: Request,
    templates: FromDishka[Jinja2Templates],
    registry: FromDishka[JobRegistry],
    samples: FromDishka[Samples],
) -> Response:
    return templates.TemplateResponse(
        request,
        'index.html',
        {'samples': samples.names(), 'jobs': registry.recent()[:6]},
    )


@router.post('/jobs', response_class=HTMLResponse)
@inject
def create_job(
    request: Request,
    templates: FromDishka[Jinja2Templates],
    registry: FromDishka[JobRegistry],
    samples: FromDishka[Samples],
    uploads: FromDishka[Uploads],
    limiter: FromDishka[TokenBucket],
    settings: FromDishka[Settings],
    files: Annotated[list[UploadFile] | None, Form()] = None,
    sample: Annotated[str | None, Form()] = None,
) -> Response:
    """Принять пачку файлов или выбранный образец и запустить разбор в фоне.

    Пачка — обычный режим работы аудитора: выписки приходят папкой за квартал.
    Разбираются они по одному (каждый и так занимает все ядра), но встают в
    очередь одним движением.

    Обработчик синхронный намеренно: приём файла читает до ста двадцати восьми
    мегабайт блокирующим чтением, и в корутине это остановило бы весь сервер.
    Обычный `def` FastAPI выполняет в пуле потоков.
    """
    if not limiter.take(client_key(request, trusted_proxy=settings.trusted_proxy)):
        raise HTTPException(status_code=429, detail='слишком много загрузок подряд, подождите минуту')

    if sample:
        path = samples.resolve(sample)

        if path is None:
            raise HTTPException(status_code=404, detail='образец не найден')

        jobs = [registry.submit(sample, path)]
    else:
        incoming = [f for f in (files or []) if f.filename]

        if not incoming:
            raise HTTPException(status_code=400, detail='нужен файл или образец')

        if len(incoming) > settings.max_batch:
            raise HTTPException(
                status_code=413,
                detail=f'за раз можно загрузить не больше {settings.max_batch} файлов',
            )

        # Пачка принимается целиком или не принимается вовсе: отказ на пятом
        # файле с четырьмя уже поставленными задачами и кодом ошибки в ответе
        # заставил бы клиента гадать, что из отправленного всё-таки взято.
        stored: list[tuple[str, Path]] = []

        try:
            for f in incoming:
                stored.append((f.filename or 'statement.pdf', store_upload(f, uploads, settings)))
        except Exception:
            for _, path in stored:
                path.unlink(missing_ok=True)

            raise

        jobs = [registry.submit(name, path, uploaded=True) for name, path in stored]

    return templates.TemplateResponse(
        request,
        'fragments/started.html',
        {'jobs': jobs, 'job': jobs[0], 'fresh': [], 'shown': 0},
    )


@router.get('/health')
@inject
def health(samples: FromDishka[Samples], settings: FromDishka[Settings]) -> JSONResponse:
    """Живость для деплоя: отвечает, пока процесс способен принимать работу.

    Режим назван явно: стенд, запущенный без `BANKSTATEMENT_REDIS`, разбирает
    документы сам, и воркер рядом с ним молчит не потому, что сломан.
    """
    return JSONResponse(
        {
            'status': 'ok',
            'mode': 'queue' if settings.redis_url else 'inline',
            'workers': default_workers(),
            'samples': len(samples.names()),
        }
    )


@router.get('/metrics', response_class=PlainTextResponse)
@inject
def metrics(registry: FromDishka[JobRegistry]) -> str:
    """Счётчики в формате Prometheus — без зависимости на клиент.

    Для продукта, где качество измеряется долей сведённых периодов, эта доля и
    есть главная метрика: без неё в проде не видно, стало лучше или хуже.
    """
    counters = registry.stats()
    lines = [
        ('bankstatement_documents_submitted_total', counters.submitted),
        ('bankstatement_documents_reused_total', counters.reused),
        ('bankstatement_documents_done_total', counters.done),
        ('bankstatement_documents_failed_total', counters.failed),
        ('bankstatement_documents_running', counters.running),
        ('bankstatement_periods_total', counters.periods),
        ('bankstatement_periods_reconciled_total', counters.reconciled),
    ]

    return '\n'.join(f'{name} {value}' for name, value in lines) + '\n'


@router.get('/jobs/{job_id}/status', response_class=HTMLResponse)
@inject
def job_status(
    request: Request,
    job_id: str,
    templates: FromDishka[Jinja2Templates],
    registry: FromDishka[JobRegistry],
    shown: int = 0,
) -> Response:
    """Полоса выполнения и выписки, которых ещё нет на экране.

    Клиент сообщает, сколько карточек уже показано, и получает только новые.
    Перерисовывать показанные нельзя: у раскрытой таблицы операций тогда
    захлопывается спойлер прямо под руками у того, кто её читает.
    """
    job = require(registry, job_id)
    fresh = job.ready[shown:]

    if job.state in (JobState.QUEUED, JobState.RUNNING):
        return templates.TemplateResponse(
            request,
            'fragments/progress.html',
            {'job': job, 'fresh': fresh, 'shown': shown + len(fresh)},
        )

    context = result_context(job)
    context['fresh'] = fresh

    return templates.TemplateResponse(request, 'fragments/result.html', context)


@router.get('/jobs/{job_id}', response_class=HTMLResponse)
@inject
def job_page(
    request: Request,
    job_id: str,
    templates: FromDishka[Jinja2Templates],
    registry: FromDishka[JobRegistry],
) -> Response:
    return templates.TemplateResponse(request, 'job.html', result_context(require(registry, job_id)))


@router.get('/jobs/{job_id}/json')
@inject
def job_json(job_id: str, registry: FromDishka[JobRegistry]) -> Response:
    job = require(registry, job_id)

    if job.result is None:
        raise HTTPException(status_code=409, detail='разбор ещё не готов')

    body = msgspec.json.format(msgspec.json.encode(job.result.to_payload()), indent=2)

    return Response(
        content=body,
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{safe_filename(job.name)}.json"'},
    )


@router.get('/jobs/{job_id}/crop')
@inject
def job_crop(
    job_id: str,
    # Нумерация с единицы — как её видит человек. Без нижней границы ноль и
    # минус ушли бы в PyMuPDF как индексы с конца, и вырезка молча пришла бы
    # с другой страницы вместо отказа.
    page: Annotated[int, Query(ge=1)],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    registry: FromDishka[JobRegistry],
) -> Response:
    """Вырезка из документа вокруг строки — провенанс, который видно."""
    job = require(registry, job_id)

    try:
        image = crop(job.file, page - 1, (x0, y0, x1, y1))
    except (IndexError, ValueError) as error:
        raise HTTPException(status_code=404, detail='страница не найдена') from error

    return Response(content=image, media_type='image/png', headers={'Cache-Control': 'max-age=3600'})


@router.get('/api/jobs/{job_id}')
@inject
def job_api(job_id: str, registry: FromDishka[JobRegistry]) -> JSONResponse:
    """Машинный вид того же состояния — стенд заодно работает как API."""
    job = require(registry, job_id)

    return JSONResponse(
        {
            'id': job.id,
            'name': job.name,
            'state': job.state.value,
            'phase': job.phase.value,
            'percent': job.percent,
            'pages_done': job.pages_done,
            'pages_total': job.pages_total,
            'elapsed': round(job.elapsed, 1),
            'error': job.error or None,
            'stalled': job.stalled,
            'periods_ready': len(job.ready),
            'result': job.result.to_payload() if job.result else None,
        }
    )


def client_key(request: Request, trusted_proxy: bool = False) -> str:
    """Ключ ограничения для rate-limiting.

    За обратным прокси реальный адрес приходит заголовком X-Forwarded-For,
    но этот заголовок может быть подменён клиентом. Доверять ему только если
    стенд явно настроен на работу за доверенным прокси (nginx, ingress).
    """
    if trusted_proxy:
        forwarded = request.headers.get('x-forwarded-for', '')

        # Последний адрес в списке дописал сам доверенный прокси; всё левее
        # пришло от клиента, и подставить туда можно что угодно.
        if forwarded:
            return forwarded.split(',')[-1].strip()

    return request.client.host if request.client else 'unknown'


def store_upload(file: UploadFile, uploads: Uploads, settings: Settings) -> Path:
    """Сохранить загруженный файл, проверив его на входе.

    Проверки делаются здесь, а не в фоне: пользователю, выбравшему не тот файл,
    нужен внятный ответ сразу, а не задача, которая через две секунды упадёт с
    внутренней ошибкой библиотеки.
    """
    path = uploads.path_for(f'{uuid4().hex}.pdf')
    size = 0

    try:
        with path.open('wb') as target:
            while chunk := file.file.read(CHUNK):
                if not size and not chunk.startswith(PDF_MAGIC):
                    raise HTTPException(status_code=415, detail='это не PDF: файл не начинается с %PDF')

                size += len(chunk)

                if size > settings.max_upload:
                    limit = settings.max_upload // 1024 // 1024

                    raise HTTPException(status_code=413, detail=f'файл больше {limit} МБ')

                target.write(chunk)
    except HTTPException:
        path.unlink(missing_ok=True)

        raise

    if not size:
        path.unlink(missing_ok=True)

        raise HTTPException(status_code=400, detail='файл пустой')

    return path


def safe_filename(name: str) -> str:
    """Имя для заголовка Content-Disposition.

    Имя приходит от пользователя вместе с файлом. Кавычка или перевод строки в
    нём разрывают заголовок и позволяют подставить свой — поэтому в имени
    остаются только буквы, цифры и несколько безобидных знаков.
    """
    cleaned = SAFE_NAME.sub('_', Path(name).stem).strip('._ ')

    return cleaned[:NAME_LIMIT] or 'statement'


def require(registry: JobRegistry, job_id: str) -> JobRecord:
    job = registry.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail='разбор не найден')

    return job


def result_context(job: JobRecord) -> dict[str, object]:
    """Что показывать по задаче — готовой или ещё идущей.

    Пока результата нет, показываются уже сведённые выписки: ссылку на разбор
    открывают и в середине работы, и пустая страница в этот момент выглядит как
    поломка, хотя половина документа уже разобрана.
    """
    result = job.result
    periods = result.periods if result else job.ready

    return {
        'job': job,
        'result': result,
        'periods': periods,
        'reconciled': sum(1 for p in periods if p.reconciliation.status is ReconStatus.RECONCILED),
        'shown': len(periods),
        'fresh': [],
    }

"""Задача разбора — то, что выполняет воркер.

Разбор синхронный и занимает все ядра: внутри уже работает пул потоков на
страницы. Поэтому задача объявлена обычной функцией — taskiq выполнит её в своём
пуле и не заблокирует цикл событий. Для отдельного процесса воркера правильный
запуск — `taskiq worker ... --use-process-pool`.

Аргумент у задачи один — идентификатор. Всё остальное воркер берёт из общего
хранилища и туда же пишет ход работы: гонять состояние через тело сообщения
значило бы получить две его копии, которые разойдутся.
"""

import logging
import time
from datetime import UTC, datetime

from bankstatement.extract import extract_result
from bankstatement.ingest import Progress, default_workers
from bankstatement.web.settings import Settings
from bankstatement.worker.broker import broker, configure_logging, store
from bankstatement.worker.store import JobRecord, JobState

log = logging.getLogger(__name__)

configure_logging()


@broker.task(task_name='bankstatement.extract')
def extract_document(job_id: str) -> str:
    """Разобрать документ и записать результат в общее хранилище."""
    job = store.get(job_id)

    if job is None:
        log.warning('задача %s исчезла из хранилища до начала работы', job_id)

        return job_id

    # Пределы воркер берёт из окружения, как и адрес Redis: у него нет
    # контейнера стенда, а настройки у обоих процессов должны совпадать.
    limits = Settings.from_env()
    run(job, max_pages=limits.max_pages, timeout=limits.document_timeout)

    return job_id


def run(job: JobRecord, *, max_pages: int | None = None, timeout: float | None = None) -> None:
    """Выполнить разбор, отражая ход работы в хранилище.

    Прогресс и готовые периоды пишутся по ходу дела: браузер спрашивает о
    состоянии постоянно, и ему нужно свежее, а не итоговое.

    Args:
        job: Запись задачи.
        max_pages: Отказать документу длиннее этого.
        timeout: Секунд на документ; по истечении отдаются готовые выписки, и
            результат помечен неполным.
    """

    def progress(update: Progress) -> None:
        job.pages_done = update.done
        job.pages_in_flight = update.in_flight
        job.pages_total = update.total
        job.engine = update.engine
        _save(job, force=False)

    def period_ready(period: object) -> None:
        job.ready.append(period)  # type: ignore[arg-type]
        _save(job)

    job.state = JobState.RUNNING
    _save(job)
    log.info('разбор начат: %s (потоков %d)', job.name, default_workers())

    try:
        deadline = time.monotonic() + timeout if timeout else None
        job.result = extract_result(
            job.file, on_page=progress, on_period=period_ready, max_pages=max_pages, deadline=deadline
        )
        job.state = JobState.DONE

        if job.result is not None:
            log.info(
                'разбор завершён: %s — периодов %d, сведено %d, %.1f с',
                job.name,
                len(job.result.periods),
                job.result.reconciled_periods,
                job.elapsed,
            )
    except Exception as error:  # интерфейс должен показать причину, а не пустую страницу
        log.exception('разбор %s не выполнен', job.name)
        job.error = readable(error, job)
        job.state = JobState.FAILED
    finally:
        job.finished = datetime.now(UTC)
        _save(job)


def _save(job: JobRecord, *, force: bool = True) -> None:
    """Записать состояние. Хранилище в памяти порог частоты игнорирует.

    Заодно это и отметка живости: пишется она на каждом сообщении о ходе работы,
    то есть на каждой прочитанной странице. Отдельного сердцебиения не нужно —
    задача, которой есть что сообщить, тем самым и сообщает, что жива.
    """
    if job.state is JobState.RUNNING:
        job.heartbeat = datetime.now(UTC)

    store.put(job, force=force)


def readable(error: Exception, job: JobRecord) -> str:
    """Сообщение для интерфейса без внутренних путей.

    Полный след уходит в лог сервера: имя временного файла на диске — это
    подробность реализации, которой в браузере делать нечего.
    """
    text = str(error).replace(job.path, job.name)

    return f'{type(error).__name__}: {text}' if text else type(error).__name__

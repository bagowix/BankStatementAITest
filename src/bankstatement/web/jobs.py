"""Постановка документов в работу.

Реестр знает, куда положить состояние (`JobStore`) и как запустить разбор
(`dispatch`), но не знает, где этот разбор произойдёт. Отсюда два режима без
единого ветвления выше по стеку: поток внутри процесса — или очередь taskiq и
отдельный воркер.

Три вещи здесь не про удобство, а про выживание под нагрузкой: одинаковый файл
не разбирается дважды, число одновременных разборов ограничено, и на каждый
разбор ведётся счётчик для метрик.
"""

import asyncio
import hashlib
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bankstatement.worker.store import JobRecord, JobState, JobStore, MemoryStore

log = logging.getLogger(__name__)

MAX_JOBS: Final[int] = 24
"""Сколько разборов держать под рукой. Старые вытесняются: стенд рассчитан на
показ, а не на хранение, и неограниченный рост здесь был бы утечкой."""

MAX_CONCURRENT: Final[int] = 2
"""Сколько документов разбирать одновременно в режиме одного процесса.

Каждый разбор сам по себе занимает все ядра, поэтому параллелить документы почти
бесполезно, а память они тратят кратно. Ограничение превращает наплыв в очередь
вместо отказа по памяти. В режиме очереди то же делает число воркеров."""

CHUNK: Final[int] = 1024 * 1024


def digest_of(path: Path) -> str:
    """SHA-256 файла — ключ, по которому один документ не разбирается дважды."""
    checksum = hashlib.sha256()

    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b''):
            checksum.update(chunk)

    return checksum.hexdigest()


@dataclass(frozen=True, slots=True)
class Stats:
    """Счётчики для метрик и для ответа на вопрос «как оно вообще работает»."""

    submitted: int
    reused: int
    done: int
    failed: int
    running: int
    periods: int
    reconciled: int


class JobRegistry:
    """Приём документов: дедупликация, постановка в работу, счётчики."""

    def __init__(
        self,
        store: JobStore | None = None,
        dispatch: Callable[[JobRecord], None] | None = None,
        max_jobs: int = MAX_JOBS,
        concurrency: int = MAX_CONCURRENT,
        uploads_dir: Path | None = None,
        upload_ttl: float | None = None,
    ) -> None:
        self.store = store if store is not None else MemoryStore()
        self.max_jobs = max_jobs
        self._dispatch = dispatch if dispatch is not None else ThreadDispatcher(concurrency)
        self._lock = threading.Lock()
        self._uploads_dir = uploads_dir
        self._upload_ttl = upload_ttl if upload_ttl is not None else 24 * 3600.0

    def _cleanup_old_uploads(self) -> None:
        """Удалить загруженные файлы, превысившие срок жизни.

        Минимальная политика хранения: файлы удаляются при постановке новой задачи,
        если они старше upload_ttl. Без этого папка uploads растёт без предела.
        """
        if not self._uploads_dir or not self._uploads_dir.is_dir():
            return

        now = time.time()

        try:
            for file_path in self._uploads_dir.glob('*.pdf'):
                mtime = file_path.stat().st_mtime
                age = now - mtime

                if age > self._upload_ttl:
                    file_path.unlink(missing_ok=True)
                    log.debug('удалён старый загруженный файл: %s (возраст %.1f часов)', file_path.name, age / 3600)
        except OSError as error:
            log.warning('ошибка при очистке старых файлов: %s', error)

    def submit(self, name: str, path: Path, *, uploaded: bool = False, digest: str | None = None) -> JobRecord:
        """Поставить документ в работу — или вернуть его прошлый разбор.

        Тот же файл под другим именем — тот же результат: разбор детерминирован,
        и второй прогон стоил бы минут ради заведомо известного ответа. В аудите
        один документ открывают много раз, поэтому попадание тут частое.

        Если загруженный файл дублирует существующий (по SHA-256), новый файл
        удаляется, чтобы избежать утечки на диске.

        При постановке новой задачи удаляются загруженные файлы, превысившие срок
        жизни (upload_ttl), чтобы избежать неограниченного роста папки uploads.
        """
        checksum = digest if digest is not None else digest_of(path)

        # Очистить старые загруженные файлы (выполняется один раз в начале)
        self._cleanup_old_uploads()

        with self._lock:
            existing = self.store.find_by_digest(checksum)

            if existing is not None and existing.state is not JobState.FAILED:
                self.store.incr('reused')

                # Новый файл дублирует существующий: удалить его, чтобы избежать утечки
                if uploaded:
                    path.unlink(missing_ok=True)

                return existing

            job = JobRecord(
                id=uuid.uuid4().hex[:12],
                name=name,
                path=str(path),
                digest=checksum,
                uploaded=uploaded,
            )
            self.store.put(job)
            self.store.evict(self.max_jobs)
            self.store.incr('submitted')

        self._dispatch(job)

        return job

    def get(self, job_id: str) -> JobRecord | None:
        return self.store.get(job_id)

    def recent(self) -> list[JobRecord]:
        return self.store.recent()

    def stats(self) -> Stats:
        jobs = self.store.recent(limit=self.max_jobs)
        done = [j for j in jobs if j.state is JobState.DONE and j.result is not None]
        counters = self.store.counters()

        return Stats(
            submitted=counters.get('submitted', 0),
            reused=counters.get('reused', 0),
            done=len(done),
            failed=sum(1 for j in jobs if j.state is JobState.FAILED),
            running=sum(1 for j in jobs if j.state in (JobState.QUEUED, JobState.RUNNING)),
            periods=sum(len(j.result.periods) for j in done if j.result),
            reconciled=sum(j.result.reconciled_periods for j in done if j.result),
        )


class ThreadDispatcher:
    """Разбор в этом же процессе — режим по умолчанию.

    Ставит работу в поток и пропускает по одному слоту: без Redis и без воркера
    стенд остаётся однокомандным, но наплыв всё равно становится очередью.
    """

    def __init__(
        self, concurrency: int = MAX_CONCURRENT, *, max_pages: int | None = None, timeout: float | None = None
    ) -> None:
        self._slots = threading.Semaphore(concurrency)
        self._max_pages = max_pages
        self._timeout = timeout

    def __call__(self, job: JobRecord) -> None:
        threading.Thread(target=self._guarded, args=(job,), daemon=True).start()

    def _guarded(self, job: JobRecord) -> None:
        from bankstatement.worker.tasks import run

        with self._slots:
            run(job, max_pages=self._max_pages, timeout=self._timeout)


class QueueDispatcher:
    """Разбор отдельным воркером через taskiq.

    Отправка асинхронная, а вызывают её из синхронного обработчика запроса,
    поэтому короткая корутина ставится в работающий цикл событий. Ждать её
    незачем: ответ браузеру — это адрес задачи, а не её результат. Ссылка на
    задачу удерживается до завершения, иначе сборщик мусора вправе оборвать её
    на полпути.
    """

    def __init__(self) -> None:
        self._pending: set[asyncio.Task[None]] = set()

    def __call__(self, job: JobRecord) -> None:
        from bankstatement.worker.tasks import extract_document

        coroutine = extract_document.kiq(job.id)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_await(coroutine))

            return

        task = loop.create_task(_await(coroutine))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)


async def _await(coroutine: object) -> None:
    await coroutine  # type: ignore[misc]

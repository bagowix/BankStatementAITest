"""Где живёт состояние разбора.

Пока API и обработка в одном процессе, состояние может лежать в словаре. Как
только появляется отдельный воркер, словарь перестаёт работать: браузер
спрашивает о ходе работы у одного процесса, а работу ведёт другой. Поэтому
состояние вынесено за протокол с двумя реализациями — в память и в Redis, — и
код выше о разнице не знает.

Хранится именно состояние, а не объект: `JobRecord` — msgspec-структура, которая
одинаково переживает и словарь, и сеть.
"""

import threading
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

import msgspec

from bankstatement.models import ExtractionResult, StatementPeriod

KEY_PREFIX: Final[str] = 'bankstatement:job:'
INDEX_KEY: Final[str] = 'bankstatement:jobs'
DIGEST_PREFIX: Final[str] = 'bankstatement:digest:'
COUNTER_PREFIX: Final[str] = 'bankstatement:counter:'
STALL_AFTER: Final[timedelta] = timedelta(minutes=5)
"""Сколько молчания воркера считать отказом.

Снизу порог ограничен самой долгой законной паузой: у страницы свой предел в две
минуты, и две тяжёлые подряд — это уже больше четырёх. Сверху — сроком, после
которого очередь сама заберёт документ у мёртвого воркера. Пять минут между ними:
человек узнаёт о молчании раньше, чем очередь начнёт разбираться."""

TTL_SECONDS: Final[int] = 24 * 3600
"""Сколько состояние живёт в Redis. Стенд показывает разборы, а не хранит их;
для настоящего хранения нужна база и политика удаления, а не срок ключа."""


class JobState(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'


class Phase(StrEnum):
    READING = 'reading'
    RECONCILING = 'reconciling'
    DONE = 'done'


PHASE_TEXT: Final[dict[Phase, str]] = {
    Phase.READING: 'распознавание страниц',
    Phase.RECONCILING: 'сведение и уточнение спорных страниц',
    Phase.DONE: 'готово',
}


class JobRecord(msgspec.Struct):
    """Состояние одного разбора — всё, что нужно и браузеру, и воркеру."""

    id: str
    name: str
    path: str
    digest: str = ''
    uploaded: bool = False
    state: JobState = JobState.QUEUED
    pages_done: int = 0
    pages_in_flight: int = 0
    pages_total: int = 0
    engine: str = ''
    error: str = ''
    result: ExtractionResult | None = None
    ready: list[StatementPeriod] = []
    """Периоды, разобранные и проверенные к этой секунде.

    Подшивку из десяти выписок незачем показывать одним куском в конце: первая
    сведена через десяток секунд, и с ней уже можно работать."""

    started: datetime = msgspec.field(default_factory=lambda: datetime.now(UTC))
    finished: datetime | None = None
    heartbeat: datetime | None = None
    """Когда воркер последний раз отмечался. Разбор идёт минутами, и без отметки
    убитый процесс оставлял бы задачу «выполняемой» до истечения срока ключа."""

    @property
    def file(self) -> Path:
        return Path(self.path)

    @property
    def stalled(self) -> bool:
        """Воркер молчит дольше положенного.

        Это не «упало»: у стрима есть подтверждение, и неподтверждённый документ
        очередь вернёт другому воркеру. Поэтому задача не помечается неудачной —
        сообщается ровно то, что известно: отметок нет.
        """
        if self.state is not JobState.RUNNING:
            return False

        return datetime.now(UTC) - (self.heartbeat or self.started) > STALL_AFTER

    @property
    def active(self) -> bool:
        """Задача ещё в работе. Замолчавшая не в счёт: место она держать не должна."""
        return self.state in (JobState.QUEUED, JobState.RUNNING) and not self.stalled

    @property
    def phase(self) -> Phase:
        """Чем задача занята прямо сейчас.

        Распознавание — не весь разбор: за ним идут гипотезы, сведение и
        возможное перечитывание спорных страниц. Без явной фазы полоса замирала
        бы у правого края, и работа выглядела бы зависшей.
        """
        if self.state in (JobState.DONE, JobState.FAILED):
            return Phase.DONE

        if not self.pages_total or self.pages_done < self.pages_total:
            return Phase.READING

        return Phase.RECONCILING

    @property
    def working_percent(self) -> int:
        """Доля страниц, которые прочитаны или читаются прямо сейчас."""
        if not self.pages_total:
            return 0

        return min(int((self.pages_done + self.pages_in_flight) / self.pages_total * 90), 90)

    @property
    def percent(self) -> int:
        if self.state is JobState.DONE:
            return 100

        if not self.pages_total:
            return 0

        return min(int(self.pages_done / self.pages_total * 90), 90)

    @property
    def elapsed(self) -> float:
        end = self.finished or datetime.now(UTC)

        return (end - self.started).total_seconds()

    def discard(self) -> None:
        """Убрать загруженный файл, когда задача вытеснена из хранилища.

        Удаляется только то, что мы сами и положили во временную папку: образцы
        из репозитория трогать нельзя.
        """
        if self.uploaded:
            self.file.unlink(missing_ok=True)


class JobStore(Protocol):
    """Хранилище состояния разборов, общее для API и воркера."""

    def put(self, job: JobRecord, *, force: bool = True) -> None:
        """Сохранить состояние. `force=False` — рядовое обновление прогресса,
        которое хранилищу разрешено придержать ради экономии записей."""
        ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def find_by_digest(self, digest: str) -> JobRecord | None: ...

    def recent(self, limit: int = 50) -> list[JobRecord]: ...

    def evict(self, keep: int) -> None: ...

    def incr(self, counter: str) -> None:
        """Увеличить счётчик метрик."""
        ...

    def counters(self) -> dict[str, int]:
        """Счётчики за всё время жизни хранилища, а не процесса.

        В режиме очереди API перезапускается независимо от воркера, и счётчики
        в памяти процесса после рестарта показывали бы нули на работающей
        системе — худший вид метрики."""
        ...


class MemoryStore:
    """Состояние в словаре процесса — режим «всё в одном процессе»."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def put(self, job: JobRecord, *, force: bool = True) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def find_by_digest(self, digest: str) -> JobRecord | None:
        with self._lock:
            found = [j for j in self._jobs.values() if j.digest == digest]

        return found[0] if found else None

    def recent(self, limit: int = 50) -> list[JobRecord]:
        with self._lock:
            jobs = list(self._jobs.values())

        return sorted(jobs, key=lambda j: j.started, reverse=True)[:limit]

    def evict(self, keep: int) -> None:
        with self._lock:
            excess = len(self._jobs) - keep

            if excess <= 0:
                return

            order = sorted(
                self._jobs.values(),
                key=lambda j: (j.active, j.started),
            )
            doomed = order[:excess]

            for job in doomed:
                self._jobs.pop(job.id, None)

        for job in doomed:
            job.discard()

    def incr(self, counter: str) -> None:
        with self._lock:
            self._counters[counter] = self._counters.get(counter, 0) + 1

    def counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)


class RedisStore:
    """Состояние в Redis — режим «API и воркер по разные стороны».

    Записи прогресса идут чаще всего, поэтому у них свой порог: гнать сеть на
    каждую из девяноста девяти страниц незачем, разница в полсекунды на шкале
    никому не видна.
    """

    def __init__(self, client: object, ttl: int = TTL_SECONDS, throttle: float = 0.4) -> None:
        self._redis = client
        self._ttl = ttl
        self._throttle = throttle
        self._last_write: dict[str, float] = {}

    def put(self, job: JobRecord, *, force: bool = True) -> None:
        if not self._due(job.id, force=force):
            return

        payload = msgspec.json.encode(job)
        self._redis.set(f'{KEY_PREFIX}{job.id}', payload, ex=self._ttl)  # type: ignore[attr-defined]
        self._redis.zadd(INDEX_KEY, {job.id: job.started.timestamp()})  # type: ignore[attr-defined]

        if job.digest:
            self._redis.set(f'{DIGEST_PREFIX}{job.digest}', job.id, ex=self._ttl)  # type: ignore[attr-defined]

    def get(self, job_id: str) -> JobRecord | None:
        raw = self._redis.get(f'{KEY_PREFIX}{job_id}')  # type: ignore[attr-defined]

        return msgspec.json.decode(raw, type=JobRecord) if raw else None

    def find_by_digest(self, digest: str) -> JobRecord | None:
        job_id = self._redis.get(f'{DIGEST_PREFIX}{digest}')  # type: ignore[attr-defined]

        if not job_id:
            return None

        return self.get(job_id.decode() if isinstance(job_id, bytes) else str(job_id))

    def recent(self, limit: int = 50) -> list[JobRecord]:
        ids = self._redis.zrevrange(INDEX_KEY, 0, limit - 1)  # type: ignore[attr-defined]
        found = [self.get(i.decode() if isinstance(i, bytes) else str(i)) for i in ids]

        return [job for job in found if job is not None]

    def evict(self, keep: int) -> None:
        """Индекс держится усечённым; сами записи уходят по сроку жизни ключа.

        А вот загруженные файлы по сроку жизни не уходят: они лежат на диске, и
        без явной уборки папка загрузок растёт до конца места. Поэтому вытесняемые
        записи сначала читаются, а потом уже удаляются из индекса.
        """
        doomed = self._redis.zrange(INDEX_KEY, 0, -(keep + 1))  # type: ignore[attr-defined]

        if not doomed:
            return

        self._redis.zremrangebyrank(INDEX_KEY, 0, -(keep + 1))  # type: ignore[attr-defined]

        for raw in doomed:
            job = self.get(raw.decode() if isinstance(raw, bytes) else str(raw))

            if job is not None:
                job.discard()

    def incr(self, counter: str) -> None:
        self._redis.incr(f'{COUNTER_PREFIX}{counter}')  # type: ignore[attr-defined]

    def counters(self) -> dict[str, int]:
        names = ('submitted', 'reused')
        values = self._redis.mget([f'{COUNTER_PREFIX}{n}' for n in names])  # type: ignore[attr-defined]

        return {name: int(value or 0) for name, value in zip(names, values, strict=True)}

    def _due(self, job_id: str, *, force: bool) -> bool:
        """Пора ли писать. Форсированная запись тоже сдвигает отсчёт.

        Иначе после каждой важной записи следующее рядовое обновление проходило
        бы вне очереди, и порог держал бы вдвое меньше, чем заявлено.
        """
        now = time.monotonic()

        if not force and now - self._last_write.get(job_id, 0.0) < self._throttle:
            return False

        self._last_write[job_id] = now

        return True

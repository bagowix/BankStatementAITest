import asyncio
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bankstatement.ingest import Progress
from bankstatement.web.jobs import MAX_JOBS, JobRegistry, QueueDispatcher, ThreadDispatcher, digest_of
from bankstatement.worker.store import STALL_AFTER, JobRecord, JobState, MemoryStore, Phase


def make_registry(runner=lambda job: None):
    """Реестр, который ничего не запускает: проверяем приём, а не разбор."""
    return JobRegistry(store=MemoryStore(), dispatch=runner)


def test__submit__job_registered_and_runner_called():
    seen: list[JobRecord] = []
    registry = make_registry(seen.append)

    job = registry.submit('a.pdf', Path('a.pdf'), digest='a')

    assert registry.get(job.id) is job
    assert [j.id for j in seen] == [job.id]


def test__get__unknown_id__none():
    assert make_registry().get('нет такого') is None


def test__percent__while_reading__never_reaches_hundred():
    """Чтение страниц — не весь разбор: за ним идут сведение и эскалация."""
    job = JobRecord(id='x', name='a.pdf', path='a.pdf', state=JobState.RUNNING, pages_done=99, pages_total=99)

    assert job.percent == 90

    job.state = JobState.DONE
    assert job.percent == 100


def test__percent__nothing_known_yet__zero():
    assert JobRecord(id='x', name='a.pdf', path='a.pdf').percent == 0


def test__recent__sorted_newest_first():
    registry = make_registry()
    first = registry.submit('a.pdf', Path('a.pdf'), digest='a')
    first.started = datetime.now(UTC) - timedelta(minutes=5)
    second = registry.submit('b.pdf', Path('b.pdf'), digest='b')

    assert [j.id for j in registry.recent()] == [second.id, first.id]


def test__submit__over_the_limit__finished_evicted_first():
    """Стенд показывает разборы, а не хранит их: рост памяти был бы утечкой."""
    registry = make_registry()
    jobs = [registry.submit(f'{index}.pdf', Path('a.pdf'), digest=str(index)) for index in range(MAX_JOBS + 3)]

    for job in jobs:
        job.state = JobState.DONE

    extra = registry.submit('свежий.pdf', Path('a.pdf'), digest='свежий')

    assert len(registry.recent()) <= MAX_JOBS
    assert registry.get(extra.id) is not None
    assert registry.get(jobs[0].id) is None


def test__submit__over_the_limit__running_job_survives():
    """Вытеснить работающий разбор значит показать 404 на середине работы."""
    registry = make_registry()
    running = registry.submit('долгий.pdf', Path('a.pdf'), digest='долгий')
    running.state = JobState.RUNNING

    for index in range(MAX_JOBS + 2):
        registry.submit(f'{index}.pdf', Path('a.pdf'), digest=str(index)).state = JobState.DONE

    registry.submit('ещё.pdf', Path('a.pdf'), digest='ещё')

    assert registry.get(running.id) is not None


def test__run__extraction_raises__job_marked_failed(monkeypatch):
    """Поломка разбора должна доехать до интерфейса текстом, а не пустой страницей."""
    from bankstatement.worker import tasks as module

    def explode(path, on_page=None, on_period=None, **limits):
        raise ValueError('битый PDF')

    monkeypatch.setattr(module, 'extract_result', explode)
    job = JobRecord(id='x', name='a.pdf', path='a.pdf')

    module.run(job)

    assert job.state is JobState.FAILED
    assert 'битый PDF' in job.error


def test__run__progress__recorded_on_the_job(monkeypatch):
    from bankstatement.worker import tasks as module

    def extract(path, on_page=None, on_period=None, **limits):
        on_page(Progress(done=5, in_flight=3, total=10, engine='tesseract'))

        return None

    monkeypatch.setattr(module, 'extract_result', extract)
    job = JobRecord(id='x', name='a.pdf', path='a.pdf')

    module.run(job)

    assert (job.pages_done, job.pages_in_flight, job.pages_total, job.engine) == (5, 3, 10, 'tesseract')
    assert job.state is JobState.DONE


def test__readable__internal_path__replaced_with_file_name():
    """Имя временного файла на диске в браузере показывать нечего."""
    from bankstatement.worker.tasks import readable

    job = JobRecord(id='x', name='выписка.pdf', path='/tmp/bankstatement-uploads/abc.pdf')
    error = ValueError("Failed to open file '/tmp/bankstatement-uploads/abc.pdf' as type pdf.")

    text = readable(error, job)

    assert '/tmp/' not in text
    assert 'выписка.pdf' in text


def test__discard__uploaded_file__removed(tmp_path):
    uploaded = tmp_path / 'upload.pdf'
    uploaded.write_bytes(b'%PDF-1.7')
    job = JobRecord(id='x', name='a.pdf', path=str(uploaded), uploaded=True)

    job.discard()

    assert not uploaded.exists()


def test__discard__sample_from_repo__left_alone(tmp_path):
    """Образцы лежат в репозитории: удалять их вытеснением задачи нельзя."""
    sample = tmp_path / 'sample.pdf'
    sample.write_bytes(b'%PDF-1.7')
    job = JobRecord(id='x', name='sample.pdf', path=str(sample))

    job.discard()

    assert sample.exists()


def test__evict__uploaded_file__cleaned_up(tmp_path):
    registry = make_registry()
    paths = []

    for index in range(MAX_JOBS + 2):
        path = tmp_path / f'{index}.pdf'
        path.write_bytes(b'%PDF-1.7')
        paths.append(path)
        registry.submit(f'{index}.pdf', path, uploaded=True, digest=str(index)).state = JobState.DONE

    assert not paths[0].exists()
    assert paths[-1].exists()


def test__phase__pages_still_being_read__reading():
    job = JobRecord(id='x', name='a.pdf', path='a.pdf', state=JobState.RUNNING, pages_done=3, pages_total=99)

    assert job.phase is Phase.READING


def test__phase__all_pages_read__reconciling():
    """Полоса не должна замирать у края: после чтения работа продолжается."""
    job = JobRecord(id='x', name='a.pdf', path='a.pdf', state=JobState.RUNNING, pages_done=99, pages_total=99)

    assert job.phase is Phase.RECONCILING
    assert job.percent == 90


def test__phase__finished__done():
    for state in (JobState.DONE, JobState.FAILED):
        job = JobRecord(id='x', name='a.pdf', path='a.pdf', state=state)
        assert job.phase is Phase.DONE


def test__submit__same_file_twice__second_reuses_the_first(tmp_path):
    """Один документ открывают много раз; второй разбор стоил бы минут впустую."""
    started: list[JobRecord] = []
    registry = make_registry(started.append)
    document = tmp_path / 'statement.pdf'
    document.write_bytes(b'%PDF-1.7 payload')

    first = registry.submit('выписка.pdf', document)
    second = registry.submit('она же под другим именем.pdf', document)

    assert second is first
    assert len(started) == 1
    assert registry.stats().reused == 1


def test__submit__duplicate_upload__new_file_deleted(tmp_path):
    """Загруженный файл, дублирующий существующий, удаляется, чтобы избежать утечки."""
    started: list[JobRecord] = []
    registry = make_registry(started.append)

    first_upload = tmp_path / 'first.pdf'
    first_upload.write_bytes(b'%PDF-1.7 payload')
    first = registry.submit('first.pdf', first_upload, uploaded=True)

    # Второй загруженный файл — тот же контент, другое имя
    second_upload = tmp_path / 'second.pdf'
    second_upload.write_bytes(b'%PDF-1.7 payload')  # Точно такой же контент
    second = registry.submit('second.pdf', second_upload, uploaded=True)

    # Дедупликация: второй результат — первый же разбор
    assert second is first
    assert len(started) == 1
    assert registry.stats().reused == 1
    # Второй загруженный файл удалён, чтобы избежать утечки
    assert not second_upload.exists()
    # Первый файл остаётся, потому что он ещё нужен разбору
    assert first_upload.exists()


def test__submit__failed_job__retried_on_next_upload(tmp_path):
    """Сбойный разбор кэшировать нельзя: повтор должен получить второй шанс."""
    registry = make_registry()
    document = tmp_path / 'statement.pdf'
    document.write_bytes(b'%PDF-1.7 payload')

    first = registry.submit('a.pdf', document)
    first.state = JobState.FAILED

    assert registry.submit('a.pdf', document) is not first


def test__stats__counts_documents_and_reconciled_periods(tmp_path):
    registry = make_registry()
    registry.submit('a.pdf', Path('a.pdf'), digest='a')

    counters = registry.stats()

    assert counters.submitted == 1
    assert counters.reused == 0


def test__digest_of__same_bytes__same_key(tmp_path):
    left, right = tmp_path / 'l.pdf', tmp_path / 'r.pdf'
    left.write_bytes(b'%PDF payload')
    right.write_bytes(b'%PDF payload')

    assert digest_of(left) == digest_of(right)


def test__thread_dispatcher__job_submitted__handled_off_the_request(monkeypatch):
    """Обработчик отвечает адресом задачи, а не её результатом: разбор идёт в потоке."""
    from bankstatement.worker import tasks

    done = threading.Event()
    seen: list[str] = []

    def fake_run(job, **limits):
        seen.append(job.id)
        done.set()

    monkeypatch.setattr(tasks, 'run', fake_run)
    job = JobRecord(id='x', name='a.pdf', path='a.pdf')

    ThreadDispatcher(concurrency=1)(job)

    assert done.wait(timeout=5)
    assert seen == ['x']


class _FakeTask:
    """Подмена задачи taskiq: важно, что её отправили, а не как она выполнится."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def kiq(self, job_id: str):
        async def send() -> None:
            self.sent.append(job_id)

        return send()


def test__queue_dispatcher__no_event_loop__sends_anyway(monkeypatch):
    """Обработчик синхронный, цикла событий под ним может не быть вовсе."""
    from bankstatement.worker import tasks

    fake = _FakeTask()
    monkeypatch.setattr(tasks, 'extract_document', fake)

    QueueDispatcher()(JobRecord(id='x', name='a.pdf', path='a.pdf'))

    assert fake.sent == ['x']


def test__queue_dispatcher__inside_a_running_loop__task_kept_until_done(monkeypatch):
    """Ссылку на задачу нужно удерживать: иначе сборщик мусора вправе оборвать отправку."""
    from bankstatement.worker import tasks

    fake = _FakeTask()
    monkeypatch.setattr(tasks, 'extract_document', fake)
    dispatcher = QueueDispatcher()

    async def drive() -> None:
        dispatcher(JobRecord(id='x', name='a.pdf', path='a.pdf'))
        assert dispatcher._pending
        await asyncio.sleep(0)

    asyncio.run(drive())

    assert fake.sent == ['x']
    assert not dispatcher._pending


def silent_for(minutes: int) -> JobRecord:
    """Задача, которая числится выполняемой и столько-то не отмечалась."""
    return JobRecord(
        id='x',
        name='a.pdf',
        path='a.pdf',
        state=JobState.RUNNING,
        heartbeat=datetime.now(UTC) - timedelta(minutes=minutes),
    )


def test__stalled__worker_reports_regularly__not_stalled():
    """Отметка ставится на каждой прочитанной странице, и молчания быть не должно."""
    assert silent_for(0).stalled is False


def test__stalled__no_heartbeat_for_too_long__worker_considered_silent():
    assert silent_for(int(STALL_AFTER.total_seconds() // 60) + 1).stalled is True


def test__stalled__job_already_finished__never_stalled():
    """Законченная задача не отмечается и не должна: молчание для неё нормально."""
    job = silent_for(60)
    job.state = JobState.DONE

    assert job.stalled is False


def test__stalled__never_started_reporting__counted_from_submission():
    """Воркер, умерший до первой страницы, отметок не оставил вовсе."""
    job = JobRecord(id='x', name='a.pdf', path='a.pdf', state=JobState.RUNNING)
    job.started = datetime.now(UTC) - STALL_AFTER - timedelta(minutes=1)

    assert job.heartbeat is None
    assert job.stalled is True


def test__evict__silent_job__loses_its_place_to_a_finished_one():
    """Замолчавшая задача не должна вечно держать место: работой это уже не назвать."""
    store = MemoryStore()
    silent = silent_for(60)
    fresh = JobRecord(id='свежая', name='b.pdf', path='b.pdf', state=JobState.DONE)
    store.put(silent)
    store.put(fresh)

    store.evict(keep=1)

    assert store.get('x') is None
    assert store.get('свежая') is not None


def test__cleanup_old_uploads__files_older_than_ttl__deleted(tmp_path):
    """Загруженные файлы, превысившие срок жизни, удаляются при постановке новой задачи."""
    registry = JobRegistry(
        store=MemoryStore(),
        dispatch=lambda job: None,
        uploads_dir=tmp_path,
        upload_ttl=1.0,  # 1 секунда для теста
    )

    # Создать старый файл
    old_file = tmp_path / 'old.pdf'
    old_file.write_bytes(b'%PDF-1.7 old')
    # Установить время создания в прошлое
    old_mtime = time.time() - 2.0  # 2 секунды назад
    old_file.touch()
    os.utime(old_file, (old_mtime, old_mtime))

    # Создать новый файл
    new_file = tmp_path / 'new.pdf'
    new_file.write_bytes(b'%PDF-1.7 new')

    # Постановка новой задачи должна удалить старый файл
    registry.submit('новый.pdf', new_file, uploaded=True, digest='new')

    assert not old_file.exists()
    assert new_file.exists()


def test__cleanup_old_uploads__files_newer_than_ttl__kept(tmp_path):
    """Загруженные файлы, не превысившие срок жизни, остаются."""
    registry = JobRegistry(
        store=MemoryStore(),
        dispatch=lambda job: None,
        uploads_dir=tmp_path,
        upload_ttl=10.0,  # 10 секунд для теста
    )

    # Создать новый файл
    new_file = tmp_path / 'new.pdf'
    new_file.write_bytes(b'%PDF-1.7 new')

    # Постановка новой задачи не должна удалить новый файл
    registry.submit('новый.pdf', new_file, uploaded=True, digest='new')

    assert new_file.exists()


def test__cleanup_old_uploads__no_uploads_dir__ignored(tmp_path):
    """Если uploads_dir не задан, очистка просто пропускается."""
    registry = JobRegistry(
        store=MemoryStore(),
        dispatch=lambda job: None,
        uploads_dir=None,
        upload_ttl=1.0,
    )

    # Это не должно вызвать ошибку
    registry.submit('a.pdf', tmp_path / 'a.pdf', digest='a')

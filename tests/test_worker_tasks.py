"""Задача воркера: что происходит с состоянием, пока идёт разбор.

Разбор здесь подменён — проверяется не извлечение (оно проверено отдельно), а
то, что воркер отражает ход работы в общем хранилище: браузер спрашивает
состояние постоянно и должен видеть свежее, а не итоговое.
"""

from pathlib import Path

import pytest

from bankstatement.ingest import Progress
from bankstatement.worker import tasks
from bankstatement.worker.store import JobRecord, JobState, MemoryStore


@pytest.fixture
def store(monkeypatch):
    made = MemoryStore()
    monkeypatch.setattr(tasks, 'store', made)

    return made


def make_job(path: Path) -> JobRecord:
    return JobRecord(id='j1', name='выписка.pdf', path=str(path))


def test__extract_document__job_gone_from_the_store__no_crash(store):
    """Хранилище пережило рестарт, а очередь — нет: задача осталась без состояния."""
    assert tasks.extract_document('нет такой') == 'нет такой'


def test__extract_document__job_in_the_store__extraction_runs(store, monkeypatch, tmp_path):
    """Тело сообщения — один идентификатор: всё остальное воркер берёт из хранилища."""
    monkeypatch.setattr(tasks, 'extract_result', lambda path, *, on_page, on_period, **limits: None)
    store.put(make_job(tmp_path / 'a.pdf'))

    assert tasks.extract_document('j1') == 'j1'
    assert store.get('j1').state is JobState.DONE


def test__run__periods_as_they_finish__written_one_by_one(store, monkeypatch, tmp_path):
    """Готовая выписка уходит в хранилище сразу, а не после последней страницы."""
    seen: list[int] = []

    def fake_extract(path, *, on_page, on_period, **limits):
        on_page(Progress(done=1, in_flight=2, total=3, engine='tesseract'))
        on_period('первая')
        seen.append(len(store.get('j1').ready))
        on_period('вторая')
        seen.append(len(store.get('j1').ready))

        return None

    monkeypatch.setattr(tasks, 'extract_result', fake_extract)
    job = make_job(tmp_path / 'a.pdf')
    store.put(job)

    tasks.run(job)

    assert seen == [1, 2]
    assert store.get('j1').state is JobState.DONE
    assert (job.pages_done, job.pages_in_flight, job.pages_total, job.engine) == (1, 2, 3, 'tesseract')


def test__run__while_reading__heartbeat_moves_with_the_progress(store, monkeypatch, tmp_path):
    """Отметка живости — это и есть запись прогресса: отдельного сердцебиения нет."""
    seen = []

    def fake_extract(path, *, on_page, on_period, **limits):
        on_page(Progress(done=1, in_flight=0, total=2, engine='tesseract'))
        seen.append(store.get('j1').heartbeat)

        return None

    monkeypatch.setattr(tasks, 'extract_result', fake_extract)
    job = make_job(tmp_path / 'a.pdf')
    store.put(job)

    tasks.run(job)

    assert seen[0] is not None
    assert not job.stalled


def test__run__extraction_raises__failed_with_a_readable_reason(store, monkeypatch, tmp_path):
    """Интерфейс обязан показать причину, а не пустую страницу."""

    def explode(path, *, on_page, on_period, **limits):
        raise ValueError('битый PDF')

    monkeypatch.setattr(tasks, 'extract_result', explode)
    job = make_job(tmp_path / 'a.pdf')
    store.put(job)

    tasks.run(job)

    saved = store.get('j1')
    assert saved.state is JobState.FAILED
    assert saved.error == 'ValueError: битый PDF'
    assert saved.finished is not None


def test__readable__temp_path_in_the_message__replaced_by_the_name(tmp_path):
    """Имя временного файла на диске — подробность реализации, в браузере ей не место."""
    job = make_job(tmp_path / 'tmp1234.pdf')

    text = tasks.readable(OSError(f'не открыть {job.path}'), job)

    assert job.path not in text
    assert 'выписка.pdf' in text


def test__readable__error_without_text__at_least_names_the_type():
    assert tasks.readable(TimeoutError(), make_job(Path('a.pdf'))) == 'TimeoutError'


def test__run__limits_given__passed_to_extraction_as_page_cap_and_deadline(store, monkeypatch, tmp_path):
    """Пределы стенда доходят до разбора, а не только читаются из окружения."""
    import time

    seen = {}

    def fake_extract(path, *, on_page, on_period, **limits):
        seen.update(limits)

        return None

    monkeypatch.setattr(tasks, 'extract_result', fake_extract)
    job = make_job(tmp_path / 'a.pdf')
    store.put(job)

    tasks.run(job, max_pages=7, timeout=60.0)

    assert seen['max_pages'] == 7
    assert seen['deadline'] > time.monotonic()

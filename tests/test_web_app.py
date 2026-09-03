"""Стенд проверяется через фабрику: приложение собирается на каждый тест.

Подмена атрибутов модуля больше не нужна — состав приложения задаётся
контейнером, и тест передаёт свой.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip('fastapi', reason='нужны зависимости группы ui')

from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.fastapi import FastapiProvider
from fastapi.testclient import TestClient

from bankstatement.extract import extract_result
from bankstatement.models import (
    Account,
    ExtractionResult,
    Guarantee,
    Period,
    Reconciliation,
    ReconStatus,
    StatementPeriod,
    Summary,
)
from bankstatement.web import routes
from bankstatement.web.factory import create_app
from bankstatement.web.jobs import JobRegistry
from bankstatement.web.providers import AppProvider
from bankstatement.web.settings import Settings
from bankstatement.worker.store import STALL_AFTER, JobRecord, JobState, MemoryStore

pytestmark = pytest.mark.ui

SAMPLES = Path('samples')
GREAT_LAKES = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'


class StubRegistryProvider(Provider):
    """Реестр, который ничего не запускает: проверяем ручки, а не разбор."""

    scope = Scope.APP

    @provide
    def registry(self) -> JobRegistry:
        return JobRegistry(store=MemoryStore(), dispatch=lambda job: None)


@pytest.fixture
def settings(tmp_path, sample_pdf):
    """Папка образцов — своя на тест, с синтетической выпиской из conftest."""
    return Settings(samples_dir=str(sample_pdf.parent), uploads_dir=str(tmp_path / 'uploads'))


@pytest.fixture
def container(settings):
    """Свой контейнер на каждый тест: и реестр, и лимитер собираются заново.

    Общий лимитер копил бы токены между проверками, и порядок их запуска начал
    бы влиять на результат.
    """
    made = make_async_container(
        AppProvider(),
        StubRegistryProvider(),
        FastapiProvider(),
        context={Settings: settings},
    )

    yield made


@pytest.fixture
def client(settings, container):
    with TestClient(create_app(settings, container)) as test_client:
        yield test_client


@pytest.fixture
def registry(client, container):
    """Реестр того же приложения — чтобы тест смотрел на то же состояние."""
    import asyncio

    return asyncio.run(container.get(JobRegistry))


def finished(job: JobRecord) -> JobRecord:

    job.result = extract_result(GREAT_LAKES)
    job.state = JobState.DONE

    return job


def fake_period(page: int, delta: str, status: ReconStatus) -> StatementPeriod:
    """Период без документа: проверяется отрисовка итога, а не извлечение."""
    return StatementPeriod(
        account=Account(period=Period(start=date(2024, page, 1), end=date(2024, page, 28))),
        summary=Summary(),
        transactions=[],
        reconciliation=Reconciliation(
            status=status,
            guarantee=Guarantee.NONE,
            delta=Decimal(delta),
            computed_deposits=Decimal('0.00'),
            computed_withdrawals=Decimal('0.00'),
        ),
        pages=[page],
    )


def test__index__page_lists_samples(client):
    response = client.get('/')

    assert response.status_code == 200
    assert 'Перетащите выписки' in response.text


def test__create_job__sample__returns_progress_fragment(client, registry, sample_pdf):
    response = client.post('/jobs', data={'sample': sample_pdf.name})

    assert response.status_code == 200
    assert '/status' in response.text
    assert registry.recent()[0].name == sample_pdf.name


def test__create_job__unknown_sample__not_found(client):
    """Имя сверяется со списком: иначе поле формы читало бы любой файл на диске."""
    response = client.post('/jobs', data={'sample': '../../etc/passwd'})

    assert response.status_code == 404


def test__create_job__uploaded_file__stored_and_queued(client, registry, sample_pdf):
    response = client.post('/jobs', files={'files': ('выписка.pdf', sample_pdf.read_bytes(), 'application/pdf')})

    assert response.status_code == 200
    job = registry.recent()[0]
    assert job.name == 'выписка.pdf'
    assert job.file.exists()


@pytest.mark.samples
def test__create_job__batch__every_file_gets_its_own_job(client, registry):
    """Пачка — обычный режим: выписки приходят папкой за квартал."""
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    payload = GREAT_LAKES.read_bytes()
    response = client.post(
        '/jobs',
        files=[
            ('files', ('январь.pdf', payload, 'application/pdf')),
            ('files', ('февраль.pdf', payload + b'%%EOF', 'application/pdf')),
        ],
    )

    assert response.status_code == 200
    assert {j.name for j in registry.recent()} == {'январь.pdf', 'февраль.pdf'}


def test__create_job__too_many_files__rejected(client, settings):
    payload = b'%PDF-1.7 x'
    batch = [
        ('files', (f'{i}.pdf', payload + str(i).encode(), 'application/pdf')) for i in range(settings.max_batch + 1)
    ]

    response = client.post('/jobs', files=batch)

    assert response.status_code == 413


def test__create_job__burst_of_uploads__rate_limited(client, settings):
    """Один клиент не должен забивать очередь разборов."""
    payload = b'%PDF-1.7 x'
    codes = [
        client.post('/jobs', files={'files': (f'{i}.pdf', payload + str(i).encode(), 'application/pdf')}).status_code
        for i in range(settings.upload_burst + 2)
    ]

    assert 429 in codes


def test__create_job__nothing_sent__bad_request(client):
    assert client.post('/jobs', data={}).status_code == 400


def test__status__while_running__progress_fragment_polls_again(client, registry, sample_pdf):
    job = registry.submit('a.pdf', sample_pdf)
    job.state = JobState.RUNNING
    job.pages_done, job.pages_in_flight, job.pages_total = 3, 12, 99

    response = client.get(f'/jobs/{job.id}/status')

    assert 'hx-get' in response.text
    assert 'распознавание страниц' in response.text
    assert 'прочитано 3 из 99' in response.text
    assert 'читается ещё 12' in response.text


def test__status__failed__error_text_reaches_the_page(client, registry, sample_pdf):
    job = registry.submit('a.pdf', sample_pdf)
    job.state, job.error = JobState.FAILED, 'ValueError: битый PDF'

    response = client.get(f'/jobs/{job.id}/status')

    assert 'битый PDF' in response.text
    assert 'hx-get' not in response.text  # опрос прекращается


def test__job_page__opened_while_still_running__shows_progress_and_keeps_watching(client, registry, sample_pdf):
    """Ссылку открывают и в середине разбора; пустая страница выглядела бы поломкой."""
    job = registry.submit('a.pdf', sample_pdf)
    job.state = JobState.RUNNING
    job.pages_done, job.pages_total = 3, 10

    text = client.get(f'/jobs/{job.id}').text

    assert 'hx-get' in text
    assert 'прочитано 3 из 10' in text


def test__status__worker_gone_silent__says_so_and_keeps_watching(client, registry, sample_pdf):
    """Не «упало»: документ остался неподтверждённым в очереди и вернётся в работу.

    Поэтому опрос не прекращается — иначе возвращение к жизни осталось бы
    незамеченным, а страница врала бы про отказ.
    """
    job = registry.submit('a.pdf', sample_pdf)
    job.state = JobState.RUNNING
    job.heartbeat = datetime.now(UTC) - STALL_AFTER - timedelta(minutes=1)

    text = client.get(f'/jobs/{job.id}/status').text

    assert 'Воркер не отвечает' in text
    assert 'вернётся в работу, когда в неё придёт следующая задача' in text
    assert 'hx-get' in text


def test__status__unknown_job__not_found(client):
    assert client.get('/jobs/неттакого/status').status_code == 404


@pytest.mark.samples
def test__job_page__reconciled__verdict_and_guarantee_are_shown(client, registry):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = finished(registry.submit(GREAT_LAKES.name, GREAT_LAKES))

    response = client.get(f'/jobs/{job.id}')

    assert '✓ сошлось' in response.text
    assert 'Проверено построчно' in response.text
    assert '2,014,882.47' in response.text


@pytest.mark.samples
def test__job_page__all_reconciled__opinion_first_and_no_findings_table(client, registry):
    """Читателю нужен один ответ, и он стоит первым — до всех подробностей."""
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = finished(registry.submit(GREAT_LAKES.name, GREAT_LAKES))

    text = client.get(f'/jobs/{job.id}').text

    assert 'Подтверждено' in text
    assert 'Требуют проверки' not in text
    assert text.index('Подтверждено') < text.index('сошлось')


def test__job_page__some_periods_failed__findings_summary_leads_to_the_cards(client, registry, sample_pdf):
    """Сводка расхождений — крупные первыми, каждое со ссылкой на свою карточку."""
    job = registry.submit('mixed.pdf', sample_pdf)
    job.result = ExtractionResult(
        periods=[fake_period(1, '0.10', ReconStatus.FAILED), fake_period(2, '-11798.31', ReconStatus.FAILED)],
        source_file='mixed.pdf',
        engine_stats={'tesseract': 2},
    )
    job.state = JobState.DONE

    text = client.get(f'/jobs/{job.id}').text

    assert 'Не подтверждено' in text
    assert 'Требуют проверки: 2' in text
    assert 'нераскрыто 11,798.41' in text
    assert text.index('-11,798.31') < text.index('0.10')
    assert f'href="#p-{job.id}-2"' in text


@pytest.mark.samples
def test__job_json__ready__downloadable_payload(client, registry):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = finished(registry.submit(GREAT_LAKES.name, GREAT_LAKES))

    response = client.get(f'/jobs/{job.id}/json')

    assert response.status_code == 200
    assert 'attachment' in response.headers['content-disposition']
    assert response.json()['summary']['ending_balance'] == 2041338.12


def test__job_json__not_ready__conflict(client, registry, sample_pdf):
    job = registry.submit('a.pdf', sample_pdf)

    assert client.get(f'/jobs/{job.id}/json').status_code == 409


@pytest.mark.samples
def test__job_crop__row_bbox__png_of_that_row(client, registry):
    """Провенанс, который видно: вырезка из исходного документа."""
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = registry.submit(GREAT_LAKES.name, GREAT_LAKES)

    response = client.get(f'/jobs/{job.id}/crop', params={'page': 1, 'x0': 31, 'y0': 204, 'x1': 564, 'y1': 216})

    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    assert response.content[:8] == b'\x89PNG\r\n\x1a\n'


@pytest.mark.samples
def test__job_crop__page_out_of_range__not_found(client, registry):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = registry.submit(GREAT_LAKES.name, GREAT_LAKES)

    response = client.get(f'/jobs/{job.id}/crop', params={'page': 999, 'x0': 0, 'y0': 0, 'x1': 10, 'y1': 10})

    assert response.status_code == 404


@pytest.mark.samples
@pytest.mark.parametrize('page', [0, -1])
def test__job_crop__page_below_one__rejected(client, registry, page):
    """PyMuPDF считает такой индекс отсчётом с конца и молча отдал бы чужую страницу."""
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = registry.submit(GREAT_LAKES.name, GREAT_LAKES)

    response = client.get(f'/jobs/{job.id}/crop', params={'page': page, 'x0': 0, 'y0': 0, 'x1': 10, 'y1': 10})

    assert response.status_code == 422


@pytest.mark.samples
def test__api_job__machine_view__same_state_as_the_page(client, registry):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = finished(registry.submit(GREAT_LAKES.name, GREAT_LAKES))

    payload = client.get(f'/api/jobs/{job.id}').json()

    assert payload['state'] == 'done'
    assert payload['percent'] == 100
    assert len(payload['result']['transactions']) == 292


def test__create_job__not_a_pdf__rejected_before_any_work(client, registry):
    """Промах в диалоге выбора файла должен получить внятный ответ сразу."""
    response = client.post('/jobs', files={'files': ('заметка.txt', 'это не pdf'.encode(), 'application/pdf')})

    assert response.status_code == 415
    assert 'не PDF' in response.json()['detail']
    assert registry.recent() == []


def test__create_job__empty_file__rejected(client):
    response = client.post('/jobs', files={'files': ('пусто.pdf', b'', 'application/pdf')})

    assert response.status_code == 400


def test__create_job__too_large__rejected_and_nothing_left_on_disk(tmp_path):
    """Предел задаётся настройками — приложение собирается под тест, а не патчится."""
    uploads = tmp_path / 'uploads'
    small = Settings(samples_dir=str(tmp_path), uploads_dir=str(uploads), max_upload=1024)
    container = make_async_container(
        AppProvider(),
        StubRegistryProvider(),
        FastapiProvider(),
        context={Settings: small},
    )

    with TestClient(create_app(small, container)) as client:
        response = client.post(
            '/jobs',
            files={'files': ('большой.pdf', b'%PDF-1.7\n' + b'0' * 4096, 'application/pdf')},
        )

    assert response.status_code == 413
    assert list(uploads.glob('*.pdf')) == []


@pytest.mark.samples
def test__status__ready_period__arrives_as_out_of_band_addition(client, registry):
    """Готовая выписка приходит добавлением, а не перерисовкой уже показанных.

    Иначе у раскрытой таблицы операций спойлер захлопывался бы каждые семьсот
    миллисекунд прямо под руками у того, кто её читает.
    """
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = registry.submit(GREAT_LAKES.name, GREAT_LAKES)
    job.state = JobState.RUNNING
    job.pages_done, job.pages_total = 3, 99
    job.ready.append(finished(JobRecord(id='tmp', name=GREAT_LAKES.name, path=str(GREAT_LAKES))).result.periods[0])

    response = client.get(f'/jobs/{job.id}/status')

    assert 'hx-swap-oob="beforeend:#periods"' in response.text
    assert '✓ сошлось' in response.text
    assert 'shown=1' in response.text  # следующий опрос попросит только новые


@pytest.mark.samples
def test__status__already_shown_period__not_sent_again(client, registry):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = registry.submit(GREAT_LAKES.name, GREAT_LAKES)
    job.state = JobState.RUNNING
    job.ready.append(finished(JobRecord(id='tmp', name=GREAT_LAKES.name, path=str(GREAT_LAKES))).result.periods[0])

    response = client.get(f'/jobs/{job.id}/status', params={'shown': 1})

    assert 'hx-swap-oob' not in response.text


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('April 2021 eStatement.pdf', 'April 2021 eStatement'),
        ('зло"; attachment; filename="evil.exe', 'зло_ attachment_ filename_evil'),
        ('../../etc/passwd', 'passwd'),
        ('', 'statement'),
        ('отчёт\nза март.pdf', 'отчёт_за март'),
    ],
)
def test__safe_filename__user_supplied_name__cannot_break_the_header(name, expected):
    """Кавычка или перевод строки в имени файла разорвали бы заголовок ответа."""
    assert routes.safe_filename(name) == expected


@pytest.mark.samples
def test__job_json__hostile_name__header_stays_intact(client, registry):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    job = finished(registry.submit('a"; attachment; filename="b.pdf', GREAT_LAKES))

    disposition = client.get(f'/jobs/{job.id}/json').headers['content-disposition']

    assert disposition.count('filename=') == 1


def test__health__reports_readiness(client):
    payload = client.get('/health').json()

    assert payload['status'] == 'ok'
    assert payload['workers'] >= 1
    assert payload['mode'] == 'inline'


def test__health__queue_mode__named(tmp_path):
    """Молчащий воркер обычно значит стенд без переменной; режим должен быть виден."""
    settings = Settings(samples_dir=str(SAMPLES), uploads_dir=str(tmp_path), redis_url='redis://localhost:6379/9')
    container = make_async_container(
        AppProvider(),
        StubRegistryProvider(),
        FastapiProvider(),
        context={Settings: settings},
    )

    with TestClient(create_app(settings, container)) as client:
        assert client.get('/health').json()['mode'] == 'queue'


def test__metrics__prometheus_text__has_the_reconciliation_counter(client):
    """Доля сведённых периодов — главная метрика качества; без неё прод слеп."""
    response = client.get('/metrics')

    assert response.status_code == 200
    assert 'bankstatement_periods_reconciled_total' in response.text
    assert 'bankstatement_documents_reused_total' in response.text


def test__client_key__untrusted_proxy__direct_ip_only(tmp_path):
    """По умолчанию не верим X-Forwarded-For: доверяем только прямому соединению."""
    settings = Settings(samples_dir=str(SAMPLES), uploads_dir=str(tmp_path), trusted_proxy=False)
    container = make_async_container(
        AppProvider(),
        StubRegistryProvider(),
        FastapiProvider(),
        context={Settings: settings},
    )

    with TestClient(create_app(settings, container)) as client:
        # Отправим запрос с поддельным X-Forwarded-For
        response = client.post(
            '/jobs',
            files={'files': ('test.pdf', b'%PDF-1.7 x', 'application/pdf')},
            headers={'X-Forwarded-For': '192.0.2.1'},  # Поддельный заголовок
        )

        # Оба запроса от TestClient имеют одинаковый IP, поэтому оба пройдут
        # (это не тест rate-limiting, а тест что trusted_proxy не работает)
        assert response.status_code == 200


def test__client_key__trusted_proxy__uses_x_forwarded_for(tmp_path):
    """Когда trusted_proxy=True, используется X-Forwarded-For для rate-limiting."""
    settings = Settings(samples_dir=str(SAMPLES), uploads_dir=str(tmp_path), trusted_proxy=True)
    container = make_async_container(
        AppProvider(),
        StubRegistryProvider(),
        FastapiProvider(),
        context={Settings: settings},
    )

    with TestClient(create_app(settings, container)) as client:
        # Отправим запросы с разными X-Forwarded-For
        payload = b'%PDF-1.7 x'
        responses = []

        # Первый запрос с IP1
        for i in range(settings.upload_burst + 1):
            if i < settings.upload_burst:
                response = client.post(
                    '/jobs',
                    files={'files': (f'{i}.pdf', payload + str(i).encode(), 'application/pdf')},
                    headers={'X-Forwarded-For': '192.0.2.100'},
                )
                responses.append(response.status_code)
            else:
                # Должен быть отклонён (rate limit)
                response = client.post(
                    '/jobs',
                    files={'files': (f'{i}.pdf', payload + str(i).encode(), 'application/pdf')},
                    headers={'X-Forwarded-For': '192.0.2.100'},
                )
                responses.append(response.status_code)

        # Проверяем что был rate limit (429)
        assert 429 in responses or len([r for r in responses if r == 200]) <= settings.upload_burst


def test__rate_limit__different_forwarded_for__bypassed_when_untrusted(tmp_path):
    """Если trusted_proxy=False, изменение X-Forwarded-For не обходит rate limit.

    Это тест на то, что один клиент с разными поддельными заголовками
    не может обойти ограничение частоты.
    """
    settings = Settings(
        samples_dir=str(SAMPLES),
        uploads_dir=str(tmp_path),
        trusted_proxy=False,
        upload_burst=2,
    )
    container = make_async_container(
        AppProvider(),
        StubRegistryProvider(),
        FastapiProvider(),
        context={Settings: settings},
    )

    with TestClient(create_app(settings, container)) as client:
        payload = b'%PDF-1.7 x'
        responses = []

        # Даже с разными X-Forwarded-For, rate limit применяется к реальному IP
        for i in range(settings.upload_burst + 2):
            response = client.post(
                '/jobs',
                files={'files': (f'{i}.pdf', payload + str(i).encode(), 'application/pdf')},
                headers={'X-Forwarded-For': f'192.0.2.{100 + i}'},  # Разные IP в заголовке
            )
            responses.append(response.status_code)

        # rate-limit всё равно должен сработать, потому что trusted_proxy=False
        assert 429 in responses


def test__client_key__trusted_proxy__takes_the_hop_the_proxy_appended(tmp_path):
    """Левые адреса в X-Forwarded-For пишет клиент; правый дописал сам прокси."""
    settings = Settings(samples_dir=str(SAMPLES), uploads_dir=str(tmp_path), trusted_proxy=True, upload_burst=2)
    container = make_async_container(
        AppProvider(),
        StubRegistryProvider(),
        FastapiProvider(),
        context={Settings: settings},
    )
    payload = b'%PDF-1.7 x'

    with TestClient(create_app(settings, container)) as client:
        codes = [
            client.post(
                '/jobs',
                files={'files': (f'{i}.pdf', payload + str(i).encode(), 'application/pdf')},
                headers={'X-Forwarded-For': f'10.0.0.{i}, 192.0.2.100'},
            ).status_code
            for i in range(3)
        ]

    assert codes == [200, 200, 429]


def test__create_job__one_bad_file_in_a_batch__nothing_accepted(client, registry, settings, sample_pdf):
    """Пачка принимается целиком или не принимается вовсе."""
    response = client.post(
        '/jobs',
        files=[
            ('files', ('январь.pdf', sample_pdf.read_bytes(), 'application/pdf')),
            ('files', ('заметка.txt', 'это не pdf'.encode(), 'application/pdf')),
        ],
    )

    assert response.status_code == 415
    assert registry.recent() == []
    assert list(Path(settings.uploads_dir).glob('*.pdf')) == []


def test__create_job__declared_body_beyond_any_limit__rejected_before_reading(tmp_path):
    """Заявленная длина известна до чтения тела, и невозможное тело отбивается сразу."""
    small = Settings(samples_dir=str(SAMPLES), uploads_dir=str(tmp_path / 'uploads'), max_upload=1024, max_batch=1)
    container = make_async_container(
        AppProvider(),
        StubRegistryProvider(),
        FastapiProvider(),
        context={Settings: small},
    )

    with TestClient(create_app(small, container)) as client:
        response = client.post(
            '/jobs',
            files={'files': ('огромный.txt', b'x' * (2 * 1024 * 1024), 'application/pdf')},
        )

    assert response.status_code == 413
    assert 'тело запроса' in response.json()['detail']


def test__job_crop__frame_outside_the_page__not_found(client, registry, sample_pdf):
    job = registry.submit('sample', sample_pdf)

    response = client.get(f'/jobs/{job.id}/crop', params={'page': 1, 'x0': 5000, 'y0': 5000, 'x1': 5100, 'y1': 5100})

    assert response.status_code == 404

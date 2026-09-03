from pathlib import Path

import pytest

from bankstatement.ingest import Ingestion, TooManyPagesError
from bankstatement.recognize.base import Word

SAMPLES = Path('samples')


class FakeRecognizer:
    def __init__(self, name, pages):
        self.name = name
        self._pages = pages
        self.calls = 0

    def available_for(self, page_index):
        return page_index in self._pages

    def words(self, page_index):
        self.calls += 1

        return self._pages.get(page_index, [])


@pytest.fixture
def fake_ingestion(monkeypatch):
    def build(ladder, on_page=None):
        ingestion = Ingestion.__new__(Ingestion)
        ingestion.path = Path('fake.pdf')
        ingestion.doc = None
        ingestion.ladder = ladder
        ingestion.on_page = on_page
        ingestion.workers = 1
        ingestion._words = {}
        ingestion._reads = {}
        ingestion._read = set()
        ingestion._in_flight = 0

        return ingestion

    return build


def word(text):
    return Word(text=text, x0=0, y0=0, x1=10, y1=10, page=0, engine='fake')


def test__words__cheapest_engine_that_can_read__used(fake_ingestion):
    """Лестница начинается с бесплатного уровня и поднимается только по нужде."""
    cheap = FakeRecognizer('textlayer', {0: [word('A')]})
    costly = FakeRecognizer('tesseract', {0: [word('B')], 1: [word('C')]})
    ingestion = fake_ingestion([cheap, costly])

    assert [w.text for w in ingestion.words(0)] == ['A']
    assert [w.text for w in ingestion.words(1)] == ['C']
    assert ingestion.engine_stats() == {'textlayer': 1, 'tesseract': 1}


def test__words__escalation__skips_cheaper_levels(fake_ingestion):
    cheap = FakeRecognizer('textlayer', {0: [word('A')]})
    costly = FakeRecognizer('tesseract', {0: [word('B')]})
    ingestion = fake_ingestion([cheap, costly])

    assert [w.text for w in ingestion.words(0, min_level=1)] == ['B']


def test__words__nothing_readable__empty(fake_ingestion):
    ingestion = fake_ingestion([FakeRecognizer('textlayer', {})])

    assert ingestion.words(0) == []
    assert ingestion.engine_stats() == {}


def test__words__repeated_call__served_from_cache(fake_ingestion):
    costly = FakeRecognizer('tesseract', {0: [word('B')]})
    ingestion = fake_ingestion([costly])

    ingestion.words(0)
    ingestion.words(0)

    assert costly.calls == 1


def test__lines__grouped_from_words(fake_ingestion):
    ingestion = fake_ingestion([FakeRecognizer('textlayer', {0: [word('A'), word('B')]})])

    assert len(ingestion.lines(0)) == 1


@pytest.mark.samples
def test__ingestion__text_layer_document__no_ocr_needed():
    path = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    with Ingestion(path) as ingestion:
        ingestion.words(0)
        assert ingestion.engine_stats() == {'textlayer': 1}
        assert ingestion.page_count == 6


def test__words__progress_callback__reports_every_page(fake_ingestion):
    """Распознавание идёт минутами — интерфейсу нужен признак жизни."""
    seen = []
    ingestion = fake_ingestion([FakeRecognizer('tesseract', {0: [word('A')]})], on_page=seen.append)
    ingestion.doc = type('Doc', (), {'page_count': 1})()

    ingestion.words(0)

    assert [(u.done, u.total, u.engine) for u in seen] == [(1, 1, 'tesseract')]


def test__words__broken_callback__does_not_break_extraction(fake_ingestion):
    """Наблюдатель вспомогательный: уронить из-за него разбор нельзя."""

    def explode(*_args):
        raise RuntimeError('наблюдатель упал')

    ingestion = fake_ingestion([FakeRecognizer('tesseract', {0: [word('A')]})], on_page=explode)
    ingestion.doc = type('Doc', (), {'page_count': 1})()

    assert [w.text for w in ingestion.words(0)] == ['A']
    assert ingestion.on_page is None


def test__prefetch__many_pages__all_cached_and_counted(fake_ingestion):
    """Страницы независимы: их чтение — единственное место с кратным выигрышем."""
    seen = []
    pages = {index: [word(f'p{index}')] for index in range(5)}
    ingestion = fake_ingestion([FakeRecognizer('tesseract', pages)], on_page=seen.append)
    ingestion.doc = type('Doc', (), {'page_count': 5})()
    ingestion.workers = 4

    ingestion.prefetch(range(5))

    assert [w.text for w in ingestion.words(3)] == ['p3']
    # Нулевой отсчёт объявляет объём работы до её начала, дальше счётчик растёт
    # монотонно — по готовности страниц, а не по их номерам.
    assert [update.done for update in seen] == [0, 1, 2, 3, 4, 5]
    assert all(update.total == 5 for update in seen)
    assert seen[0].in_flight > 0  # объём работы виден до первого результата


def test__prefetch__already_cached__engine_not_called_again(fake_ingestion):
    recognizer = FakeRecognizer('tesseract', {0: [word('A')], 1: [word('B')]})
    ingestion = fake_ingestion([recognizer])
    ingestion.doc = type('Doc', (), {'page_count': 2})()

    ingestion.prefetch([0, 1])
    ingestion.prefetch([0, 1])

    assert recognizer.calls == 2


def test__prefetch__single_worker__still_reads_everything(fake_ingestion):
    ingestion = fake_ingestion([FakeRecognizer('tesseract', {0: [word('A')], 1: [word('B')]})])
    ingestion.doc = type('Doc', (), {'page_count': 2})()
    ingestion.workers = 1

    ingestion.prefetch([0, 1])

    assert [w.text for w in ingestion.words(1)] == ['B']


def test__default_workers__environment_override__respected(monkeypatch):
    """Число потоков приходится ограничивать снаружи: контейнер, общая машина."""
    from bankstatement.ingest import MAX_WORKERS, default_workers

    monkeypatch.setenv('BANKSTATEMENT_WORKERS', '3')
    assert default_workers() == 3

    monkeypatch.setenv('BANKSTATEMENT_WORKERS', 'нет')
    assert 1 <= default_workers() <= MAX_WORKERS

    monkeypatch.delenv('BANKSTATEMENT_WORKERS')
    assert 1 <= default_workers() <= MAX_WORKERS


def test__tesseract_recognizer__has_a_page_timeout():
    """Битая страница не должна занимать процесс навсегда."""
    pytest.importorskip('pytesseract')

    from bankstatement.recognize.tesseract import PAGE_TIMEOUT, TesseractRecognizer

    recognizer = TesseractRecognizer(doc=None, timeout=5.0)

    assert recognizer._timeout == 5.0
    assert PAGE_TIMEOUT > 0


@pytest.mark.samples
def test__ingestion__max_pages_exceeded__raises_error():
    """Документ, превышающий лимит страниц, отклоняется с ошибкой."""
    path = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    # Great Lakes имеет 6 страниц, отклонять при max_pages=5
    with pytest.raises(TooManyPagesError) as exc_info:
        Ingestion(path, max_pages=5)

    assert '6' in str(exc_info.value)
    assert '5' in str(exc_info.value)


@pytest.mark.samples
def test__ingestion__max_pages_exactly__accepted():
    """Документ с точным числом страниц принимается."""
    path = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    # Great Lakes имеет 6 страниц, должна приниматься при max_pages=6
    with Ingestion(path, max_pages=6) as ingestion:
        assert ingestion.page_count == 6


@pytest.mark.samples
def test__ingestion__max_pages_none__no_limit():
    """max_pages=None означает отсутствие ограничения."""
    path = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    with Ingestion(path, max_pages=None) as ingestion:
        assert ingestion.page_count == 6


def test__engine_stats__page_reread_by_a_higher_rung__counted_for_both(fake_ingestion):
    """Строка о цене считает чтения, а не последнего читателя: перечит стоил отдельно."""
    cheap = FakeRecognizer('textlayer', {0: [word('a')]})
    costly = FakeRecognizer('tesseract', {0: [word('b')]})
    ingestion = fake_ingestion([cheap, costly])

    ingestion.words(0)
    ingestion.words(0, min_level=1)

    assert ingestion.engine_stats() == {'textlayer': 1, 'tesseract': 1}

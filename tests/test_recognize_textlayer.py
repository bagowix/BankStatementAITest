"""Тесты для recognize/textlayer.py"""

from pathlib import Path

import pytest

from bankstatement.recognize.textlayer import (
    MIN_CHARS_PER_PAGE,
    TextLayerRecognizer,
)

SAMPLES = Path('samples')


def test__textlayer__min_chars_per_page__constant():
    """MIN_CHARS_PER_PAGE должен быть определён и равен 100."""
    assert MIN_CHARS_PER_PAGE == 100


def test__available_for__full_page_picture_under_real_text__still_available():
    """Декоративная подложка на всю страницу текстовый слой не отменяет.

    Синтетическая выписка с картинкой-фоном теряла безупречный текстовый слой и
    уходила в OCR, который её разреженную вёрстку не прочёл вовсе.
    """
    import io

    import pymupdf
    from PIL import Image

    doc = pymupdf.open()
    page = doc.new_page()
    picture = io.BytesIO()
    Image.new('RGB', (8, 8), (240, 240, 240)).save(picture, format='PNG')
    page.insert_image(page.rect, stream=picture.getvalue())

    for row in range(8):
        page.insert_text((40, 60 + row * 14), '01/02/2025 DEPOSIT FROM CUSTOMER 1,234.56 10,000.00')

    recognizer = TextLayerRecognizer(doc)

    assert recognizer.available_for(0)

    doc.close()


@pytest.mark.samples
def test__available_for__great_lakes__pass_all_pages():
    """Great Lakes: текст + 0% изображений — все страницы должны пройти."""
    path = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    import pymupdf

    doc = pymupdf.open(path)
    recognizer = TextLayerRecognizer(doc)

    # Great Lakes имеет 6 страниц с полным текстовым слоем
    for page_index in range(doc.page_count):
        assert recognizer.available_for(page_index), f'страница {page_index} должна пройти'

    doc.close()


@pytest.mark.samples
def test__available_for__fulton_april_2021__fail_all_pages():
    """April 2021 (Fulton): мало текста (103 символа на 15 страниц) — все должны упасть."""
    path = SAMPLES / 'April 2021 eStatement.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    import pymupdf

    doc = pymupdf.open(path)
    recognizer = TextLayerRecognizer(doc)

    # April 2021 имеет только артефакты текстового слоя
    for page_index in range(doc.page_count):
        # Собираем количество символов на этой странице
        text = doc[page_index].get_text().strip()

        if len(text) < MIN_CHARS_PER_PAGE:
            assert not recognizer.available_for(page_index), f'страница {page_index} должна упасть'

    doc.close()


@pytest.mark.samples
def test__available_for__renasant_image_statement__fail_all_pages():
    """Image Statement (Renasant): скан без текстового слоя — все должны упасть."""
    path = SAMPLES / 'Image Statement Jan 31 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    import pymupdf

    doc = pymupdf.open(path)
    recognizer = TextLayerRecognizer(doc)

    # Image Statement это скан с вложенными изображениями
    for page_index in range(doc.page_count):
        assert not recognizer.available_for(page_index), f'страница {page_index} должна упасть'

    doc.close()


@pytest.mark.samples
def test__available_for__binder__fail_all_pages():
    """Binder: скан без текстового слоя — все должны упасть."""
    path = SAMPLES / 'Binder 2.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    import pymupdf

    doc = pymupdf.open(path)
    recognizer = TextLayerRecognizer(doc)

    # Binder это полностью отсканированный документ
    for page_index in range(doc.page_count):
        assert not recognizer.available_for(page_index), f'страница {page_index} должна упасть'

    doc.close()


def test__available_for__empty_page_with_stamp__fails_min_chars():
    """Дефект №9: штамп конфиденциальности (86 символов) не должен пройти."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()

    # Добавляем текст как штамп (примерно 86 символов)
    stamp_text = 'CONFIDENTIAL - PRODUCED PURSUANT TO SUBPOENA - PAGE 1 OF 3'
    page.insert_text((100, 100), stamp_text)

    recognizer = TextLayerRecognizer(doc)

    # Штамп имеет ~60 символов, что < 100, поэтому должна упасть
    assert not recognizer.available_for(0)

    doc.close()


@pytest.mark.samples
def test__textlayer_recognizer__words__great_lakes__extracted():
    """Great Lakes: слова извлекаются из текстового слоя."""
    path = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'

    if not path.exists():
        pytest.skip('нет образцов')

    import pymupdf

    doc = pymupdf.open(path)
    recognizer = TextLayerRecognizer(doc)

    words = recognizer.words(0)

    assert len(words) > 0
    assert all(hasattr(w, 'text') for w in words)
    assert all(w.engine == 'textlayer' for w in words)
    assert all(w.confidence == 100.0 for w in words)

    doc.close()

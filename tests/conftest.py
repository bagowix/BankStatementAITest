"""Фабрики для синтетических страниц.

Тесты строятся на словах с координатами, а не на PDF: так проверяется логика
разбора, а не установленный в системе Tesseract. Реальные документы проверяются
отдельно, под маркерами `ocr` и `samples`.
"""

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from bankstatement.layout import Line, group_lines
from bankstatement.models import Summary
from bankstatement.recognize.base import Word

LINE_HEIGHT = 10.0
CHAR_WIDTH = 6.0


def word(text: str, x0: float, y0: float, *, page: int = 0, height: float = LINE_HEIGHT) -> Word:
    return Word(
        text=text,
        x0=x0,
        y0=y0,
        x1=x0 + CHAR_WIDTH * len(text),
        y1=y0 + height,
        page=page,
        confidence=95.0,
        engine='test',
    )


def text_line(text: str, y: float, *, x0: float = 10.0, page: int = 0, gap: float = CHAR_WIDTH) -> list[Word]:
    """Строка из текста: слова расставляются слева направо через один пробел."""
    out: list[Word] = []
    cursor = x0

    for token in text.split(' '):
        out.append(word(token, cursor, y, page=page))
        cursor = out[-1].x1 + gap

    return out


def page(*texts: str, page_index: int = 0) -> list[Line]:
    words: list[Word] = []

    for row, text in enumerate(texts):
        words.extend(text_line(text, 20.0 + row * (LINE_HEIGHT * 2), page=page_index))

    return group_lines(words)


@pytest.fixture
def summary() -> Summary:
    return Summary(
        beginning_balance=Decimal('100.00'),
        ending_balance=Decimal('120.00'),
        deposits_total=Decimal('50.00'),
        withdrawals_total=Decimal('30.00'),
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Пропустить тесты, которым нужен внешний бинарь, если его нет.

    Маркер `ocr` без этого был бы обещанием на бумаге: без Tesseract такой тест
    падал бы с ошибкой распознавания вместо честного пропуска, и на голом
    клоне набор выглядел бы сломанным.
    """
    if 'ocr' in item.keywords and shutil.which('tesseract') is None:
        pytest.skip('нет бинаря tesseract')

    # Реальные выписки в репозиторий не входят: тест, которому они нужны,
    # пропускается на голом клоне и в CI, а не падает на отсутствии файла.
    if 'samples' in item.keywords and not any(SAMPLES_DIR.glob('*.pdf')):
        pytest.skip('нет образцов в samples/')


SAMPLES_DIR = Path('samples')

STATEMENT_LINES = (
    'Sample Bank',
    'Business Checking Statement',
    'Account ****1234',
    'Statement period: April 01, 2025 - April 30, 2025',
    'Beginning balance $100.00',
    'Deposits and credits $50.00',
    'Withdrawals and debits $30.00',
    'Ending balance $120.00',
    'Date Description Amount Balance',
    '04/01/2025 DEPOSIT 50.00 150.00',
    '04/02/2025 PAYMENT -30.00 120.00',
)
"""Выписка на одну страницу, которая сходится классом A: остаток за каждой строкой."""


def write_statement_pdf(path: Path) -> Path:
    """PDF с текстовым слоем вместо реальной выписки.

    Стенд и CLI проверяются на нём: настоящие документы в репозиторий не
    входят, а тестам стенда нужен лишь файл, который проходит весь конвейер.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()

    for row, text in enumerate(STATEMENT_LINES):
        page.insert_text((40, 60 + row * 16), text, fontname='cour', fontsize=10)

    doc.save(path)
    doc.close()

    return path


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Синтетическая выписка в своей папке образцов."""
    folder = tmp_path / 'samples'
    folder.mkdir()

    return write_statement_pdf(folder / 'Sample Bank 2025.pdf')

import pymupdf
import pytest

from bankstatement.recognize.base import MAX_RENDER_PIXELS, MAX_UPSCALED_PIXELS, render_page


def document(width: float, height: float) -> pymupdf.Document:
    doc = pymupdf.open()
    doc.new_page(width=width, height=height)

    return doc


def test__render_page__letter_page__rendered_at_measured_settings():
    """Обычная страница проходит бюджеты насквозь: 300 dpi и апскейл ×2 выбраны замером."""
    with document(612, 792) as doc:
        image, px_per_point = render_page(doc, 0, dpi=300, scale=2)

    assert (image.width, image.height) == (int(612 * 300 / 72) * 2, int(792 * 300 / 72) * 2)
    assert px_per_point == (300 / 72) * 2


def test__render_page__large_but_real_page__not_downscaled():
    """Страница крупного скана обязана читаться в полном масштабе.

    Первая версия бюджета была занижена и молча уменьшала масштаб на реальном
    образце Renasant: распознавание портилось, и сведённый период разваливался.
    Здесь размер страницы взят такой же по площади.
    """
    with document(900, 957) as doc:
        image, px_per_point = render_page(doc, 0, dpi=300, scale=2)

    assert px_per_point == (300 / 72) * 2
    assert image.width * image.height > MAX_RENDER_PIXELS


def test__render_page__page_of_absurd_size__fits_both_budgets():
    """Предел PDF — 14400 pt: при 300 dpi это 3.6 Гпикс и десяток гигабайт памяти."""
    with document(7000, 7000) as doc:
        image, px_per_point = render_page(doc, 0, dpi=300, scale=2)

    assert image.width * image.height <= MAX_UPSCALED_PIXELS
    assert px_per_point < (300 / 72) * 2


def test__render_page__downscaled__coefficient_matches_the_image():
    """Провенанс держится на этом коэффициенте: рамка строки считается через него.

    Разъедься он с настоящим масштабом изображения — все bbox уехали бы, причём
    молча и только на больших страницах.
    """
    with document(7000, 7000) as doc:
        image, px_per_point = render_page(doc, 0, dpi=300, scale=2)

    assert image.width == pytest.approx(7000 * px_per_point, abs=2)
    assert image.height == pytest.approx(7000 * px_per_point, abs=2)

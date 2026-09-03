"""Единый интерфейс распознавания.

Всё, что нужно разбору выше по стеку, — это токены с координатами. Откуда они
взялись (текстовый слой, локальный OCR, облачная модель), разбор знать не должен;
иначе замена движка перестаёт быть заменой одной детали.
"""

import logging
import math
import threading
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

import pymupdf
from PIL import Image

log = logging.getLogger(__name__)

MAX_RENDER_PIXELS: Final[int] = 40_000_000
"""Потолок пиксмапа, который рисует MuPDF, — здесь и находится настоящий расход
памяти. Обычная страница его не касается: US Letter при 300 dpi занимает 8.4
Мпикс, самый крупный из образцов — 15. Порог нужен от другого: страница
предельного для PDF размера 14400 pt дала бы 3.6 Гпикс и порядка десяти гигабайт
ещё до обработки, а таких страниц в двенадцать потоков достаточно, чтобы
исчерпать память машины."""

MAX_UPSCALED_PIXELS: Final[int] = 160_000_000
"""Потолок изображения после апскейла. Замеренная конфигурация (300 dpi ×2) на
самом крупном образце даёт 60 Мпикс, поэтому реальных документов порог не
касается — он срабатывает только на патологической странице. Занижать его нельзя:
апскейл ×2 выбран замером, и молчаливое уменьшение масштаба портит распознавание
там, где оно раньше было точным."""

MIN_DPI: Final[int] = 72
"""Ниже этого распознавать нечего: текст перестаёт читаться совсем."""


@dataclass(frozen=True, slots=True)
class Word:
    """Токен с рамкой в координатах страницы (PDF-точки, начало — левый верх)."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
    confidence: float | None = None
    engine: str = 'unknown'

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@runtime_checkable
class Recognizer(Protocol):
    """Реализации: текстовый слой, Tesseract, (при наличии ключа) модель по картинке."""

    name: str

    def available_for(self, page_index: int) -> bool:
        """Может ли этот движок вообще работать с этой страницей."""
        ...

    def words(self, page_index: int) -> list[Word]: ...


RENDER_LOCK = threading.Lock()
"""PyMuPDF не рассчитан на одновременный доступ к одному документу из разных
потоков. Рендер занимает малую долю времени страницы (основное — распознавание),
поэтому дешевле сериализовать именно его, чем открывать документ на каждый поток
и платить памятью."""


def fit_dpi(width_pt: float, height_pt: float, dpi: int) -> int:
    """Понизить dpi, если пиксмап страницы не влезает в бюджет.

    Args:
        width_pt: Ширина страницы в точках PDF.
        height_pt: Высота страницы в точках PDF.
        dpi: Желаемое разрешение рендера.

    Returns:
        Разрешение, при котором пиксмап укладывается в `MAX_RENDER_PIXELS`.
    """
    pixels = (width_pt * dpi / 72) * (height_pt * dpi / 72)

    if pixels <= MAX_RENDER_PIXELS or pixels <= 0:
        return dpi

    return max(MIN_DPI, int(dpi * math.sqrt(MAX_RENDER_PIXELS / pixels)))


def fit_scale(width: int, height: int, scale: int) -> float:
    """Понизить апскейл, если изображение после него не влезает в бюджет."""
    pixels = width * height * scale * scale

    if pixels <= MAX_UPSCALED_PIXELS or pixels <= 0:
        return float(scale)

    return max(1.0, scale * math.sqrt(MAX_UPSCALED_PIXELS / pixels))


def render_page(
    doc: pymupdf.Document, page_index: int, dpi: int = 300, scale: int = 2, sharpen: int = 1
) -> tuple[Image.Image, float]:
    """Рендер страницы под OCR плюс коэффициент перевода пикселей в точки PDF.

    Параметры не случайны: 300 dpi с апскейлом x2 и одним проходом резкости —
    единственная конфигурация, которая на замерах взяла и скан 144 dpi (Binder),
    и битональный 318 dpi (Renasant) без единой потери. См. README.

    Оба бюджета пикселей срабатывают только на странице ненормального размера:
    предел PDF — 14400 pt, и такая страница при 300 dpi просит 3.6 Гпикс. Сначала
    понижается dpi, потому что память тратит пиксмап MuPDF, и лишь потом апскейл.

    Args:
        doc: Открытый документ.
        page_index: Номер страницы, считая с нуля.
        dpi: Разрешение рендера.
        scale: Во сколько раз увеличить изображение после рендера.
        sharpen: Сколько проходов резкости сделать.

    Returns:
        Изображение страницы и число пикселей на точку PDF.
    """
    import io

    from PIL import ImageFilter

    with RENDER_LOCK:
        page = doc[page_index]
        rect = page.rect
        effective_dpi = fit_dpi(rect.width, rect.height, dpi)
        pixmap = page.get_pixmap(dpi=effective_dpi)
        payload = pixmap.tobytes('png')

    image = Image.open(io.BytesIO(payload)).convert('L')
    effective_scale = fit_scale(image.width, image.height, scale)

    if effective_dpi != dpi or effective_scale != scale:
        log.warning(
            'страница %d великовата: dpi %d -> %d, апскейл %.2f -> %.2f',
            page_index,
            dpi,
            effective_dpi,
            float(scale),
            effective_scale,
        )

    if effective_scale != 1.0:
        image = image.resize(
            (int(image.width * effective_scale), int(image.height * effective_scale)),
            Image.Resampling.LANCZOS,
        )

    for _ in range(sharpen):
        image = image.filter(ImageFilter.SHARPEN)

    return image, (effective_dpi / 72) * effective_scale

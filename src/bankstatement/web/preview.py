"""Вырезка из исходного PDF вокруг найденной строки.

Провенанс перестаёт быть строчкой в JSON, когда его видно: интерфейс показывает
тот самый кусок документа, из которого взята сумма. Для аудита это и есть
основная работа — сверить цифру с бумагой.
"""

import io
from pathlib import Path
from typing import Final

import pymupdf
from PIL import Image, ImageDraw

from bankstatement.recognize.base import fit_dpi

MARGIN: Final[float] = 12.0
"""Отступ вокруг строки в точках PDF: без него вырезка выглядит обрезком, а с
ним видно соседние колонки и понятно, что это за строка."""

DPI: Final[int] = 170
FRAME: Final[tuple[int, int, int]] = (208, 74, 62)


def crop(path: Path, page_index: int, bbox: tuple[float, float, float, float]) -> bytes:
    """PNG с областью строки и рамкой вокруг неё."""
    with pymupdf.open(path) as doc:
        page = doc[page_index]
        x0, y0, x1, y1 = bbox
        clip = pymupdf.Rect(x0 - MARGIN, y0 - MARGIN, x1 + MARGIN, y1 + MARGIN) & page.rect

        if clip.width <= 0 or clip.height <= 0:
            raise ValueError('область вне страницы')

        # Бюджет пикселей тот же, что у распознавания: рамку задаёт клиент, и
        # рамка во всю страницу предельного размера стоила бы гигабайты памяти.
        dpi = fit_dpi(clip.width, clip.height, DPI)
        pixmap = page.get_pixmap(dpi=dpi, clip=clip)
        image = Image.open(io.BytesIO(pixmap.tobytes('png'))).convert('RGB')

    scale = dpi / 72
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [
            ((x0 - clip.x0) * scale, (y0 - clip.y0) * scale),
            ((x1 - clip.x0) * scale, (y1 - clip.y0) * scale),
        ],
        outline=FRAME,
        width=2,
    )

    out = io.BytesIO()
    image.save(out, format='PNG')

    return out.getvalue()

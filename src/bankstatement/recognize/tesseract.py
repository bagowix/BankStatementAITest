"""Локальный OCR.

Конфигурация выбрана замером на всех четырёх документах, а не по умолчанию:
рендер 300 dpi -> апскейл x2 -> один проход резкости -> --psm 4. На этой связке
Binder (скан 144 dpi) и Renasant (битональ 318 dpi) отдают все эталонные числа
без единого ложного токена. Ни whitelist символов, ни бинаризация, ни больший
dpi результат не улучшают — whitelist его ухудшает, ломая языковую модель.
"""

from typing import Final

import pymupdf
import pytesseract
from pytesseract import Output

from bankstatement.recognize.base import Word, render_page

PSM: Final[int] = 4
"""Одна колонка текста переменного размера. --psm 6 (однородный блок) на этих
макетах теряет строки: у выписки колонки разной плотности."""

PAGE_TIMEOUT: Final[float] = 120.0
"""Предохранитель на страницу. Обычная страница читается за секунды, так что
порог не мешает работе, но не даёт битому или враждебному документу занять
процесс навсегда — а без ограничения сверху такой документ его займёт."""


class TesseractRecognizer:
    def __init__(
        self,
        doc: pymupdf.Document,
        dpi: int = 300,
        scale: int = 2,
        sharpen: int = 1,
        psm: int = PSM,
        name: str = 'tesseract',
        timeout: float = PAGE_TIMEOUT,
    ) -> None:
        self.name = name
        self._doc = doc
        self._dpi = dpi
        self._scale = scale
        self._sharpen = sharpen
        self._psm = psm
        self._timeout = timeout
        self._cache: dict[int, list[Word]] = {}

    def available_for(self, page_index: int) -> bool:
        return True

    def words(self, page_index: int) -> list[Word]:
        if page_index in self._cache:
            return self._cache[page_index]

        image, ppp = render_page(self._doc, page_index, dpi=self._dpi, scale=self._scale, sharpen=self._sharpen)
        data = pytesseract.image_to_data(
            image,
            config=f'--psm {self._psm}',
            output_type=Output.DICT,
            timeout=self._timeout,
        )

        out: list[Word] = []

        for i, raw in enumerate(data['text']):
            text = raw.strip()

            if not text:
                continue

            confidence = float(data['conf'][i])

            if confidence < 0:
                continue

            left, top = data['left'][i], data['top'][i]
            width, height = data['width'][i], data['height'][i]
            out.append(
                Word(
                    text=text,
                    x0=left / ppp,
                    y0=top / ppp,
                    x1=(left + width) / ppp,
                    y1=(top + height) / ppp,
                    page=page_index,
                    confidence=confidence,
                    engine=self.name,
                )
            )

        self._cache[page_index] = out

        return out

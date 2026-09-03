"""Чтение встроенного текстового слоя PDF — нулевая стоимость и точные координаты."""

from typing import Final

import pymupdf

from bankstatement.recognize.base import RENDER_LOCK, Word

MIN_CHARS_PER_PAGE: Final[int] = 100
"""Минимум текста на странице для признания её текстовой, а не сканом.

Fulton April 2021 (15 страниц, 103 символа всего): скан с вектор-глифами.
Каждая страница имеет 0–7 символов, в основном просто артефакты OCR.
Признание текстовым слоем такого документа приведёт к пустому результату.

Great Lakes (6 страниц, 17000+ символов): настоящая выписка, 2700+ на странице.
Текстовый слой работает идеально.

Картинки на странице текстовый слой не отменяют. Раньше страница с
изображениями больше чем на пятую часть площади уходила сразу в OCR, и
синтетическая выписка с декоративной подложкой на всю страницу теряла
безупречный текстовый слой ради распознавания, которое её разреженную вёрстку
не прочло вовсе. Надёжность слоя проверяет оракул: не сошлось — страницу
перечитает следующая ступень."""


class TextLayerRecognizer:
    name = 'textlayer'

    def __init__(self, doc: pymupdf.Document) -> None:
        self._doc = doc

    def available_for(self, page_index: int) -> bool:
        """Есть ли на странице текстовый слой, за который стоит браться.

        Единственный критерий — количество текста: меньше ста символов даёт
        скан или штамп, и такую страницу читает Tesseract.
        """
        with RENDER_LOCK:
            text = self._doc[page_index].get_text().strip()

        return len(text) >= MIN_CHARS_PER_PAGE

    def words(self, page_index: int) -> list[Word]:
        out: list[Word] = []

        with RENDER_LOCK:
            found = self._doc[page_index].get_text('words')

        for x0, y0, x1, y1, text, *_ in found:
            if text.strip():
                out.append(
                    Word(
                        text=text.strip(),
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        page=page_index,
                        confidence=100.0,
                        engine=self.name,
                    )
                )

        return out

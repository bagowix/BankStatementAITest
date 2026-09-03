"""Ступень L2: страницу читает vision-модель.

Это последняя и единственная платная ступень лестницы, и включается она только
переменной окружения и ключом. Место её в архитектуре такое же, как у Tesseract:
она отдаёт слова с координатами, а всё, что выше — раскладка, шапка, гипотезы,
оракул, — не знает, кто их прочёл. Ответ модели проверяется той же арифметикой,
что и любой другой: перечит принимается, только если период сошёлся лучше.

Модель не отдаёт координат, поэтому просим её переписать страницу построчно в
порядке чтения и раскладываем строки по странице сами: вертикальная позиция
строки — её доля в порядке чтения, суммы прижаты к правому краю колонками. Это
приближение, и провенанс о нём не молчит: у таких слов движок `vision-...`, а
рамка показывает полосу страницы, а не точное место.

Бюджет страниц на документ ограничивает расход: подшивка на сто страниц с
девятью несведёнными выписками попросила бы перечитать больше половины.
"""

import logging
import os
import threading
from typing import Final, Protocol

import pymupdf

from bankstatement.models import BankStatementError
from bankstatement.money import is_money
from bankstatement.recognize.base import RENDER_LOCK, Word, fit_dpi

log = logging.getLogger(__name__)

PROVIDER_ENV: Final[str] = 'BANKSTATEMENT_VISION'
"""Провайдер: `openai` или `anthropic`. Пусто — ступени нет."""

MODEL_ENV: Final[str] = 'BANKSTATEMENT_VISION_MODEL'
"""Модели через запятую, по ступени на каждую; без переменной — умолчание провайдера."""

BUDGET_ENV: Final[str] = 'BANKSTATEMENT_VISION_PAGES'
"""Сколько страниц одного документа разрешено отдать каждой из моделей."""

DEFAULT_PAGE_BUDGET: Final[int] = 40
"""Умолчание бюджета. Несведённый период подшивки — десять страниц, и четырёх
таких периодов хватает, чтобы измерить пользу ступени, не оплачивая всю сотню."""

RENDER_DPI: Final[int] = 150
"""Разрешение картинки для модели. Ей не нужен апскейл, нужный Tesseract:
страница Letter при 150 dpi — это 1275×1650 пикселей, цифры читаются, а счёт за
изображение остаётся в пределах пары тысяч токенов."""

PROMPT: Final[str] = (
    'This is one page of a bank statement. Transcribe it exactly, line by line, in reading order, '
    'as plain text. Keep every table row on a single line: the date first, then the description, then '
    'the amounts in the order of their columns, separated by two spaces. Copy numbers exactly as '
    'printed, including commas, decimal points and minus signs; never round, sum or reorder them. '
    'Keep header lines such as beginning balance, ending balance, deposits and withdrawals with their '
    'amounts on the same line. Do not add commentary, markdown or column names that are not printed.'
)

LINE_HEIGHT: Final[float] = 10.0
CHAR_WIDTH: Final[float] = 6.0
RIGHT_EDGE: Final[float] = 590.0
COLUMN_STEP: Final[float] = 90.0
"""Синтетическая раскладка: ширина символа и высота строки как у обычной печати,
денежные колонки через девяносто точек от правого края — заведомо дальше, чем
склейка разорванного числа, и заведомо ближе, чем ширина страницы."""


class VisionClient(Protocol):
    """Контракт клиента модели: картинка страницы и подсказка — текст в ответ.

    Ровно один метод, чтобы провайдер менялся заменой одного класса, а тест
    подставлял заглушку без сети.
    """

    name: str

    def transcribe(self, image_png: bytes, prompt: str) -> str: ...


def render_for_model(doc: pymupdf.Document, page_index: int, dpi: int = RENDER_DPI) -> tuple[bytes, float, float]:
    """PNG страницы для модели и размер страницы в точках PDF."""
    with RENDER_LOCK:
        page = doc[page_index]
        rect = page.rect
        pixmap = page.get_pixmap(dpi=fit_dpi(rect.width, rect.height, dpi))
        payload: bytes = pixmap.tobytes('png')

    return payload, float(rect.width), float(rect.height)


def transcript_to_words(text: str, page_index: int, width: float, height: float, engine: str) -> list[Word]:
    """Построчный текст модели — в слова с синтетическими координатами.

    Строка занимает свою долю высоты страницы по порядку чтения; слова идут
    слева направо, а денежные токены в конце строки прижимаются к правому краю
    колонками. Этого достаточно, чтобы раскладка собрала строки, а разбор нашёл
    дату в начале и остаток в конце.

    Args:
        text: Ответ модели.
        page_index: Номер страницы, считая с нуля.
        width: Ширина страницы в точках PDF.
        height: Высота страницы в точках PDF.
        engine: Имя движка для провенанса.

    Returns:
        Слова страницы.
    """
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith('```')]

    if not lines:
        return []

    band = height / (len(lines) + 1)
    word_height = min(LINE_HEIGHT, band * 0.8)
    out: list[Word] = []

    for row, line in enumerate(lines):
        tokens = [token for token in line.split() if token != '|']
        y0 = band * (row + 0.5)
        trailing = _trailing_money(tokens)
        cursor = 10.0

        for index, token in enumerate(tokens):
            text_width = CHAR_WIDTH * len(token)

            if index in trailing:
                x1 = RIGHT_EDGE - COLUMN_STEP * trailing[index]
                x0 = x1 - text_width
            else:
                x0, x1 = cursor, cursor + text_width
                cursor = x1 + CHAR_WIDTH

            out.append(
                Word(
                    text=token,
                    x0=x0,
                    y0=y0,
                    x1=min(x1, width) if x1 > x0 else x0 + 1.0,
                    y1=y0 + word_height,
                    page=page_index,
                    confidence=None,
                    engine=engine,
                )
            )

    return out


def _trailing_money(tokens: list[str]) -> dict[int, int]:
    """Денежные токены в хвосте строки: индекс — во сколько колонок от правого края."""
    slots: dict[int, int] = {}

    for distance, token in enumerate(reversed(tokens)):
        if not is_money(token):
            break

        slots[len(tokens) - 1 - distance] = distance

    return slots


class VisionRecognizer:
    """Распознаватель поверх vision-модели с бюджетом страниц на документ."""

    def __init__(
        self, doc: pymupdf.Document, client: VisionClient, budget: int = DEFAULT_PAGE_BUDGET, name: str | None = None
    ) -> None:
        self.name = name or f'vision-{client.name}'
        self._doc = doc
        self._client = client
        self._budget = budget
        self._spent = 0
        self._lock = threading.Lock()

    @property
    def spent(self) -> int:
        """Сколько страниц уже отдано модели."""
        return self._spent

    def available_for(self, page_index: int) -> bool:
        with self._lock:
            return self._spent < self._budget

    def words(self, page_index: int) -> list[Word]:
        with self._lock:
            if self._spent >= self._budget:
                return []

            self._spent += 1

        image, width, height = render_for_model(self._doc, page_index)

        try:
            text = self._client.transcribe(image, PROMPT)
        except Exception:
            # Отказ модели или сети не роняет разбор: страница остаётся такой,
            # какой её прочла предыдущая ступень, а причина уходит в лог.
            log.exception('vision: страница %d не прочитана', page_index + 1)

            return []

        found = transcript_to_words(text, page_index, width, height, self.name)

        if not any(is_money(word.text) for word in found):
            # Ответ без единой суммы — это отказ модели («I'm unable to assist»)
            # или страница без таблицы; в обоих случаях страница остаётся за
            # предыдущей ступенью, а начало ответа уходит в лог, чтобы отказ не
            # остался незамеченным.
            log.warning('vision: страница %d, в ответе модели нет сумм: %r', page_index + 1, text.strip()[:120])

            return []

        log.info('vision: страница %d прочитана моделью, слов %d', page_index + 1, len(found))

        return found


def vision_from_env(doc: pymupdf.Document) -> list[VisionRecognizer]:
    """Платные ступени по переменным окружения; без провайдера — ни одной.

    Моделей может быть несколько, через запятую, и каждая — своя ступень:
    эскалация отдаёт следующей только те периоды, которые предыдущая не свела,
    поэтому сильная модель читает лишь остаток, а не всё подряд. Одна и та же
    модель дважды — тоже две ступени: её ответ от раза к разу различается, и
    второй проход дешёвой модели закрывает часть того, что не закрыл первый
    (замер в README).

    Раскрывать провайдера при импорте нельзя: без ключа пакет обязан работать
    как раньше, а SDK провайдера — необязательная зависимость.
    """
    provider = os.environ.get(PROVIDER_ENV, '').strip().lower()

    if not provider:
        return []

    names = [name.strip() for name in os.environ.get(MODEL_ENV, '').split(',') if name.strip()]
    models: list[str | None] = list(names) or [None]
    raw_budget = os.environ.get(BUDGET_ENV, '').strip()
    budget = int(raw_budget) if raw_budget.isdigit() and int(raw_budget) > 0 else DEFAULT_PAGE_BUDGET
    rungs: list[VisionRecognizer] = []

    for model in models:
        client, resolved = _client(provider, model)
        rungs.append(VisionRecognizer(doc, client, budget, name=f'vision-{provider}/{resolved}'))

    return rungs


def _client(provider: str, model: str | None) -> tuple[VisionClient, str]:
    """Клиент провайдера и имя модели, которую он в итоге вызывает."""
    if provider == 'openai':
        from bankstatement.recognize.openai_client import OpenAIVisionClient

        openai_client = OpenAIVisionClient.from_env(model)

        return openai_client, openai_client.model

    if provider == 'anthropic':
        from bankstatement.recognize.anthropic_client import AnthropicVisionClient

        anthropic_client = AnthropicVisionClient.from_env(model)

        return anthropic_client, anthropic_client.model

    raise BankStatementError(f'{PROVIDER_ENV}={provider!r}: провайдер неизвестен, допустимы openai и anthropic')

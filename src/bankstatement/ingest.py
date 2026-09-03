"""Документ и лестница распознавателей над ним.

Каскад устроен так, что цена извлечения зависит от трудности документа, а не от
числа страниц: сначала работает бесплатный уровень, и только страницы, которые
оракул забраковал, поднимаются на следующий.

Уровни нумеруются: 0 — текстовый слой PDF, 1 — локальный OCR, дальше место для
платных движков. Номер уровня — это его цена, поэтому порядок в списке
существенен и не должен меняться местами при настройке.
"""

import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

import pymupdf

from bankstatement.layout import Line, group_lines
from bankstatement.models import BankStatementError
from bankstatement.recognize.base import Recognizer, Word
from bankstatement.recognize.textlayer import TextLayerRecognizer
from bankstatement.recognize.vision import vision_from_env


@dataclass(frozen=True, slots=True)
class Progress:
    """Ход распознавания: что уже готово и что прямо сейчас в работе.

    Одних завершённых страниц мало. Читаем мы их пачкой во столько потоков,
    сколько есть ядер, и пачка финиширует почти одновременно: по одному лишь
    счётчику готовых работа полминуты выглядит стоящей, а потом прыгает через
    десяток страниц. Число страниц в работе показывает, что происходит на самом
    деле.
    """

    done: int
    in_flight: int
    total: int
    engine: str


type PageCallback = Callable[[Progress], None]

MAX_WORKERS: Final[int] = 12
"""Потолок параллельного чтения. Каждый поток держит развёрнутую страницу и свой
процесс Tesseract, так что на машине с большим числом ядер упор будет в память
раньше, чем в процессор. Замер на подшивке Ixonia: 31 с последовательно против
8 с в двенадцать потоков."""


def default_workers() -> int:
    """Сколько страниц читать одновременно.

    Переменная окружения нужна эксплуатации: в контейнере с урезанной памятью
    или на общей машине число потоков приходится ограничивать снаружи.
    """
    override = os.environ.get('BANKSTATEMENT_WORKERS')

    if override and override.isdigit() and int(override) > 0:
        return int(override)

    return max(1, min(os.cpu_count() or 4, MAX_WORKERS))


def default_ladder(doc: pymupdf.Document) -> list[Recognizer]:
    """Текстовый слой, Tesseract, Tesseract с другими параметрами.

    Третья ступень — не про качество движка, а про его настройку: у OCR нет
    режима, оптимального для всех макетов сразу, и страница, развалившаяся при
    разборе на колонки, часто читается целиком при разборе единым блоком.
    Ступень стоит нисколько и включается только для периодов, которые оракул
    забраковал, — прежде чем звать платную модель, разумно переспросить даром.

    Tesseract импортируется лениво: без установленного бинаря пакет всё равно
    должен импортироваться и работать на документах с текстовым слоем.

    Дальше — vision-модели, по ступени на модель: включаются переменной
    `BANKSTATEMENT_VISION` и ключом провайдера, см. `recognize/vision.py`.
    """
    ladder: list[Recognizer] = [TextLayerRecognizer(doc)]

    try:
        from bankstatement.recognize.tesseract import TesseractRecognizer
    except ImportError:  # pragma: no cover - зависит от окружения
        pass
    else:
        ladder.append(TesseractRecognizer(doc))
        ladder.append(TesseractRecognizer(doc, dpi=400, psm=6, name='tesseract-psm6'))

    # Платные ступени стоят последними и появляются только по переменной
    # окружения: без неё лестница такая же, как была. Каждая следующая модель
    # читает лишь то, что не свела предыдущая.
    ladder.extend(vision_from_env(doc))

    return ladder


class TooManyPagesError(BankStatementError):
    """Документ содержит больше страниц, чем допускается."""

    pass


class Ingestion:
    """Постраничный доступ к токенам с запоминанием того, кто их дал.

    Результат кэшируется по паре (страница, уровень): OCR одной страницы стоит
    порядка секунды, а разбор обращается к странице многократно.
    """

    def __init__(
        self,
        path: str | Path,
        recognizers: Sequence[Recognizer] | None = None,
        on_page: PageCallback | None = None,
        workers: int | None = None,
        max_pages: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.doc = pymupdf.open(self.path)
        page_count = int(self.doc.page_count)

        if max_pages is not None and page_count > max_pages:
            self.doc.close()

            raise TooManyPagesError(f'документ содержит {page_count} страниц, допускается не более {max_pages}')

        self.ladder: list[Recognizer] = list(recognizers) if recognizers is not None else default_ladder(self.doc)
        self.on_page = on_page
        self.workers = workers if workers is not None else default_workers()
        self._words: dict[tuple[int, int], list[Word]] = {}
        self._reads: dict[str, int] = {}
        self._read: set[int] = set()
        self._in_flight = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.doc.close()

    @property
    def page_count(self) -> int:
        return int(self.doc.page_count)

    def words(self, page_index: int, *, min_level: int = 0) -> list[Word]:
        """Токены страницы с уровня не ниже указанного.

        min_level — способ сказать «предыдущий движок не справился, дай
        следующий», не зная, какой именно движок отработал в прошлый раз.
        """
        key = (page_index, min_level)

        if key not in self._words:
            self._in_flight = max(self._in_flight, 1)
            found, engine = self._recognize(page_index, min_level)
            self._in_flight = max(self._in_flight - 1, 0)
            self._store(key, found, engine)

        return self._words[key]

    def prefetch(self, pages: Iterable[int], *, min_level: int = 0) -> None:
        """Прочитать страницы одновременно, сложив результат в тот же кэш.

        Страницы независимы, а распознавание одной идёт около секунды, так что
        это единственное место, где параллелизм даёт кратный выигрыш. Результаты
        складываются в кэш из главного потока: так и запись остаётся простой, и
        счётчик прогресса растёт монотонно.
        """
        missing = [page for page in pages if (page, min_level) not in self._words]

        if not missing:
            return

        workers = min(self.workers, len(missing))
        self._in_flight = min(workers, len(missing))
        self._announce('')  # объявить объём работы до её начала

        if len(missing) < 2 or workers < 2:
            for page in missing:
                self.words(page, min_level=min_level)

            self._in_flight = 0

            return

        with ThreadPoolExecutor(max_workers=workers) as pool:
            tasks = {pool.submit(self._recognize, page, min_level): page for page in missing}
            # Результаты забираются по готовности, а не по номеру страницы: по
            # порядку прогресс ждал бы первую страницу, даже когда десяток
            # следующих уже прочитан.
            left = len(missing)

            for task in as_completed(tasks):
                page = tasks[task]
                found, engine = task.result()
                left -= 1
                self._in_flight = min(workers, left)
                self._store((page, min_level), found, engine)

        self._in_flight = 0

    def _recognize(self, page_index: int, min_level: int) -> tuple[list[Word], str]:
        """Прочитать страницу самым дешёвым способом из доступных. Без побочных эффектов."""
        for level in range(min_level, len(self.ladder)):
            recognizer = self.ladder[level]

            if not recognizer.available_for(page_index):
                continue

            if found := recognizer.words(page_index):
                return found, recognizer.name

        return [], 'none'

    def _store(self, key: tuple[int, int], found: list[Word], engine: str) -> None:
        page_index = key[0]
        self._words[key] = found

        if found:
            self._reads[engine] = self._reads.get(engine, 0) + 1

        self._read.add(page_index)
        self._announce(engine)

    def _announce(self, engine: str) -> None:
        """Сообщить наблюдателю, что страница прочитана.

        Распознавание идёт минутами, и без этого сигнала интерфейс не отличит
        работу от зависания. Ошибку наблюдателя гасим: он вспомогательный, и
        уронить из-за него извлечение было бы нелепо.
        """
        if self.on_page is None:
            return

        try:
            self.on_page(
                Progress(
                    done=len(self._read),
                    in_flight=self._in_flight,
                    total=self.page_count,
                    engine=engine,
                )
            )
        except Exception:  # pragma: no cover - наблюдатель не влияет на разбор
            self.on_page = None

    def lines(self, page_index: int, *, min_level: int = 0) -> list[Line]:
        return group_lines(self.words(page_index, min_level=min_level))

    def engine_stats(self) -> dict[str, int]:
        """Сколько страниц прочёл каждый движок — строка отчёта о стоимости.

        С перечитами: страница, отданная модели после Tesseract, учтена у обоих.
        Считать только последнего читателя значило бы занижать счёт за модель
        ровно на те страницы, где её чтение потом перекрыл кто-то ещё.
        """
        return dict(self._reads)

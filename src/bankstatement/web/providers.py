"""Сборка зависимостей приложения.

Раньше всё создавалось прямо при импорте модуля с роутами: реестр, лимитер,
шаблоны. Это удобно ровно до первого вопроса — «а как поднять два приложения с
разными настройками» или «как подменить хранилище в тесте». Ответ был один:
трогать атрибуты модуля.

Теперь состав приложения описан провайдерами, а контейнер собирается фабрикой.
Настройки приходят в контейнер контекстом, всё остальное строится из них.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import Final

from dishka import Provider, Scope, from_context, provide
from fastapi.templating import Jinja2Templates

from bankstatement.models import Guarantee, Opinion, ReconStatus
from bankstatement.web.jobs import JobRegistry, QueueDispatcher, ThreadDispatcher
from bankstatement.web.limits import TokenBucket
from bankstatement.web.settings import Settings
from bankstatement.worker.store import PHASE_TEXT, JobStore, MemoryStore

TEMPLATES_DIR: Final[Path] = Path(__file__).parent / 'templates'
STATIC_DIR: Final[Path] = Path(__file__).parent / 'static'

GUARANTEE_TEXT: Final[dict[Guarantee, str]] = {
    Guarantee.CHAIN: 'Проверено построчно: остаток после каждой операции сходится с предыдущим',
    Guarantee.AGGREGATE: 'Проверены итоги за период; построчной проверки документ не позволяет',
    Guarantee.NONE: 'Не подтверждено',
}

OPINION_TEXT: Final[dict[Opinion, tuple[str, str]]] = {
    Opinion.CLEAN: (
        'Подтверждено',
        'Каждая выписка сошлась с итогами, напечатанными в самом документе.',
    ),
    Opinion.QUALIFIED: (
        'Подтверждено частично',
        'Часть выписок сошлась с итогами документа, остальные требуют проверки человеком.',
    ),
    Opinion.ADVERSE: (
        'Не подтверждено',
        'Ни одна выписка не сошлась с итогами документа — полагаться на извлечённые числа нельзя.',
    ),
    Opinion.DISCLAIMER: (
        'Заключение невозможно',
        'В документе не нашлось ни одной выписки: сверять нечего и не с чем.',
    ),
}

DIAGNOSIS_TEXT: Final[dict[str, str]] = {
    'chain_break': 'остаток в строке не сходится с предыдущим — здесь потеряна или искажена операция',
    'duplicate_row': 'расхождение равно сумме одной строки — похоже, операция посчитана дважды',
    'lost_row': 'дельта обратна сумме найденной строки — вероятно потеря строки',
    'wrong_column': 'расхождение равно удвоенной сумме строки — операция попала не в ту колонку',
    'digit_transposition': 'расхождение кратно девяти — в одном числе переставлены цифры',
    'count_only': 'суммы сходятся, а число операций — нет: лишняя служебная строка или одна разбита надвое',
    'no_reference': 'в шапке не нашлось ни одного итога — сверять не с чем',
    'unexplained': 'расхождение не объясняется одной строкой — вероятно, потеряно несколько',
    'no_balance': 'сумма есть, но остаток не прочитался и не восстановился',
    'no_sign': 'гипотеза разделов не нашла знака (приход или расход) для этой строки',
    'foreign_column': (
        'строка на странице без заголовка раздела, и её сумма не попадает в колонку сумм: '
        'похоже, это образ чека или бланк сверки, а не операция'
    ),
    'unreadable': 'денежный токен не разобрался — сумму не удалось распознать',
    'pending_overflow': 'подвешенных строк накопилось больше предела — строка потеряна при очистке буфера',
    'out_of_chain': 'строка не легла в цепочку и не подтвердилась другим способом',
}


def static_version() -> str:
    """Отпечаток статики для адреса файла.

    Без него выкаченная правка стилей не доезжает до того, кто уже открывал
    страницу: браузер отдаёт закешированную версию, и интерфейс выглядит
    сломанным ровно у тех, кто пользуется им чаще всего. Считается один раз при
    сборке приложения — на запрос это не влияет.
    """
    digest = hashlib.blake2b(digest_size=6)

    for name in sorted(p.name for p in STATIC_DIR.iterdir() if p.is_file()):
        digest.update((STATIC_DIR / name).read_bytes())

    return digest.hexdigest()


class Uploads:
    """Куда складывать принятые файлы. Отдельный тип, чтобы не путать пути."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, name: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)

        return self.directory / name


class Samples:
    """Папка с образцами — она же список допустимых имён.

    Имя из формы сверяется с этим списком, а не подставляется в путь: иначе поле
    формы позволило бы прочитать любой файл на диске.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def names(self) -> list[str]:
        if not self.directory.is_dir():
            return []

        return sorted(p.name for p in self.directory.glob('*.pdf'))

    def resolve(self, name: str) -> Path | None:
        return self.directory / name if name in self.names() else None


class AppProvider(Provider):
    """Состав приложения. Всё живёт столько же, сколько само приложение."""

    scope = Scope.APP

    settings = from_context(provides=Settings)

    @provide
    def store(self, settings: Settings) -> JobStore:
        """Хранилище состояния: словарь в процессе или общее в Redis."""
        if settings.redis_url is None:
            return MemoryStore()

        import redis

        from bankstatement.worker.store import RedisStore

        return RedisStore(redis.Redis.from_url(settings.redis_url))

    @provide
    def registry(self, settings: Settings, store: JobStore, uploads: Uploads) -> JobRegistry:
        """Приём документов. Режим выбирается один раз — по наличию Redis."""
        dispatch = (
            QueueDispatcher()
            if settings.redis_url
            else ThreadDispatcher(
                settings.max_concurrent, max_pages=settings.max_pages, timeout=settings.document_timeout
            )
        )

        return JobRegistry(
            store=store,
            dispatch=dispatch,
            max_jobs=settings.max_jobs,
            uploads_dir=uploads.directory,
            upload_ttl=settings.upload_ttl,
        )

    @provide
    def uploads(self, settings: Settings) -> Uploads:
        directory = (
            Path(settings.uploads_dir)
            if settings.uploads_dir
            else Path(tempfile.gettempdir()) / 'bankstatement-uploads'
        )

        return Uploads(directory)

    @provide
    def samples(self, settings: Settings) -> Samples:
        return Samples(Path(settings.samples_dir))

    @provide
    def limiter(self, settings: Settings) -> TokenBucket:
        return TokenBucket(burst=settings.upload_burst, refill=settings.upload_refill)

    @provide
    def templates(self) -> Jinja2Templates:
        templates = Jinja2Templates(directory=TEMPLATES_DIR)
        templates.env.globals.update(
            guarantee_text=GUARANTEE_TEXT,
            diagnosis_text=DIAGNOSIS_TEXT,
            opinion_text=OPINION_TEXT,
            static_version=static_version(),
            phase_text=PHASE_TEXT,
            RECONCILED=ReconStatus.RECONCILED,
        )

        return templates

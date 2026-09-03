"""Настройки приложения.

Собираются из окружения один раз и дальше передаются явно. Это не формальность:
пока значения читались прямо в модулях, приложение нельзя было ни запустить в
двух конфигурациях, ни проверить в тесте иначе как подменой атрибутов.

msgspec, а не pydantic-settings: типов немного, приведение нужно самое простое, а
лишняя зависимость ради восьми полей не окупается.
"""

import os
from collections.abc import Mapping
from contextlib import suppress
from typing import Final, Self

import msgspec

DEFAULT_MAX_UPLOAD: Final[int] = 128 * 1024 * 1024
"""Самый большой образец — подшивка на 56 МБ, так что запас двукратный. Предел
нужен не от злого умысла, а чтобы промах в диалоге выбора файла не забил диск."""


class Settings(msgspec.Struct, frozen=True):
    """Всё, что меняется между запусками."""

    redis_url: str | None = None
    """С ним разбор уезжает в отдельный воркер, без него живёт в этом процессе."""

    samples_dir: str = 'samples'
    uploads_dir: str = ''
    """Пусто — временная папка системы; задаётся явно в тестах и в контейнере."""

    max_upload: int = DEFAULT_MAX_UPLOAD
    max_batch: int = 20
    """Пачка больше двадцати — это уже выгрузка из системы, и её место в очереди,
    а не в форме браузера."""

    upload_burst: int = 6
    """Сколько загрузок подряд разрешено одному клиенту. Пачку кладут разом,
    поэтому запас должен покрывать обычную пачку целиком."""

    upload_refill: float = 0.2
    """Токенов в секунду: около двенадцати загрузок в минуту в установившемся
    режиме. Разбор одного документа идёт дольше, так что это не узкое место."""

    max_jobs: int = 24
    """Сколько разборов держать под рукой. Стенд рассчитан на показ, а не на
    хранение, и неограниченный рост здесь был бы утечкой."""

    max_concurrent: int = 2
    """Сколько документов разбирать одновременно в режиме одного процесса.
    Каждый разбор сам по себе занимает все ядра, а память тратит кратно."""

    trusted_proxy: bool = False
    """Доверять ли заголовку X-Forwarded-For для определения IP клиента.
    По умолчанию ложь — используется прямой адрес сокета. Включить только
    если стенд стоит за надёжным обратным прокси (nginx, ingress)."""

    max_pages: int = 400
    """Максимум страниц в одном документе. Превышение отсекается на этапе
    нарезки, не забивая очередь разборов."""

    document_timeout: float = 900.0
    """Предел времени на разбор одного документа в секундах (900 = 15 минут).
    При срабатывании разбор возвращает частичный результат."""

    upload_ttl: float = 24 * 3600.0
    """Время жизни загруженного файла в секундах (по умолчанию 24 часа).
    Файлы старше этого срока удаляются при постановке новой задачи."""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        source = os.environ if env is None else env
        values = {
            'redis_url': source.get('BANKSTATEMENT_REDIS') or None,
            'samples_dir': source.get('BANKSTATEMENT_SAMPLES', 'samples'),
            'uploads_dir': source.get('BANKSTATEMENT_UPLOADS', ''),
            'trusted_proxy': source.get('BANKSTATEMENT_TRUSTED_PROXY', '').lower() in ('1', 'true', 'yes'),
        }
        numeric = {
            'max_upload': 'BANKSTATEMENT_MAX_UPLOAD',
            'max_batch': 'BANKSTATEMENT_MAX_BATCH',
            'max_jobs': 'BANKSTATEMENT_MAX_JOBS',
            'max_concurrent': 'BANKSTATEMENT_MAX_CONCURRENT',
            'max_pages': 'BANKSTATEMENT_MAX_PAGES',
        }

        for field, variable in numeric.items():
            raw = source.get(variable)

            if raw and raw.isdigit():
                values[field] = int(raw)  # type: ignore[assignment]

        floats = {
            'document_timeout': 'BANKSTATEMENT_DOCUMENT_TIMEOUT',
            'upload_ttl': 'BANKSTATEMENT_UPLOAD_TTL',
        }

        for field, variable in floats.items():
            raw = source.get(variable)

            if raw:
                with suppress(ValueError):
                    values[field] = float(raw)  # type: ignore[assignment]

        return msgspec.convert(values, cls)

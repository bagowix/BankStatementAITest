"""Конфиг клиента: один TOML-файл на клиента и пакет.

Секретов в файле нет: вместо токена конфиг называет переменную окружения, где он
лежит (`*_env`). Так конфиг можно класть в репозиторий клиента и пересылать, а
ключи остаются в `.env` на сервере.
"""

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import msgspec

from autostudio.errors import ConfigError

_CLIENT_ID: Final = re.compile(r'^[a-z0-9][a-z0-9-]{1,40}$')


class ClientInfo(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Кто клиент и в каком часовом поясе и на каком языке он работает.

    Attributes:
        id: Стабильный идентификатор (`acme-barbers`): по нему каталог данных клиента.
        name: Название бизнеса, как его видят покупатели.
        timezone: IANA-зона, например `Asia/Tbilisi`.
        language: Язык сообщений по умолчанию (`en`, `ru`, `ka`).
    """

    id: str
    name: str
    timezone: str = 'Asia/Tbilisi'
    language: str = 'en'

    def __post_init__(self) -> None:
        if not _CLIENT_ID.match(self.id):
            raise ConfigError(f'client.id={self.id!r}: только a-z, 0-9 и дефис, 2–41 символ')
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f'неизвестный часовой пояс {self.timezone!r}') from None

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class NotifyConfig(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Куда слать уведомления владельцу бизнеса.

    Attributes:
        telegram_chat_id: Чат владельца в Telegram или None.
        telegram_token_env: Переменная окружения с токеном бота-уведомителя.
        webhook_url_env: Переменная с URL входящего вебхука (Slack, Discord, Make) или None.
        console: Печатать уведомления в stdout — для демо и отладки.
    """

    telegram_chat_id: str | None = None
    telegram_token_env: str = 'AUTOSTUDIO_NOTIFY_BOT_TOKEN'  # noqa: S105 - имя переменной, а не секрет
    webhook_url_env: str | None = None
    console: bool = False


class SinkConfig(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Куда писать таблицы для клиента.

    Attributes:
        backend: `csv` — файлы в `csv_dir`; `google` — Google Sheets через сервисный аккаунт.
        csv_dir: Каталог CSV-выгрузок.
        spreadsheet_id: ID таблицы Google (из её URL) для `google`.
        credentials_env: Переменная с путём к JSON-ключу сервисного аккаунта.
    """

    backend: Literal['csv', 'google'] = 'csv'
    csv_dir: Path = Path('var/out')
    spreadsheet_id: str | None = None
    credentials_env: str = 'GOOGLE_APPLICATION_CREDENTIALS'

    def __post_init__(self) -> None:
        if self.backend == 'google' and not self.spreadsheet_id:
            raise ConfigError('sink.backend = "google" требует sink.spreadsheet_id')


def _decode_hook(kind: type, value: object) -> object:
    # TOML отдаёт пути строками, а msgspec сам Path не строит.
    if kind is Path and isinstance(value, str):
        return Path(value)
    raise NotImplementedError


def load_client_config[T: msgspec.Struct](path: Path, kind: type[T]) -> T:
    """Читает и проверяет конфиг клиента.

    Args:
        path: Путь к TOML-файлу.
        kind: Struct-тип конфига конкретного пакета.

    Returns:
        Проверенный конфиг.

    Raises:
        ConfigError: Файла нет, TOML битый или поля не сходятся со схемой.
    """
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ConfigError(f'конфиг не найден: {path}') from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'{path}: битый TOML: {exc}') from None
    try:
        return msgspec.convert(data, type=kind, dec_hook=_decode_hook)
    except msgspec.ValidationError as exc:
        raise ConfigError(f'{path}: {exc}') from None


class _ClientOnly(msgspec.Struct, frozen=True):
    client: ClientInfo


def load_client_info(path: Path) -> ClientInfo:
    """Читает только секцию `[client]` из конфига любого пакета (для отчёта и служебных команд).

    Args:
        path: Путь к TOML-файлу.

    Returns:
        Сведения о клиенте.

    Raises:
        ConfigError: Файла нет или секция `[client]` неверна.
    """
    return load_client_config(path, _ClientOnly).client


def client_data_dir(base: Path, client: ClientInfo) -> Path:
    """Каталог данных клиента: журнал событий, SQLite пакетов, выгрузки."""
    return base / client.id


def require_env(name: str, environ: Mapping[str, str] | None = None) -> str:
    """Возвращает значение секрета из окружения или объясняет, чего не хватает.

    Args:
        name: Имя переменной окружения.
        environ: Окружение; по умолчанию `os.environ`.

    Returns:
        Непустое значение переменной.

    Raises:
        ConfigError: Переменная не задана или пуста.
    """
    source = os.environ if environ is None else environ
    value = source.get(name, '')
    if not value:
        raise ConfigError(f'переменная окружения {name} не задана (см. .env.example)')
    return value

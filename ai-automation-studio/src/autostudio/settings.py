"""Настройки процесса: читаются из окружения один раз, при сборке приложения.

Здесь живёт то, что относится к развёртыванию (какая модель, куда писать данные,
куда слать сигнал «жив»), а не к клиенту. Всё про клиента — в его TOML-конфиге,
см. `autostudio.clientconf`.
"""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self, cast

import msgspec

from autostudio.errors import ConfigError

type Effort = Literal['low', 'medium', 'high', 'xhigh', 'max']


class LLMProvider(StrEnum):
    OPENAI = 'openai'
    ANTHROPIC = 'anthropic'
    FAKE = 'fake'


# Модель по умолчанию для каждого провайдера. Переопределяется AUTOSTUDIO_LLM_MODEL.
# Anthropic — Haiku 5.5 (решение оператора): задачи пакетов — классификация, извлечение
# полей и короткие ответы, а Opus 5.5 ($4/$20 за 1 млн токенов против $0,10/$0,50)
# съел бы почти всю абонентскую плату. Клиенту с трудными письмами можно включить
# Sonnet или Opus одной строкой в его .env.
DEFAULT_MODELS: Final[Mapping[LLMProvider, str]] = {
    LLMProvider.OPENAI: 'gpt-5.4-mini',
    LLMProvider.ANTHROPIC: 'claude-haiku-5-5',
    LLMProvider.FAKE: 'fake',
}

_KEY_VARS: Final[Mapping[LLMProvider, str]] = {
    LLMProvider.OPENAI: 'OPENAI_API_KEY',
    LLMProvider.ANTHROPIC: 'ANTHROPIC_API_KEY',
}

_EFFORTS: Final[tuple[str, ...]] = ('low', 'medium', 'high', 'xhigh', 'max')


class Settings(msgspec.Struct, frozen=True, kw_only=True):
    """Настройки развёртывания.

    Attributes:
        llm_provider: Провайдер модели; `fake` работает без сети и без ключа.
        llm_model: Идентификатор модели у провайдера.
        llm_effort: Глубина рассуждения (`low` … `max`) или None — тогда не передаётся.
        data_dir: Каталог для SQLite и выгрузок.
        healthcheck_url: URL пинга (healthchecks.io и аналоги) или None.
        operator_chat_id: Telegram-чат оператора для сигналов о сбоях или None.
        operator_token_env: Переменная окружения с токеном бота оператора.
        llm_provider_explicit: Провайдер задан в окружении явно, а не взят по умолчанию.
            Боевой запуск пакета, которому нужна модель, по нему отказывается молча
            работать на заглушке.
    """

    llm_provider: LLMProvider = LLMProvider.FAKE
    llm_model: str = DEFAULT_MODELS[LLMProvider.FAKE]
    llm_effort: Effort | None = 'low'
    data_dir: Path = Path('var')
    healthcheck_url: str | None = None
    operator_chat_id: str | None = None
    operator_token_env: str = 'AUTOSTUDIO_OPERATOR_BOT_TOKEN'  # noqa: S105 - имя переменной, а не секрет
    llm_provider_explicit: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Собирает настройки из переменных окружения.

        Args:
            environ: Окружение процесса (в тестах — обычный словарь).

        Returns:
            Проверенные настройки.

        Raises:
            ConfigError: Неизвестный провайдер, не задан ключ или неверный effort.
        """
        raw_provider = environ.get('AUTOSTUDIO_LLM_PROVIDER', '') or LLMProvider.FAKE.value
        try:
            provider = LLMProvider(raw_provider.strip().lower())
        except ValueError:
            known = ', '.join(p.value for p in LLMProvider)
            raise ConfigError(f'AUTOSTUDIO_LLM_PROVIDER={raw_provider!r}: ожидается одно из {known}') from None

        key_var = _KEY_VARS.get(provider)
        if key_var is not None and not environ.get(key_var):
            raise ConfigError(f'провайдер {provider.value} выбран, но {key_var} не задан')

        raw_effort = environ.get('AUTOSTUDIO_LLM_EFFORT', 'low').strip().lower()
        if raw_effort and raw_effort not in _EFFORTS:
            raise ConfigError(f'AUTOSTUDIO_LLM_EFFORT={raw_effort!r}: ожидается одно из {", ".join(_EFFORTS)}')
        effort = cast(Effort, raw_effort) if raw_effort else None

        return cls(
            llm_provider=provider,
            llm_provider_explicit=bool(environ.get('AUTOSTUDIO_LLM_PROVIDER', '').strip()),
            llm_model=environ.get('AUTOSTUDIO_LLM_MODEL', '') or DEFAULT_MODELS[provider],
            llm_effort=effort,
            data_dir=Path(environ.get('AUTOSTUDIO_DATA_DIR', '') or 'var'),
            healthcheck_url=environ.get('AUTOSTUDIO_HEALTHCHECK_URL') or None,
            operator_chat_id=environ.get('AUTOSTUDIO_OPERATOR_CHAT_ID') or None,
        )

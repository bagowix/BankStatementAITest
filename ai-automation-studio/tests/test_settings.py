from pathlib import Path

import pytest

from autostudio.errors import ConfigError
from autostudio.settings import LLMProvider, Settings


def test__settings__empty_env__fake_provider_without_key():
    settings = Settings.from_env({})
    assert settings.llm_provider is LLMProvider.FAKE
    assert settings.llm_model == 'fake'
    assert settings.data_dir == Path('var')


def test__settings__openai_with_key__default_model():
    settings = Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'OpenAI', 'OPENAI_API_KEY': 'sk-test'})
    assert settings.llm_provider is LLMProvider.OPENAI
    assert settings.llm_model == 'gpt-5.4-mini'


def test__settings__anthropic_model_override__used():
    settings = Settings.from_env(
        {'AUTOSTUDIO_LLM_PROVIDER': 'anthropic', 'ANTHROPIC_API_KEY': 'k', 'AUTOSTUDIO_LLM_MODEL': 'claude-haiku-5-5'}
    )
    assert settings.llm_model == 'claude-haiku-5-5'


def test__settings__provider_without_key__config_error():
    with pytest.raises(ConfigError, match='ANTHROPIC_API_KEY'):
        Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'anthropic'})


def test__settings__unknown_provider__config_error():
    with pytest.raises(ConfigError, match='ожидается'):
        Settings.from_env({'AUTOSTUDIO_LLM_PROVIDER': 'gemini'})


def test__settings__bad_effort__config_error():
    with pytest.raises(ConfigError, match='EFFORT'):
        Settings.from_env({'AUTOSTUDIO_LLM_EFFORT': 'extreme'})


def test__settings__empty_effort__none():
    assert Settings.from_env({'AUTOSTUDIO_LLM_EFFORT': ''}).llm_effort is None


def test__settings__operator_and_healthcheck__read():
    settings = Settings.from_env(
        {'AUTOSTUDIO_HEALTHCHECK_URL': 'https://hc-ping.com/x', 'AUTOSTUDIO_OPERATOR_CHAT_ID': '42', 'AUTOSTUDIO_DATA_DIR': '/data'}
    )
    assert settings.healthcheck_url == 'https://hc-ping.com/x'
    assert settings.operator_chat_id == '42'
    assert settings.data_dir == Path('/data')

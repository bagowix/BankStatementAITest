"""Выбор реализации `LLMClient` по настройкам."""

from autostudio.llm.base import LLMClient
from autostudio.llm.fake import FakeLLM
from autostudio.settings import LLMProvider, Settings


def make_llm(settings: Settings, fake: FakeLLM | None = None) -> LLMClient:
    """Строит клиента модели.

    SDK провайдеров импортируются здесь, а не на уровне модуля: они в extras, и
    развёртыванию на заглушке не нужно их ставить.

    Args:
        settings: Настройки процесса.
        fake: Заглушка с эвристиками пакета; используется, когда провайдер `fake`.

    Returns:
        Клиент модели.
    """
    match settings.llm_provider:
        case LLMProvider.OPENAI:
            import openai

            from autostudio.llm.openai_client import OpenAIClient

            return OpenAIClient(openai.AsyncOpenAI(), settings.llm_model, settings.llm_effort)
        case LLMProvider.ANTHROPIC:
            import anthropic

            from autostudio.llm.anthropic_client import AnthropicClient

            return AnthropicClient(anthropic.AsyncAnthropic(), settings.llm_model, settings.llm_effort)
        case LLMProvider.FAKE:
            return fake if fake is not None else FakeLLM()

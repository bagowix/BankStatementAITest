"""Выбор реализации `LLMClient` по настройкам."""

from autostudio.llm.base import LLMClient
from autostudio.llm.fake import FakeLLM
from autostudio.llm.resilient import FallbackHook, ResilientLLM
from autostudio.settings import LLMProvider, Settings


def make_llm(settings: Settings, fake: FakeLLM | None = None, on_fallback: FallbackHook | None = None) -> LLMClient:
    """Строит клиента модели.

    SDK провайдеров импортируются здесь, а не на уровне модуля: они в extras, и
    развёртыванию на заглушке не нужно их ставить.

    Args:
        settings: Настройки процесса.
        fake: Эвристики пакета. Провайдер `fake` — работают только они; настоящий
            провайдер — они же становятся запасным выходом при его сбое.
        on_fallback: Сигнал оператору о деградации на эвристики.

    Returns:
        Клиент модели.
    """
    primary: LLMClient
    match settings.llm_provider:
        case LLMProvider.OPENAI:
            import openai

            from autostudio.llm.openai_client import OpenAIClient

            primary = OpenAIClient(openai.AsyncOpenAI(), settings.llm_model, settings.llm_effort)
        case LLMProvider.ANTHROPIC:
            import anthropic

            from autostudio.llm.anthropic_client import AnthropicClient

            primary = AnthropicClient(anthropic.AsyncAnthropic(), settings.llm_model, settings.llm_effort)
        case LLMProvider.FAKE:
            return fake if fake is not None else FakeLLM()
    return primary if fake is None else ResilientLLM(primary, fake, on_fallback)

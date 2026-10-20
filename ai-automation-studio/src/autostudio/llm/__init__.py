"""Модель за одним контрактом: OpenAI, Anthropic или детерминированная заглушка."""

from autostudio.llm.base import JSONObject, JSONValue, LLMClient, LLMUsage, decode_output, output_schema
from autostudio.llm.factory import make_llm
from autostudio.llm.fake import FakeCall, FakeLLM

__all__ = [
    'FakeCall',
    'FakeLLM',
    'JSONObject',
    'JSONValue',
    'LLMClient',
    'LLMUsage',
    'decode_output',
    'make_llm',
    'output_schema',
]

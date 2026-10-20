"""Эвристики `FakeLLM` для монитора цен: демо и тесты без ключа и без сети.

«Модель» ищет в тексте страницы первое число рядом со знаком валюты и отдаёт его
цитатой. Ответ идёт тем же путём, что и ответ настоящей модели: схема `PriceGuess`,
затем проверка цитаты по странице в `verify_guess`.
"""

import re
from typing import Final

from autostudio.llm import FakeLLM
from autostudio.prices.extract import PriceGuess
from autostudio.prices.money import find_price_mentions

_PAGE: Final = re.compile(r'<page_text>\n(?P<text>.*)\n</page_text>', re.DOTALL)
_NOT_FOUND: Final = PriceGuess(found=False, price=None, currency=None, evidence=None)


def guess_price(system: str, prompt: str) -> PriceGuess:
    """Ответ «модели» на запрос цены.

    Args:
        system: Системная инструкция (не используется).
        prompt: Запрос с текстом страницы в `<page_text>`.

    Returns:
        Цена с цитатой или `found=False`.
    """
    match = _PAGE.search(prompt)
    if match is None:
        return _NOT_FOUND
    text = match.group('text')
    mention = next((m for m in find_price_mentions(text) if m.currency is not None and m.amount > 0), None)
    if mention is None:
        return _NOT_FOUND
    return PriceGuess(
        found=True,
        price=format(mention.amount, 'f'),
        currency=mention.currency,
        evidence=text[mention.start : mention.end],
    )


def make_fake_llm() -> FakeLLM:
    """`FakeLLM` с эвристикой цены — для демо, тестов и развёртывания без ключа."""
    return FakeLLM({PriceGuess: guess_price})

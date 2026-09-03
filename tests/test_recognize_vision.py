"""Ступень L2: контракт клиента, раскладка ответа модели, бюджет, выбор по окружению."""

import logging
from decimal import Decimal

import pymupdf
import pytest

from bankstatement.header import build_account, build_summary
from bankstatement.ingest import default_ladder
from bankstatement.layout import group_lines
from bankstatement.models import BankStatementError, Guarantee, ReconStatus
from bankstatement.parse import chain_hypothesis
from bankstatement.recognize.anthropic_client import AnthropicVisionClient
from bankstatement.recognize.openai_client import OpenAIVisionClient
from bankstatement.recognize.vision import (
    PROMPT,
    VisionRecognizer,
    render_for_model,
    transcript_to_words,
    vision_from_env,
)
from bankstatement.reconcile import reconcile

TRANSCRIPT = """Ixonia Bank  Account Number XXXXXX4664
Beginning Balance as of 04/01/2025  $597,068.70
Ending Balance as of 04/30/2025  $604,346.94
Date  Description  Deposits  Withdrawals  Balance
Apr 01  AIRLINEHYD 2759/VENDOR PMT  1,809.28  598,877.98
Apr 01  METAL TRADERS IN/ACH PAYMEN 9966204593  3,506.80  602,384.78
Apr 01  MID ATLANTIC TR/ACH PULLS  1,852.51  600,532.27
Apr 02  REMOTE DEPOSIT  3,814.67  604,346.94
"""


class FakeClient:
    """Клиент без сети: отдаёт заготовленный текст и считает вызовы."""

    name = 'fake'

    def __init__(self, text: str = TRANSCRIPT, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls: list[tuple[int, str]] = []

    def transcribe(self, image_png: bytes, prompt: str) -> str:
        self.calls.append((len(image_png), prompt))

        if self.fail:
            raise RuntimeError('сеть недоступна')

        return self.text


def blank_document(pages: int = 3) -> pymupdf.Document:
    doc = pymupdf.open()

    for _ in range(pages):
        doc.new_page()

    return doc


def test__transcript_to_words__rows_in_reading_order__chain_reconciles():
    """Ответ модели без координат раскладывается так, что цепочка остатков сходится."""
    words = transcript_to_words(TRANSCRIPT, page_index=0, width=612, height=792, engine='vision-fake')
    lines = group_lines(words)
    summary = build_summary(lines)
    period = build_account(lines).period
    assert period is not None

    guess = chain_hypothesis(lines, summary, (period.start, period.end))
    verdict = reconcile(summary, guess.transactions)

    assert summary.beginning_balance == Decimal('597068.70')
    assert len(guess.transactions) == 4
    assert verdict.status is ReconStatus.RECONCILED
    assert verdict.guarantee is Guarantee.CHAIN
    assert {w.engine for w in words} == {'vision-fake'}
    assert all(w.confidence is None for w in words)


def test__transcript_to_words__money_at_the_end__aligned_in_columns():
    """Суммы в хвосте строки прижаты к правому краю одинаково на всех строках."""
    words = transcript_to_words('Apr 01  A  1.00  2.00\nApr 02  LONGER DESCRIPTION HERE  3.00  4.00', 0, 612, 792, 'v')
    balances = [w for w in words if w.text in ('2.00', '4.00')]
    amounts = [w for w in words if w.text in ('1.00', '3.00')]

    assert len({w.x1 for w in balances}) == 1
    assert len({w.x1 for w in amounts}) == 1
    assert amounts[0].x1 < balances[0].x0


def test__transcript_to_words__code_fence_and_blank_lines__ignored():
    words = transcript_to_words('```\n\nApr 01  X  1.00  2.00\n```\n', 0, 612, 792, 'v')

    assert [w.text for w in words] == ['Apr', '01', 'X', '1.00', '2.00']
    assert transcript_to_words('', 0, 612, 792, 'v') == []


def test__render_for_model__page__png_and_size():
    doc = blank_document(1)

    image, width, height = render_for_model(doc, 0)

    assert image.startswith(b'\x89PNG')
    assert (round(width), round(height)) == (595, 842)


def test__vision_recognizer__budget__pages_beyond_it_are_not_sent():
    """Бюджет страниц — единственная защита от счёта за всю подшивку."""
    client = FakeClient()
    recognizer = VisionRecognizer(blank_document(3), client, budget=2)

    first = recognizer.words(0)
    second = recognizer.words(1)
    third = recognizer.words(2)

    assert first and second
    assert third == []
    assert recognizer.available_for(2) is False
    assert recognizer.spent == 2
    assert client.calls[0][1] == PROMPT
    assert recognizer.name == 'vision-fake'


def test__vision_recognizer__model_answers_nothing_useful__warned(caplog):
    """Отказ модели не должен теряться в тишине: он попадает в лог первой строкой ответа."""
    recognizer = VisionRecognizer(blank_document(1), FakeClient(text="I'm unable to assist with that."), budget=5)

    with caplog.at_level('WARNING'):
        assert recognizer.words(0) == []

    assert 'unable to assist' in caplog.text


def test__vision_recognizer__client_fails__page_stays_empty_and_logged(caplog):
    """Отказ сети не роняет разбор: страница остаётся за предыдущей ступенью."""
    recognizer = VisionRecognizer(blank_document(1), FakeClient(fail=True), budget=5)

    with caplog.at_level('ERROR'):
        assert recognizer.words(0) == []

    assert 'не прочитана' in caplog.text


def test__vision_from_env__not_configured__no_rung(monkeypatch):
    monkeypatch.delenv('BANKSTATEMENT_VISION', raising=False)

    assert vision_from_env(blank_document(1)) == []
    assert not default_ladder(blank_document(1))[-1].name.startswith('vision-')


def test__vision_from_env__openai_without_key__clear_error(monkeypatch):
    monkeypatch.setenv('BANKSTATEMENT_VISION', 'openai')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)

    with pytest.raises(BankStatementError, match='OPENAI_API_KEY'):
        vision_from_env(blank_document(1))


def test__vision_from_env__anthropic_without_key__clear_error(monkeypatch):
    monkeypatch.setenv('BANKSTATEMENT_VISION', 'anthropic')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)

    with pytest.raises(BankStatementError, match='ANTHROPIC_API_KEY'):
        vision_from_env(blank_document(1))


def test__vision_from_env__unknown_provider__error(monkeypatch):
    monkeypatch.setenv('BANKSTATEMENT_VISION', 'gemini')

    with pytest.raises(BankStatementError, match='неизвестен'):
        vision_from_env(blank_document(1))


def test__default_ladder__openai_configured__fourth_rung_with_budget(monkeypatch):
    """С провайдером и ключом лестница получает платную ступень последней."""
    monkeypatch.setenv('BANKSTATEMENT_VISION', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    monkeypatch.setenv('BANKSTATEMENT_VISION_MODEL', 'gpt-4.1')
    monkeypatch.setenv('BANKSTATEMENT_VISION_PAGES', '3')

    ladder = default_ladder(blank_document(1))
    rung = ladder[-1]

    assert isinstance(rung, VisionRecognizer)
    assert rung.name == 'vision-openai/gpt-4.1'
    assert rung.available_for(0)


def test__default_ladder__anthropic_configured__fourth_rung(monkeypatch):
    monkeypatch.setenv('BANKSTATEMENT_VISION', 'anthropic')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    monkeypatch.delenv('BANKSTATEMENT_VISION_MODEL', raising=False)
    monkeypatch.setenv('BANKSTATEMENT_VISION_PAGES', 'нет')

    rung = default_ladder(blank_document(1))[-1]

    assert rung.name == 'vision-anthropic/claude-opus-5'


def test__vision_from_env__two_models__two_rungs_in_order(monkeypatch):
    """Вторая модель — своя ступень: эскалация отдаст ей только то, что не свела первая."""
    monkeypatch.setenv('BANKSTATEMENT_VISION', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    monkeypatch.setenv('BANKSTATEMENT_VISION_MODEL', ' gpt-5.4-mini, gpt-5.6-sol ,')
    monkeypatch.setenv('BANKSTATEMENT_VISION_PAGES', '2')

    rungs = vision_from_env(blank_document(1))
    ladder = [r.name for r in default_ladder(blank_document(1))]

    assert [r.name for r in rungs] == ['vision-openai/gpt-5.4-mini', 'vision-openai/gpt-5.6-sol']
    assert ladder[-2:] == ['vision-openai/gpt-5.4-mini', 'vision-openai/gpt-5.6-sol']


def test__vision_from_env__same_model_twice__two_independent_rungs(monkeypatch):
    """Повтор дешёвой модели — тоже ступень: её ответ от раза к разу различается, у каждой свой бюджет."""
    monkeypatch.setenv('BANKSTATEMENT_VISION', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    monkeypatch.setenv('BANKSTATEMENT_VISION_MODEL', 'gpt-5.4-mini,gpt-5.4-mini')
    monkeypatch.setenv('BANKSTATEMENT_VISION_PAGES', '1')

    first, second = vision_from_env(blank_document(1))
    first._spent = 1  # первая ступень исчерпала бюджет

    assert first.name == second.name == 'vision-openai/gpt-5.4-mini'
    assert first is not second
    assert not first.available_for(0)
    assert second.available_for(0)


class _Recorder:
    """Заглушка SDK: запоминает аргументы вызова и отдаёт заготовленный ответ."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, object] = {}

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs

        return self.response


class _Attr:
    def __init__(self, **fields: object) -> None:
        self.__dict__.update(fields)


def test__openai_client__request_shape__image_and_prompt_in_one_user_turn():
    recorder = _Recorder(_Attr(choices=[_Attr(message=_Attr(content='Apr 01  X  1.00  2.00'))], usage=None))
    sdk = _Attr(chat=_Attr(completions=recorder))
    client = OpenAIVisionClient(sdk, model='gpt-4o')  # type: ignore[arg-type]

    text = client.transcribe(b'\x89PNG...', PROMPT)

    assert text == 'Apr 01  X  1.00  2.00'
    assert recorder.kwargs['model'] == 'gpt-4o'
    message = recorder.kwargs['messages'][0]  # type: ignore[index]
    assert message['role'] == 'user'
    assert message['content'][0] == {'type': 'text', 'text': PROMPT}
    assert message['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert client.model == 'gpt-4o'


def test__openai_client__reasoning_family__low_effort_requested():
    """Переписать страницу — задача без рассуждений; gpt-5 без указания усилия тратит бюджет на них."""
    from bankstatement.recognize.openai_client import wants_low_effort

    assert wants_low_effort('gpt-5.4-mini')
    assert wants_low_effort('o4-mini')
    assert not wants_low_effort('gpt-5.2-chat-latest')
    assert not wants_low_effort('gpt-4o')

    recorder = _Recorder(_Attr(choices=[_Attr(message=_Attr(content='x'))], usage=None))
    OpenAIVisionClient(_Attr(chat=_Attr(completions=recorder)), model='gpt-5.5').transcribe(b'png', PROMPT)  # type: ignore[arg-type]

    assert recorder.kwargs['reasoning_effort'] == 'low'
    assert 'reasoning_effort' not in _Recorder(_Attr(choices=[_Attr(message=_Attr(content='x'))], usage=None)).kwargs


def test__openai_client__usage_reported__tokens_logged(caplog):
    """Цена ступени — это токены; без записи о них стоимость страницы остаётся оценкой."""
    response = _Attr(
        choices=[_Attr(message=_Attr(content='x'))],
        usage=_Attr(prompt_tokens=2619, completion_tokens=1520),
    )
    client = OpenAIVisionClient(_Attr(chat=_Attr(completions=_Recorder(response))), model='gpt-5.4-mini')  # type: ignore[arg-type]

    with caplog.at_level(logging.INFO, logger='bankstatement.recognize.openai_client'):
        client.transcribe(b'png', PROMPT)

    assert 'gpt-5.4-mini, токенов на входе 2619, на выходе 1520' in caplog.text


def test__openai_client__no_model_given__measured_default():
    """Умолчание — самая дешёвая модель, которая измеримо сводит подшивку; README называет её же."""
    from bankstatement.recognize.openai_client import DEFAULT_MODEL

    client = OpenAIVisionClient(_Attr(chat=_Attr(completions=_Recorder(None))))  # type: ignore[arg-type]

    assert client.model == DEFAULT_MODEL == 'gpt-5.4-mini'


def test__openai_client__empty_answer__empty_text():
    recorder = _Recorder(_Attr(choices=[_Attr(message=_Attr(content=None))], usage=None))
    client = OpenAIVisionClient(_Attr(chat=_Attr(completions=recorder)), model='gpt-4o')  # type: ignore[arg-type]

    assert client.transcribe(b'png', PROMPT) == ''


def test__anthropic_client__request_shape__fallbacks_on_and_text_joined():
    response = _Attr(
        stop_reason='end_turn',
        content=[_Attr(type='text', text='Apr 01  X'), _Attr(type='thinking', thinking='')],
        usage=_Attr(input_tokens=1000, output_tokens=200),
    )
    recorder = _Recorder(response)
    sdk = _Attr(beta=_Attr(messages=recorder))
    client = AnthropicVisionClient(sdk)  # type: ignore[arg-type]

    text = client.transcribe(b'\x89PNG...', PROMPT)

    assert text == 'Apr 01  X'
    assert recorder.kwargs['model'] == 'claude-opus-5'
    assert recorder.kwargs['fallbacks'] == 'default'
    assert 'server-side-fallback-2026-07-01' in recorder.kwargs['betas']  # type: ignore[operator]
    content = recorder.kwargs['messages'][0]['content']  # type: ignore[index]
    assert content[0]['type'] == 'image'
    assert content[1] == {'type': 'text', 'text': PROMPT}
    assert client.model == 'claude-opus-5'


def test__anthropic_client__refusal__error_not_empty_page():
    recorder = _Recorder(_Attr(stop_reason='refusal', content=[], usage=_Attr(input_tokens=1, output_tokens=1)))
    client = AnthropicVisionClient(_Attr(beta=_Attr(messages=recorder)))  # type: ignore[arg-type]

    with pytest.raises(BankStatementError, match='отказалась'):
        client.transcribe(b'png', PROMPT)

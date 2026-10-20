"""Заглушки SDK: ловят аргументы вызова и отдают заранее заданный ответ или ошибку."""

from types import SimpleNamespace

import httpx2


def status_error(cls: type, status: int = 500) -> Exception:
    request = httpx2.Request('POST', 'https://api.example.test/v1')
    return cls('boom', response=httpx2.Response(status, request=request), body=None)


def connection_error(cls: type) -> Exception:
    return cls(request=httpx2.Request('POST', 'https://api.example.test/v1'))


class Endpoint:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def openai_stub(result: object = None, error: Exception | None = None) -> SimpleNamespace:
    return SimpleNamespace(responses=Endpoint(result, error))


def openai_response(text: str, status: str = 'completed') -> SimpleNamespace:
    return SimpleNamespace(
        id='resp_1',
        status=status,
        output_text=text,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def anthropic_stub(result: object = None, error: Exception | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        messages=Endpoint(result, error),
        beta=SimpleNamespace(messages=Endpoint(result, error)),
    )


def anthropic_message(text: str | None, stop_reason: str = 'end_turn') -> SimpleNamespace:
    content = [SimpleNamespace(type='thinking', thinking='')]
    if text is not None:
        content.append(SimpleNamespace(type='text', text=text))
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        content=content,
    )

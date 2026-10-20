import msgspec
import pytest

from autostudio.errors import LLMError
from autostudio.llm import decode_output, output_schema


class Inner(msgspec.Struct):
    x: float


class Outer(msgspec.Struct):
    name: str
    tags: list[str]
    inner: Inner
    note: str | None


class Node(msgspec.Struct):
    child: 'Node | None'


def test__output_schema__nested_struct__refs_inlined_and_closed():
    schema = output_schema(Outer)
    assert '$defs' not in schema
    assert '$ref' not in str(schema)
    assert schema['additionalProperties'] is False
    assert schema['required'] == ['name', 'tags', 'inner', 'note']
    inner = schema['properties']['inner']
    assert inner['additionalProperties'] is False
    assert inner['required'] == ['x']
    assert 'title' not in schema


def test__output_schema__recursive__llm_error():
    with pytest.raises(LLMError, match='рекурсивная'):
        output_schema(Node)


def test__decode_output__valid__struct():
    out = decode_output('{"name":"a","tags":[],"inner":{"x":1},"note":null}', Outer)
    assert out == Outer(name='a', tags=[], inner=Inner(x=1.0), note=None)


def test__decode_output__wrong_shape__llm_error():
    with pytest.raises(LLMError, match='Outer'):
        decode_output('{"name": 1}', Outer)


def test__decode_output__not_json__llm_error():
    with pytest.raises(LLMError):
        decode_output('sure! here you go', Outer)

import csv

import pytest

from autostudio.clientconf import SinkConfig
from autostudio.errors import SinkError
from autostudio.sinks import CsvSink, make_sink, neutralize_formula


def _read(path):
    with path.open(encoding='utf-8', newline='') as fh:
        return list(csv.reader(fh))


async def test__csv_append__twice__header_once(tmp_path):
    sink = CsvSink(tmp_path)
    await sink.append('Leads', ['a', 'b'], [['1', '2']])
    await sink.append('Leads', ['a', 'b'], [['3', '4']])
    assert _read(tmp_path / 'Leads.csv') == [['a', 'b'], ['1', '2'], ['3', '4']]


async def test__csv_replace__existing__overwritten(tmp_path):
    sink = CsvSink(tmp_path)
    await sink.append('Now', ['p'], [['old']])
    await sink.replace('Now', ['p'], [['new']])
    assert _read(tmp_path / 'Now.csv') == [['p'], ['new']]


async def test__csv_append__formula_cell__neutralized(tmp_path):
    sink = CsvSink(tmp_path)
    await sink.append('x', ['v'], [['=HYPERLINK("http://evil")'], ['-12.50'], ['@cmd']])
    assert _read(tmp_path / 'x.csv')[1:] == [['\'=HYPERLINK("http://evil")'], ['-12.50'], ["'@cmd"]]


def test__csv_path_for__unsafe_name__sanitized(tmp_path):
    assert CsvSink(tmp_path).path_for('../../etc/passwd').parent == tmp_path
    assert CsvSink(tmp_path).path_for('///').name == 'sheet.csv'


async def test__csv_append__unwritable__sink_error(tmp_path):
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    with pytest.raises(SinkError):
        await CsvSink(blocker / 'sub').append('t', ['a'], [['1']])


@pytest.mark.parametrize(('value', 'expected'), [('hello', 'hello'), ('-5', '-5'), ('-x', "'-x"), ('+1', '+1'), ('+x', "'+x")])
def test__neutralize_formula__cases(value, expected):
    assert neutralize_formula(value) == expected


def test__make_sink__csv_default__csv_sink(tmp_path):
    assert isinstance(make_sink(SinkConfig(csv_dir=tmp_path)), CsvSink)


@pytest.mark.parametrize('phone', ['+995 555 12-34-56', '+1 (415) 555-0100', '+79990001122'])
def test__neutralize_formula__international_phone__untouched(phone):
    assert neutralize_formula(phone) == phone


def test__neutralize_formula__plus_formula__still_escaped():
    assert neutralize_formula('+SUM(A1:A2)') == "'+SUM(A1:A2)"
    assert neutralize_formula('+1+cmd|calc') == "'+1+cmd|calc"

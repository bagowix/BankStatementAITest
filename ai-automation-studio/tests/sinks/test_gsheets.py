import gspread
import pytest

from autostudio.clientconf import SinkConfig
from autostudio.errors import ConfigError, SinkError
from autostudio.sinks import GoogleSheetsSink, make_sink


class FakeSheet:
    def __init__(self):
        self.rows: list[list[str]] = []

    def append_row(self, row, value_input_option):
        self.rows.append(row)

    def append_rows(self, rows, value_input_option):
        self.rows.extend(rows)

    def clear(self):
        self.rows = []

    def update(self, rows, value_input_option):
        self.rows = list(rows)


class FakeBook:
    def __init__(self):
        self.sheets: dict[str, FakeSheet] = {}

    def worksheet(self, title):
        if title not in self.sheets:
            raise gspread.WorksheetNotFound(title)
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        self.sheets[title] = FakeSheet()
        return self.sheets[title]


def _sink(tmp_path):
    sink = GoogleSheetsSink('sheet-id', tmp_path / 'key.json')
    sink._book = FakeBook()
    return sink


async def test__gsheets_append__new_tab__created_with_header_and_safe_rows(tmp_path):
    sink = _sink(tmp_path)
    await sink.append('Leads', ['name'], [['Ann'], ['=1+1']])
    assert sink._book.sheets['Leads'].rows == [['name'], ['Ann'], ["'=1+1"]]


async def test__gsheets_replace__existing__header_plus_rows(tmp_path):
    sink = _sink(tmp_path)
    await sink.append('Now', ['p'], [['old']])
    await sink.replace('Now', ['p'], [['new']])
    assert sink._book.sheets['Now'].rows == [['p'], ['new']]


async def test__gsheets__missing_key_file__sink_error(tmp_path):
    with pytest.raises(SinkError, match='sheet-id'):
        await GoogleSheetsSink('sheet-id', tmp_path / 'missing.json').append('t', ['a'], [['1']])


async def test__gsheets__api_failure__sink_error(tmp_path):
    sink = _sink(tmp_path)

    def boom(title):
        raise RuntimeError('quota')

    sink._book.worksheet = boom
    with pytest.raises(SinkError, match='quota'):
        await sink.replace('t', ['a'], [])
    with pytest.raises(SinkError, match='quota'):
        await sink.append('t', ['a'], [['1']])


def test__make_sink__google__needs_credentials_env(monkeypatch, tmp_path):
    config = SinkConfig(backend='google', spreadsheet_id='abc', credentials_env='TEST_GOOGLE_KEY')
    monkeypatch.delenv('TEST_GOOGLE_KEY', raising=False)
    with pytest.raises(ConfigError, match='TEST_GOOGLE_KEY'):
        make_sink(config)
    monkeypatch.setenv('TEST_GOOGLE_KEY', str(tmp_path / 'k.json'))
    assert isinstance(make_sink(config), GoogleSheetsSink)

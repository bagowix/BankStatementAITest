"""Команды cyclopts — обычные функции.

Поэтому проверяются двумя способами: разбор аргументов — через приложение,
поведение — прямым вызовом. У typer второе было бы невозможно: там в сигнатуре
живут его собственные объекты вместо значений.
"""

from pathlib import Path

import msgspec
import pytest
from cyclopts import ValidationError

from bankstatement.cli import _write_json, app, evaluate, extract

SAMPLES = Path('samples')
GREAT_LAKES = SAMPLES / 'Great Lakes Commerce Bank 2025.pdf'


def test__write_json__any_payload__ends_with_newline(tmp_path):
    """Результат уходит в git и в diff, а файл без перевода строки в конце шумит."""
    out = tmp_path / 'r.json'

    _write_json(out, {'a': 1})

    assert out.read_bytes().endswith(b'\n')


@pytest.mark.samples
def test__extract__reconciled_document__report_and_zero_code(tmp_path, capsys):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    out = tmp_path / 'result.json'
    code = extract(GREAT_LAKES, json_out=out)

    assert code == 0
    assert '✓' in capsys.readouterr().out

    payload = msgspec.json.decode(out.read_bytes())
    assert payload['summary']['ending_balance'] == 2041338.12


@pytest.mark.samples
def test__extract__quiet__prints_json_only(capsys):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    extract(GREAT_LAKES, quiet=True)

    payload = msgspec.json.decode(capsys.readouterr().out.encode())
    assert payload['meta']['periods_total'] == 1


def test__extract__missing_file__validation_error(tmp_path):
    """Несуществующий файл — ошибка вызова, а не пустой результат."""
    with pytest.raises(ValidationError):
        extract(tmp_path / 'нет.pdf')


def test__evaluate__missing_folder__validation_error(tmp_path):
    with pytest.raises(ValidationError):
        evaluate(tmp_path / 'нет')


@pytest.mark.samples
def test__evaluate__folder_of_samples__table_and_json(tmp_path, capsys):
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    folder = tmp_path / 'one'
    folder.mkdir()
    (folder / GREAT_LAKES.name).write_bytes(GREAT_LAKES.read_bytes())
    out = tmp_path / 'eval.json'

    evaluate(folder, json_out=out)

    assert 'итого' in capsys.readouterr().out
    assert msgspec.json.decode(out.read_bytes())['totals']['periods'] == 1


@pytest.mark.samples
def test__app__parses_arguments__and_exits_with_the_command_code(tmp_path):
    """Разбор аргументов: путь, флаг и код возврата доезжают до команды."""
    if not GREAT_LAKES.exists():
        pytest.skip('нет образцов')

    out = tmp_path / 'r.json'

    with pytest.raises(SystemExit) as exit_info:
        app(['extract', str(GREAT_LAKES), '--json-out', str(out), '--quiet'], exit_on_error=False)

    assert exit_info.value.code == 0
    assert out.exists()


@pytest.mark.ocr
def test__extract__document_without_statements__nonzero_code(tmp_path, capsys):
    """Пустой документ — не успех: в конвейере он не должен пройти как сведённый."""
    import pymupdf

    blank = tmp_path / 'blank.pdf'
    doc = pymupdf.open()
    doc.new_page()
    doc.save(blank)

    assert extract(blank) == 1


def test__extract__synthetic_statement__report_and_zero_code(sample_pdf, tmp_path, capsys):
    """Полный путь CLI без реальных документов: синтетическая выписка сходится."""
    out = tmp_path / 'result.json'

    code = extract(sample_pdf, json_out=out)

    assert code == 0
    assert '✓' in capsys.readouterr().out
    assert msgspec.json.decode(out.read_bytes())['summary']['ending_balance'] == 120.0


def test__evaluate__folder_with_synthetic_statement__table_without_truth(sample_pdf, tmp_path, capsys):
    out = tmp_path / 'eval.json'

    evaluate(sample_pdf.parent, json_out=out)

    text = capsys.readouterr().out
    payload = msgspec.json.decode(out.read_bytes())

    assert 'итого' in text
    assert payload['totals'] == {'periods': 1, 'reconciled': 1, 'transactions': 2, 'header': '0/0'}
    assert payload['files'][0]['header'] == '—'

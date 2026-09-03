"""Командный интерфейс: извлечение одного файла и прогон по всем образцам.

Cyclopts, а не typer: тот построен поверх click, и в сигнатуре команды всегда
видно оба слоя — `Annotated[Path, typer.Argument(...)]`. Cyclopts читает обычные
аннотации и докстроки, поэтому функция команды остаётся обычной функцией,
которую можно вызвать из кода и из теста напрямую.
"""

from pathlib import Path

import cyclopts
import msgspec

from bankstatement.evaluate import evaluate_paths, render_table, update_readme
from bankstatement.extract import extract_result
from bankstatement.logs import configure_logging
from bankstatement.report import render

app = cyclopts.App(
    name='bankstatement',
    help='Извлечение банковских выписок со сверкой по арифметике самого документа.',
)


def _write_json(path: Path, payload: object) -> None:
    """Записать JSON с отступами — файл читает человек, а не только машина."""
    path.write_bytes(msgspec.json.format(msgspec.json.encode(payload), indent=2) + b'\n')


@app.command
def extract(
    pdf: Path,
    /,
    *,
    json_out: Path | None = None,
    quiet: bool = False,
) -> int:
    """Извлечь одну выписку.

    Args:
        pdf: Путь к PDF выписки.
        json_out: Записать результат в файл JSON.
        quiet: Только JSON в stdout, без отчёта.

    Returns:
        Ноль, если сведены все периоды; единица, если хоть один не сведён или
        выписок в документе не нашлось.
    """
    if not pdf.is_file():
        raise cyclopts.ValidationError(f'файл не найден: {pdf}')

    result = extract_result(pdf)
    payload = result.to_payload()

    if json_out is not None:
        _write_json(json_out, payload)

    if quiet:
        print(msgspec.json.format(msgspec.json.encode(payload), indent=2).decode())
    else:
        print(render(result))

    # Ненулевой код возврата, если хоть один период не сведён или не найден
    # вовсе: в конвейере это должно останавливать выгрузку, а не проходить
    # незамеченным. Ноль из нуля — это пустой документ, а не успех.
    return 0 if result.periods and result.reconciled_periods == len(result.periods) else 1


@app.command
def evaluate(
    folder: Path = Path('samples'),
    /,
    *,
    json_out: Path | None = None,
    readme: Path | None = None,
) -> None:
    """Прогнать все образцы и показать таблицу точности.

    Args:
        folder: Папка с PDF.
        json_out: Записать сводку в файл JSON.
        readme: Подставить свежую таблицу в README.
    """
    if not folder.is_dir():
        raise cyclopts.ValidationError(f'папка не найдена: {folder}')

    report = evaluate_paths(sorted(folder.glob('*.pdf')))
    print(render_table(report))

    if json_out is not None:
        _write_json(json_out, report.to_payload())

    if readme is not None:
        update_readme(readme, report)


if __name__ == '__main__':
    # Логи в stderr, отчёт в stdout: решения эскалации и ответы платной ступени
    # должны быть видны, а JSON при `--quiet` остаётся чистым.
    configure_logging()
    app()

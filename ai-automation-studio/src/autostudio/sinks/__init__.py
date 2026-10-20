"""Куда пакеты пишут результат для клиента."""

import os
from pathlib import Path

from autostudio.clientconf import SinkConfig, require_env
from autostudio.sinks.base import Cell, Row, Rows, TableSink
from autostudio.sinks.csv_sink import CsvSink, csv_cell, neutralize_formula
from autostudio.sinks.gsheets import GoogleSheetsSink, sheet_cell

__all__ = [
    'Cell',
    'CsvSink',
    'GoogleSheetsSink',
    'Row',
    'Rows',
    'TableSink',
    'csv_cell',
    'make_sink',
    'neutralize_formula',
    'sheet_cell',
]


def make_sink(config: SinkConfig) -> TableSink:
    """Строит таблицу клиента по его конфигу.

    Args:
        config: Секция `[sink]` конфига клиента.

    Returns:
        CSV или Google Sheets.
    """
    if config.backend == 'google' and config.spreadsheet_id:
        return GoogleSheetsSink(config.spreadsheet_id, Path(require_env(config.credentials_env, os.environ)))
    return CsvSink(config.csv_dir)

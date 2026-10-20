"""Куда пакеты пишут результат для клиента."""

import os
from pathlib import Path

from autostudio.clientconf import SinkConfig, require_env
from autostudio.sinks.base import Row, TableSink
from autostudio.sinks.csv_sink import CsvSink, neutralize_formula
from autostudio.sinks.gsheets import GoogleSheetsSink

__all__ = ['CsvSink', 'GoogleSheetsSink', 'Row', 'TableSink', 'make_sink', 'neutralize_formula']


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

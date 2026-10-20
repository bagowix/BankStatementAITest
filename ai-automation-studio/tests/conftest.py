import datetime as dt
from pathlib import Path

import pytest

from autostudio.clock import FrozenClock

TBILISI = dt.timezone(dt.timedelta(hours=4))


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC))


@pytest.fixture
def write_toml(tmp_path: Path):
    def _write(text: str, name: str = 'client.toml') -> Path:
        path = tmp_path / name
        path.write_text(text, encoding='utf-8')
        return path

    return _write

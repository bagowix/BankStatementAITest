import datetime as dt
from pathlib import Path

import pytest

from autostudio.office.operator import Issuer, OperatorProfile
from autostudio.office.store import OfficeStore
from tests.office.helpers import OPERATOR_TOML, make_profile


@pytest.fixture
def operator() -> OperatorProfile:
    return make_profile()


@pytest.fixture
def issuer(operator: OperatorProfile) -> Issuer:
    return operator.issuer()


@pytest.fixture
def operator_file(tmp_path: Path) -> Path:
    path = tmp_path / 'operator.toml'
    path.write_text(OPERATOR_TOML, encoding='utf-8')
    return path


@pytest.fixture
def store(tmp_path: Path) -> OfficeStore:
    office = OfficeStore(tmp_path / 'office' / 'office.sqlite')
    office.init()
    return office


@pytest.fixture
def now() -> dt.datetime:
    return dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC)

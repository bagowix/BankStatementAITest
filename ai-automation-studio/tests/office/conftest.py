import datetime as dt
from pathlib import Path

import pytest

from autostudio.office.operator import OperatorProfile
from autostudio.office.store import OfficeStore
from tests.office.helpers import OPERATOR_TOML, VALID_IBAN


@pytest.fixture
def operator() -> OperatorProfile:
    return OperatorProfile(
        legal_name='Individual Entrepreneur Jane Example',
        legal_name_ru='ИП Джейн Пример',
        id_number='000000000',
        address='Georgia, Batumi, Example Street 1',
        bank_name='JSC Example Bank',
        iban=VALID_IBAN,
        swift='XXXXGE22',
        email='jane@example.com',
        telegram='jane_example',
        contract_fields={'SERVER_LOCATION': 'Example Country'},
    )


@pytest.fixture
def operator_file(tmp_path: Path) -> Path:
    path = tmp_path / 'operator.toml'
    path.write_text(OPERATOR_TOML, encoding='utf-8')
    return path


@pytest.fixture
def store(tmp_path: Path) -> OfficeStore:
    return OfficeStore(tmp_path / 'office' / 'office.sqlite')


@pytest.fixture
def now() -> dt.datetime:
    return dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC)

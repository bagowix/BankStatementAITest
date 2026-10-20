from pathlib import Path

import pytest

from autostudio.booking.config import BookingConfig
from tests.booking.helpers import App, make_config


@pytest.fixture
def config() -> BookingConfig:
    return make_config()


@pytest.fixture
def app(tmp_path: Path, config: BookingConfig) -> App:
    return App(tmp_path, config)

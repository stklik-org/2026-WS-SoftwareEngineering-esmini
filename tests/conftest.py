from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA

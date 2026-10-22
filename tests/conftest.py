import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT


def _has_data() -> bool:
    return len(list(DATA_DIR.glob("*.csv"))) >= 3


@pytest.fixture(scope="session")
def run():
    """The real dataset, loaded once."""
    if not _has_data():
        pytest.skip("ADRE exports not present next to the tests")
    from rotordyn import build_run

    return build_run(DATA_DIR)

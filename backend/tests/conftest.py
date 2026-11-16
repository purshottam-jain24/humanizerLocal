import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Isolate test data from the user's real data directory.
_TMP = tempfile.mkdtemp(prefix="humanwrite-test-")
os.environ["HUMANWRITE_DATA_DIR"] = _TMP

from tests.fixtures.make_fixtures import build_all  # noqa: E402


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("docx"))

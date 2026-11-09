import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.osm_model import OsmDocument  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "small.osm"


@pytest.fixture
def raw() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture
def doc(raw: bytes) -> OsmDocument:
    return OsmDocument.parse(raw)

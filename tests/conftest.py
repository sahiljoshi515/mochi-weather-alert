import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeOpenMeteo, frozen, index

from plugin import openmeteo, sources

T0 = datetime.fromisoformat(index()["clock"])  # the recorded answers' clock: today is 2026-10-16 (UTC)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("NANOAGENT_NETWORK"):
        return
    skip = pytest.mark.skip(reason="needs the network: set NANOAGENT_NETWORK=1")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def offline(monkeypatch):
    """Open-Meteo replayed from tests/fixtures/openmeteo/ and the clock frozen at the recording's;
    returns the fake, whose clock and overrides a test may change."""
    fake = FakeOpenMeteo(frozen(T0))
    monkeypatch.setattr(openmeteo, "OPENER", fake)
    monkeypatch.setattr(openmeteo, "SLEEP", lambda seconds: None)
    monkeypatch.setattr(sources, "CLOCK", lambda: fake.clock())
    sources.CACHE.clear()
    yield fake
    sources.CACHE.clear()

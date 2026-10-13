import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("NANOAGENT_NETWORK"):
        return
    skip = pytest.mark.skip(reason="needs the network: set NANOAGENT_NETWORK=1")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)

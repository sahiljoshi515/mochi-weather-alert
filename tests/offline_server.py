"""The plugin's MCP server with Open-Meteo replayed and the clock frozen, for the stdio tests:

    python tests/offline_server.py <ISO clock>
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]

from fakes import FakeOpenMeteo

from plugin import __main__ as server
from plugin import openmeteo, sources

if __name__ == "__main__":
    clock = datetime.fromisoformat(sys.argv[1])
    fake = FakeOpenMeteo(lambda: clock)
    openmeteo.OPENER = fake
    sources.CLOCK = lambda: clock
    server.main()

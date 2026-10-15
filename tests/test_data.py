"""The shipped snapshot and the page's copies of it: complete, consistent, and in sync."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from plugin.data import DATA, snapshot
from plugin.rules import CITY_COUNT, MAX_REPORT_ENTRIES, SNAPSHOT_END, SNAPSHOT_START, SOURCE_VARIABLES

ROOT = Path(__file__).resolve().parents[1]
DAYS = 92


def test_the_verifier_and_the_server_share_one_rules_module() -> None:
    assert (ROOT / "verifier" / "rules.py").read_bytes() == (ROOT / "src" / "plugin" / "rules.py").read_bytes()


def test_the_snapshot_covers_every_city_and_day() -> None:
    data = snapshot()
    names = [city.name for city in data.cities]
    assert len(names) == 30 == MAX_REPORT_ENTRIES == CITY_COUNT and names == sorted(names) and len(set(names)) == 30
    assert {"Santiago", "Auckland"} <= set(names)
    lengths = Counter()
    for city in data.cities:
        days = {date for (name, date) in data.days if name == city.name}
        assert len(days) == DAYS, city.name
        assert min(days) == SNAPSHOT_START and max(days) == SNAPSHOT_END
        for date in days:
            rows = data.day(city.name, date)
            times = [time for time, _ in rows]
            assert times == sorted(times) and len(set(times)) == len(times)
            assert all(time[:10] == date for time in times)
            lengths[len(rows)] += 1
    assert set(lengths) <= {23, 24, 25}
    assert lengths[23] == 2 and lengths[25] == 0
    assert sum(len(rows) for rows in data.days.values()) == 30 * DAYS * 24 - 2


def test_clocks_going_forward_give_23_hour_days() -> None:
    data = snapshot()
    santiago = [time for time, _ in data.day("Santiago", "2026-09-06")]
    assert len(santiago) == 23 and santiago[0] == "2026-09-06T01:00-03:00" and santiago[-1] == "2026-09-06T23:00-03:00"
    assert [time for time, _ in data.day("Santiago", "2026-09-05")][-1] == "2026-09-05T23:00-04:00"
    auckland = [time for time, _ in data.day("Auckland", "2026-09-27")]
    assert len(auckland) == 23 and "2026-09-27T02:00+12:00" not in auckland and "2026-09-27T02:00+13:00" not in auckland
    assert auckland[1] == "2026-09-27T01:00+12:00" and auckland[2] == "2026-09-27T03:00+13:00"


def test_values_are_plausible_decimals() -> None:
    data = snapshot()
    missing = Counter()
    for rows in data.days.values():
        for _, values in rows:
            for variable in SOURCE_VARIABLES:
                value = values[variable]
                if value is None:
                    missing[variable] += 1
                    continue
                assert isinstance(value, Decimal)
                if variable == "relative_humidity_2m":
                    assert 0 <= value <= 100 and value == value.to_integral_value()
                elif variable in ("precipitation", "wind_speed_10m"):
                    assert value >= 0
                else:
                    assert -60 <= value <= 60
    provenance = json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["missing_values"] == {variable: missing.get(variable, 0) for variable in SOURCE_VARIABLES}


def test_provenance_matches_the_files() -> None:
    provenance = json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))
    assert (provenance["start"], provenance["end"]) == (SNAPSHOT_START, SNAPSHOT_END)
    for name, digest in provenance["sha256"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == digest, name
    assert provenance["short_days"] == [
        {"city": "Auckland", "date": "2026-09-27", "hours": 23},
        {"city": "Santiago", "date": "2026-09-06", "hours": 23},
    ]
    assert provenance["long_days"] == []
    assert provenance["rows"] == sum(len(rows) for rows in snapshot().days.values())
    assert (DATA / "hourly.csv.gz").stat().st_size <= 2 * 1024 * 1024
    cities = json.loads((DATA / "cities.json").read_text(encoding="utf-8"))
    assert [city["timezone"] for city in cities] == [provenance["cities"][city["name"]]["timezone"] for city in cities]


PAGE_CHECK = Path.home() / "mochi-dev" / "harness" / "page_check.sh"


def gold_output() -> dict:
    """The report the gold case's one report_alerts call makes, as the task's output."""
    case = json.loads((ROOT / "tests" / "cases" / "gold.json").read_text(encoding="utf-8"))
    call = next(step for step in case["steps"] if step.get("tool") == "report_alerts")["arguments"]
    return {"task": call["task"], "alerts": call["alerts"], "unavailable": [{"city": "Cairo", "variables": ["humidity"]}]}


@pytest.mark.skipif(shutil.which("node") is None or not PAGE_CHECK.is_file(), reason="needs node and the lab's page_check.sh")
def test_the_renderer_draws_the_report_with_the_served_kit(tmp_path: Path) -> None:
    """The browser evaluates ui/renderers.js with NanoAgent's kit and draws each output class with it;
    `node --check` alone misses bad imports and a renderer that throws, so the lab's page check loads
    the module the way the page does and draws the gold report and an empty one."""
    output = gold_output()
    (tmp_path / "gold.json").write_text(json.dumps(output), encoding="utf-8")
    (tmp_path / "empty.json").write_text(json.dumps({**output, "alerts": [], "unavailable": []}), encoding="utf-8")
    for name, rows, words in (("gold", 1 + len(output["alerts"]), ["Dubai", "101.3", "High ≥ 101.3 °F", "Cairo", "humidity"]), ("empty", 0, ["No city crossed"])):
        completed = subprocess.run(["bash", str(PAGE_CHECK), str(ROOT), f"AlertReportOutput={tmp_path / name}.json"], capture_output=True, text=True, timeout=120, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        lines = completed.stdout.strip().splitlines()
        assert any(line.startswith("renderers.js: OK") and "default is object" in line for line in lines), completed.stdout
        assert any(line.startswith("kit/boot.js: OK") for line in lines), completed.stdout
        assert any(line.startswith(f"render AlertReportOutput: OK ({rows} table rows") for line in lines), completed.stdout
        assert all(": FAILED" not in line for line in lines), completed.stdout
        for word in words:
            assert word in completed.stdout, word


def test_the_renderer_names_no_page_host_or_network_global() -> None:
    """Rule 8: a renderer draws only its output, with the kit's modules (NanoAgent checks this too)."""
    for script in (ROOT / "ui").glob("*.js"):
        code = script.read_text(encoding="utf-8")
        for name in ("document", "window", "globalThis", "fetch", "localStorage", "parent", "location"):
            assert not any(part.strip().startswith(name) for part in code.replace("(", " ").split()), f"{script.name} uses {name}"
    assert sorted(path.name for path in (ROOT / "ui").iterdir() if not path.name.startswith(".")) == ["renderers.css", "renderers.js"]

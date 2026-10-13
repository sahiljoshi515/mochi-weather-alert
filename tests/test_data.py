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
from plugin.rules import SNAPSHOT_END, SNAPSHOT_START, SOURCE_VARIABLES, parse_condition

ROOT = Path(__file__).resolve().parents[1]
DAYS = 92


def test_the_verifier_and_the_server_share_one_rules_module() -> None:
    assert (ROOT / "verifier" / "rules.py").read_bytes() == (ROOT / "src" / "plugin" / "rules.py").read_bytes()


def test_the_snapshot_covers_every_city_and_day() -> None:
    data = snapshot()
    names = [city.name for city in data.cities]
    assert len(names) == 30 and names == sorted(names) and len(set(names)) == 30
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


def test_provenance_matches_the_files_and_the_page() -> None:
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
    page = (ROOT / "ui" / "snapshot.js").read_text(encoding="utf-8")
    shown = json.loads(page[page.index("{") : page.rindex("}") + 1])
    assert shown["start"] == SNAPSHOT_START and shown["end"] == SNAPSHOT_END
    assert shown["fetchedAt"] == provenance["fetched_at"]
    assert shown["shortDays"] == provenance["short_days"] and shown["longDays"] == provenance["long_days"]
    assert shown["cities"] == [{"name": c["name"], "country": c["country"], "timezone": c["timezone"]} for c in cities]


SAMPLES = [
    "max_temp >= 95", "max_temp>=95.00", "  rain > .5 ", "wind <= +007.10", "humidity < 100", "min_temp < -0.00",
    "min_temp <= -3.25", "temp >= 3", "max_temp = 3", "max_temp >= 1.234", "max_temp >=", ">= 3", "", "MAX_TEMP >= 3",
    "rain>0", "rain > 0.0", "humidity >= 90 ", "wind < 5.5.5",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_grammar_agrees_with_the_python_grammar() -> None:
    script = (
        "const { parseCondition, conditionProblem } = await import(process.argv[1]);"
        "const samples = JSON.parse(process.argv[2]);"
        "const out = samples.map((s) => parseCondition(s)?.text ?? null);"
        "out.push(conditionProblem('rain > 0.0', ['rain > 0']), conditionProblem('rain > 1', ['rain > 0']));"
        "console.log(JSON.stringify(out));"
    )
    url = (ROOT / "ui" / "grammar.js").as_uri()
    completed = subprocess.run(["node", "--input-type=module", "-e", script, "--", url, json.dumps(SAMPLES)], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    found = json.loads(completed.stdout)
    expected = []
    for sample in SAMPLES:
        try:
            expected.append(parse_condition(sample).text)
        except ValueError:
            expected.append(None)
    assert found[: len(SAMPLES)] == expected
    assert found[len(SAMPLES)] == "That rule means the same as rain > 0." and found[len(SAMPLES) + 1] is None

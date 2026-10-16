"""Differential test: the independent oracle (tests/oracle.py) and the verifier's expected answer agree
on every recorded day's data, for hundreds of random tasks whose thresholds often sit exactly on a
value (where >= and > part ways) and whose rounding often meets an exact tie."""

from __future__ import annotations

import random
from decimal import Decimal

import oracle
import pytest
from conftest import T0
from fakes import index, recorded
from helpers import Run
from slimconfig import plain_output

from plugin.rules import expected_from_series, plain_entries

VARIABLES = ("max_temp", "min_temp", "rain", "wind", "humidity")
OPS = (">=", ">", "<=", "<")
SCENARIOS = [scenario for scenario in index()["scenarios"] if scenario.get("pinned")]


def canonical(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "0") else text


def random_task(randomly: random.Random, date: str, locations: list[dict], step: int) -> dict:
    units = {"temperature_unit": randomly.choice("CF"), "wind_unit": randomly.choice(["kmh", "mph", "ms"]), "rain_unit": randomly.choice(["mm", "in"])}
    conditions = []
    for variable in randomly.sample(VARIABLES, randomly.randint(1, 4)):
        source = oracle.SOURCE[variable]
        location = randomly.choice(locations)
        readings = [value for value in location["hourly"][source] if value is not None]
        if readings and randomly.random() < 0.6:
            threshold = Decimal(repr(float(randomly.choice(readings))))  # a reading's own value: the boundary
            if variable in ("max_temp", "min_temp"):
                units["temperature_unit"] = "C"
            if variable == "wind":
                units["wind_unit"] = "kmh"
            if variable == "rain":
                units["rain_unit"] = "mm"
                threshold = sum((Decimal(repr(float(value))) for value in readings[:randomly.randint(1, 24)]), Decimal(0))
        else:
            threshold = Decimal(randomly.randint(-200, 1200)) / 10
        threshold = threshold.quantize(Decimal("0.01")) if threshold.as_tuple().exponent < -2 else threshold
        conditions.append(f"{variable} {randomly.choice(OPS)} {canonical(threshold)}")
    return {"date": date, "match": randomly.choice(["all", "any"]), **units, "conditions": conditions}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[f"{s['date']}@{s['clock'][11:16]}" for s in SCENARIOS])
def test_the_oracle_and_the_verifier_agree(offline, scenario: dict) -> None:
    from datetime import datetime

    clock = datetime.fromisoformat(scenario["clock"])
    run = Run({"date": scenario["date"], "match": "any", "temperature_unit": "C", "wind_unit": "kmh", "rain_unit": "mm", "conditions": ["max_temp >= 30"]}, offline, clock=clock)
    randomly = random.Random(f"weather-oracle:{scenario['date']}:{scenario['clock']}")
    for candidate in plain_output(run.pin)["candidates"]:
        locations = recorded(candidate["request"])["body"]
        step = candidate["source"]["step_hours"]
        for _ in range(12):
            task = random_task(randomly, scenario["date"], locations, step)
            want = oracle.answer(task, locations, step)
            got = plain_entries(expected_from_series(task, step, candidate["cities"]))
            assert got == want, (task, candidate["source"]["label"])


def test_ties_and_boundaries_are_exercised(offline) -> None:
    """The random tasks above hit thresholds equal to a value; here, at least, one tie and one equality."""
    run = Run({"date": "2026-07-07", "match": "any", "temperature_unit": "C", "wind_unit": "kmh", "rain_unit": "mm", "conditions": ["humidity >= 83.25", "max_temp >= 33.3"]}, offline, clock=T0)
    candidate = plain_output(run.pin)["candidates"][0]
    locations = recorded(candidate["request"])["body"]
    want = oracle.answer(run.task, locations, 1)
    cities = {entry["city"]: entry for entry in want["alerts"]}
    assert cities["Perth"]["values"]["humidity"] == 83.3 and cities["Perth"]["matched"] == ["humidity >= 83.25"]
    assert "max_temp >= 33.3" in cities["Athens"]["matched"]
    assert plain_entries(expected_from_series(run.task, 1, candidate["cities"])) == want

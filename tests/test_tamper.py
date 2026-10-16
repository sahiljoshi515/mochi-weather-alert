"""R2-02: the files a run leaves in the member's folder (its marker, receipt and report) can be edited between
the run and the verdict. Whatever they hold, verify_task returns a verdict and never raises: an odd value is
refused, naming the file and the field, before any arithmetic touches it. The fuzz below is the challenger's
round-2 file fuzz (1,800 mutations: one field, at any depth, replaced by an odd JSON value)."""

from __future__ import annotations

import copy
import json
import random

import pytest
from conftest import T0
from helpers import Run, failed
from slimconfig import build_output, plain_output

import verifier
from contract import AlertInput, AlertReportOutput, PinOutput
from plugin import alerts
from plugin.rules import key_of, series_sha256

TASK = {"match": "any", "temperature_unit": "C", "wind_unit": "kmh", "rain_unit": "mm", "conditions": ["max_temp >= 20", "rain > 1"]}
DATES = ("2026-09-06", "2026-10-15", "2026-10-30")  # the IFS archive, a forecast run, the seasonal outlook
ODD = [0, -1, 1.5, 10**400, float("nan"), float("inf"), None, True, [], {}, "", "x", "2026-09-06", "0" * 64, [1, 2], {"a": 1}]
MUTATIONS = 600  # per date: 1,800 in all


def paths(node, prefix=()):
    """Every field's path in a JSON value; of a list, its first and last entries'."""
    yield prefix
    if isinstance(node, dict):
        for key, value in node.items():
            yield from paths(value, (*prefix, key))
    elif isinstance(node, list) and node:
        for index in sorted({0, len(node) - 1}):
            yield from paths(node[index], (*prefix, index))


def set_at(node, path, value) -> None:
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def gold(offline, date: str) -> tuple[Run, dict]:
    run = Run({"date": date, **TASK}, offline, clock=T0)
    return run, run.gold()


def verdict(run: Run, report: dict) -> dict:
    return verifier.verify_task(
        "daily-alert",
        build_output(run.task, AlertInput),
        build_output(report, AlertReportOutput),
        build_output(plain_output(run.pin), PinOutput),
        str(run.workspace),
    )


@pytest.mark.parametrize("date", DATES)
def test_any_value_in_any_field_of_a_runs_files_gets_a_verdict_never_an_exception(offline, date: str) -> None:
    run, report = gold(offline, date)
    files = {"marker": run.folder / "run.json", "receipt": run.folder / f"{date}.source.json", "report": run.folder / f"{date}.json"}
    originals = {name: path.read_text() for name, path in files.items()}
    parsed = {name: json.loads(text) for name, text in originals.items()}
    fields = {name: [path for path in paths(value) if path] for name, value in parsed.items()}
    rng = random.Random(f"R2-02 {date}")
    outcomes = {"valid": 0, "invalid": 0}
    for _ in range(MUTATIONS):
        name = rng.choice(sorted(files))
        path = rng.choice(fields[name])
        value = rng.choice(ODD)
        data = copy.deepcopy(parsed[name])
        set_at(data, path, value)
        files[name].write_text(json.dumps(data))
        try:
            found = verdict(run, report)
        finally:
            files[name].write_text(originals[name])
        assert isinstance(found["valid"], bool) and 0.0 <= found["score"] <= 1.0
        assert "cannot be read" not in found["message"], f"{name} {path} = {value!r} reached the last resort: {found['message']}"
        if found["valid"]:
            assert data == parsed[name], f"{name} {path} = {value!r} changed the file and still passed"
        outcomes["valid" if found["valid"] else "invalid"] += 1
    assert outcomes["invalid"] > MUTATIONS * 0.8, outcomes
    assert verdict(run, report)["valid"], "the files are restored: the run passes again"


def rehash(run: Run, date: str, change) -> None:
    """Edit the receipt and fix its hash, and the report's, as a careful forger would."""
    path = run.folder / f"{date}.source.json"
    receipt = json.loads(path.read_text())
    change(receipt)
    receipt["sha256"] = series_sha256(date, key_of(receipt["source"]), receipt["cities"])
    path.write_text(json.dumps(receipt))
    report_path = run.folder / f"{date}.json"
    saved = json.loads(report_path.read_text())
    saved["sha256"] = receipt["sha256"]
    report_path.write_text(json.dumps(saved))


def edit(path, change) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


@pytest.mark.parametrize(
    ("date", "file", "change", "check", "words"),
    [
        # The challenger's six crash signatures, each now a verdict naming the file and the field.
        ("2026-09-06", "receipt", lambda data: data["cities"][0]["precipitation"].__setitem__(0, 10**400), "agent-read", "Athens: precipitation reading 10000000000000000000… at 2026-09-05T21:00Z is outside 0 to 1000"),
        ("2026-09-06", "receipt", lambda data: data["cities"][0]["precipitation"].__setitem__(24, float("nan")), "agent-read", "Athens: precipitation reading nan at 2026-09-06T21:00Z is not a finite number"),
        ("2026-10-15", "report", lambda data: data["alerts"][0]["values"].__setitem__("max_temp", 10**400), "report-saved", "values must map condition variables to finite numbers (at most 1e9 in size)"),
        ("2026-10-15", "receipt", lambda data: data["source"].__setitem__("run", float("inf")), "agent-read", "source: a forecast's run must be a UTC time YYYY-MM-DDTHH:MM at 00, 06, 12 or 18"),
        ("2026-10-15", "report", lambda data: data["source"].__setitem__("run", -1), "report-saved", "a forecast's run must be a UTC time YYYY-MM-DDTHH:MM at 00, 06, 12 or 18"),
        ("2026-09-06", "receipt", lambda data: data["source"].__setitem__("run", 3), "agent-read", "source: only a forecast names a run"),
    ],
)
def test_each_crash_the_challenger_found_is_a_verdict_naming_the_file_and_field(offline, date, file, change, check, words) -> None:
    run, report = gold(offline, date)
    path = run.folder / (f"{date}.source.json" if file == "receipt" else f"{date}.json")
    edit(path, change)
    found = verdict(run, report)
    assert not found["valid"] and failed(found)[0] == check, found["message"]
    assert f"reports/{alerts.RUN}/{path.name}" in found["message"] and words in found["message"], found["message"]


def test_a_rehashed_receipt_with_a_numeric_fetched_at_is_a_verdict_not_a_crash(offline) -> None:
    """The sixth signature: a re-hashed outlook receipt whose fetched_at is a number reached the comparison of
    two fetch times; fetched_at is now checked as a UTC time first."""
    run, report = gold(offline, "2026-10-30")
    def change(receipt):
        receipt["cities"][0]["temperature_2m"][1] = 9.0
        receipt["fetched_at"] = 1760587220
    rehash(run, "2026-10-30", change)
    found = verdict(run, report)
    assert failed(found)[0] == "agent-read"
    assert "2026-10-30.source.json was altered after get_daily: fetched_at must be a UTC time YYYY-MM-DDTHH:MM:SSZ" in found["message"]


def test_a_receipt_reading_with_more_decimals_than_open_meteo_gives_is_refused_by_name(offline) -> None:
    """WA-09: a consistent (re-hashed) receipt still may not hold a reading of more than 2 decimals."""
    run, report = gold(offline, "2026-09-06")
    rehash(run, "2026-09-06", lambda receipt: receipt["cities"][1]["wind_speed_10m"].__setitem__(4, 12.345))
    found = verdict(run, report)
    assert failed(found)[0] == "agent-read"
    assert "Auckland: wind_speed_10m reading 12.345 at" in found["message"] and "has more than 2 decimals" in found["message"]


def test_a_file_too_deep_or_too_large_to_parse_is_refused_by_name(offline) -> None:
    run, report = gold(offline, "2026-09-06")
    path = run.folder / "2026-09-06.source.json"
    path.write_text("[" * 100_000 + "]" * 100_000)
    found = verdict(run, report)
    assert failed(found)[0] == "agent-read" and f"reports/{alerts.RUN}/2026-09-06.source.json is not a receipt: not JSON" in found["message"]
    path.write_text(" " * 4_000_001)
    found = verdict(run, report)
    assert f"reports/{alerts.RUN}/2026-09-06.source.json is not a receipt: too large" in found["message"]

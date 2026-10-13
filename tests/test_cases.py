"""The scripted-harness cases under tests/cases/: each gold report is exactly what the shared rules
give for the snapshot, and each wrong case gets the verdict it expects from the real verifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from slimconfig import build_output, plain_output

import verifier
from contract import AlertInput, PinOutput
from plugin.alerts import pin_alerts
from plugin.rules import plain_report
from verifier.pin import expected_of

CASES = sorted((Path(__file__).resolve().parent / "cases").glob("*.json"))


def test_there_are_gold_and_wrong_cases() -> None:
    names = {case.stem for case in CASES}
    assert {"gold", "gold_dst", "wrong_unit", "bankers_rounding", "boundary", "missing_city", "any_vs_all", "extra_city", "no_report", "dst_utc_day"} <= names


@pytest.mark.parametrize("case_file", CASES, ids=[case.stem for case in CASES])
def test_each_case_expects_the_verifiers_verdict(case_file: Path, tmp_path: Path) -> None:
    case = json.loads(case_file.read_text(encoding="utf-8"))
    arguments = build_output(case["arguments"], AlertInput)
    pinned = pin_alerts(arguments, tmp_path / "scratch")
    gold = plain_report(case["arguments"]["date"], expected_of(arguments, pinned))
    reports = [step for step in case["steps"] if step.get("tool") == "report_alerts" and not step.get("expect_error")]
    assert all(step["tool"] in {"list_cities", "get_daily", "get_hourly", "report_alerts"} for step in case["steps"] if "tool" in step)
    assert all(len(step["arguments"]["cities"]) <= 10 for step in case["steps"] if step.get("tool") == "get_daily")
    if reports:
        (tmp_path / "weather-alert").mkdir()
        (tmp_path / "weather-alert" / f"{case['arguments']['date']}.json").write_text(json.dumps(reports[-1]["arguments"]))
    found = verifier.verify_task("daily-alert", arguments, build_output(plain_output(pinned), PinOutput), str(tmp_path))
    expect = case["expect"]
    assert found["valid"] == expect["valid"], found["message"]
    if "message_contains" in expect:
        assert expect["message_contains"] in found["message"]
    if "score_min" in expect:
        assert found["score"] >= expect["score_min"]
    if "score_max" in expect:
        assert found["score"] <= expect["score_max"]
    if expect["valid"]:
        assert reports[-1]["arguments"] == gold

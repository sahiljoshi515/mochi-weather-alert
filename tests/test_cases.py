"""The scripted-harness cases under tests/cases/: each gold report is exactly what the shared rules
give for the snapshot, and each wrong case gets the verdict it expects from the real verifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from slimconfig import build_output, plain_output

import verifier
from contract import AlertInput, PinOutput, ReportInput
from plugin.alerts import pin_alerts, report_alerts, write_run_marker
from plugin.rules import plain_report
from verifier.pin import expected_of

CASES = sorted((Path(__file__).resolve().parent / "cases").glob("*.json"))


def test_there_are_gold_and_wrong_cases() -> None:
    names = {case.stem for case in CASES}
    assert {"gold", "gold_chunked", "gold_dst", "batched_daily", "corrected", "unexamined", "overlapping_calls", "stale_old_version", "stale_previous_run", "stale_no_calls", "wrong_unit", "bankers_rounding", "boundary", "missing_city", "any_vs_all", "extra_city", "no_report", "dst_utc_day"} <= names


def by_city(entries):
    return sorted(entries, key=lambda entry: entry["city"])


@pytest.mark.parametrize("case_file", CASES, ids=[case.stem for case in CASES])
def test_each_case_expects_the_verifiers_verdict(case_file: Path, tmp_path: Path) -> None:
    case = json.loads(case_file.read_text(encoding="utf-8"))
    arguments = build_output(case["arguments"], AlertInput)
    pinned = pin_alerts(arguments, tmp_path / "scratch")
    gold = plain_report(case["arguments"]["date"], expected_of(arguments, pinned))
    tool_steps = [step for step in case["steps"] if "tool" in step and not step.get("expect_error")]
    assert all(step["tool"] in {"list_cities", "get_daily", "get_hourly", "report_alerts"} for step in tool_steps)
    assert all(len(step["arguments"]["cities"]) <= 30 for step in tool_steps if step["tool"] == "get_daily")
    assert len(tool_steps) <= 5, "every case leaves room for the answer within Mochi's 6 steps"
    if case_file.stem in ("gold_chunked", "gold_dst"):
        assert [step["tool"] for step in tool_steps] == ["get_daily", "report_alerts", "report_alerts", "report_alerts"]
    # as the host does: the server marks the run at its start, python steps act behind the tools' back,
    # and the report steps go through report_alerts itself, so the merge is what is exercised
    write_run_marker(tmp_path)
    for step in case["steps"]:
        if "python" in step:
            exec(step["python"], {"workspace": tmp_path, "outputs": {}, "json": json, "Path": Path})  # noqa: S102 - the case's own step
        elif step.get("tool") == "report_alerts" and not step.get("expect_error"):
            report_alerts(build_output(step["arguments"], ReportInput), tmp_path)
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
        saved = json.loads((tmp_path / "weather-alert" / f"{case['arguments']['date']}.json").read_text())
        assert saved["examined"] == gold["examined"] and saved["run"] == json.loads((tmp_path / "weather-alert" / "run.json").read_text())["run"]
        assert by_city(saved["alerts"]) == by_city(gold["alerts"]) and by_city(saved["unavailable"]) == by_city(gold["unavailable"])

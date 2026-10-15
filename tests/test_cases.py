"""The scripted-harness cases under tests/cases/: each gold submission is exactly what the shared rules
give for the snapshot, and each wrong case gets the verdict it expects from the real verifier, with the
report steps applied through report_alerts itself and the submission taken as the harness takes it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from slimconfig import build_output, plain_output

import verifier
from contract import AlertInput, AlertReportOutput, PinOutput, ReportInput
from plugin.alerts import pin_alerts, report_alerts, write_run_marker
from plugin.rules import expected_output, plain_task
from verifier.pin import expected_of

CASES = sorted((Path(__file__).resolve().parent / "cases").glob("*.json"))
TASK_TOOLS = {"get_daily", "report_alerts"}  # what the manifest gives the task's agent, plus submit


def test_there_are_gold_and_wrong_cases() -> None:
    names = {case.stem for case in CASES}
    assert {"gold", "gold_chunked", "gold_dst", "batched_daily", "corrected", "unexamined", "overlapping_calls", "stale_old_version", "stale_previous_run", "stale_no_calls", "divergent_submission", "wrong_unit", "bankers_rounding", "boundary", "missing_city", "any_vs_all", "extra_city", "no_report", "dst_utc_day"} <= names


def by_city(entries):
    return sorted(entries, key=lambda entry: entry["city"])


def resolve(value, outputs):
    """A "$ref:<save_as>.<key>..." as the harness resolves it."""
    if isinstance(value, str) and value.startswith("$ref:"):
        node = outputs
        for part in value[5:].split("."):
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node
    return value


def replay(case: dict, workspace: Path):
    """Apply the case's steps as the host does: the server marks the run at its start, python steps act
    behind the tools' back, report steps go through report_alerts itself, and the submit step's
    payload (a literal, or a $ref to an earlier output) is the task's output."""
    write_run_marker(workspace)
    outputs = {}
    submitted = None
    for step in case["steps"]:
        if "python" in step:
            exec(step["python"], {"workspace": workspace, "outputs": outputs, "json": json, "Path": Path})  # noqa: S102 - the case's own step
        elif step.get("tool") == "report_alerts" and not step.get("expect_error"):
            out = plain_output(report_alerts(build_output(step["arguments"], ReportInput), workspace))
            if step.get("save_as"):
                outputs[step["save_as"]] = out
        elif step.get("tool") == "submit":
            submitted = resolve(step["arguments"], outputs)
    return submitted


@pytest.mark.parametrize("case_file", CASES, ids=[case.stem for case in CASES])
def test_each_case_expects_the_verifiers_verdict(case_file: Path, tmp_path: Path) -> None:
    case = json.loads(case_file.read_text(encoding="utf-8"))
    arguments = build_output(case["arguments"], AlertInput)
    pinned = pin_alerts(arguments, tmp_path / "scratch")
    gold = expected_output(plain_task(arguments), expected_of(arguments, pinned))
    tool_steps = [step for step in case["steps"] if "tool" in step and not step.get("expect_error")]
    assert all(step["tool"] in TASK_TOOLS | {"submit"} for step in tool_steps)
    assert all(len(step["arguments"]["cities"]) <= 30 for step in tool_steps if step["tool"] == "get_daily")
    assert sum(1 for step in case["steps"] if "tool" in step) <= 5, "every case leaves the host's sixth step for the final answer"
    assert [step["tool"] for step in case["steps"] if "tool" in step][-1] == "submit", "every case ends by submitting"
    if case_file.stem in ("gold_chunked", "gold_dst"):
        assert [step["tool"] for step in tool_steps] == ["get_daily", "report_alerts", "report_alerts", "report_alerts", "submit"]
    submitted = replay(case, tmp_path)
    found = verifier.verify_task("daily-alert", arguments, build_output(submitted, AlertReportOutput), build_output(plain_output(pinned), PinOutput), str(tmp_path))
    expect = case["expect"]
    assert found["valid"] == expect["valid"], found["message"]
    if "message_contains" in expect:
        assert expect["message_contains"] in found["message"]
    if "score_min" in expect:
        assert found["score"] >= expect["score_min"]
    if "score_max" in expect:
        assert found["score"] <= expect["score_max"]
    if expect["valid"]:
        assert by_city(submitted["alerts"]) == by_city(gold["alerts"]) and by_city(submitted["unavailable"]) == by_city(gold["unavailable"])
        saved = json.loads((tmp_path / "reports" / f"{case['arguments']['date']}.json").read_text())
        assert sorted(saved["examined"]) == sorted(gold_examined(pinned)) and saved["run"] == json.loads((tmp_path / "reports" / "run.json").read_text())["run"]
        assert by_city(saved["alerts"]) == by_city(gold["alerts"]) and by_city(saved["unavailable"]) == by_city(gold["unavailable"])


def gold_examined(pinned) -> list[str]:
    return [row.city for row in pinned.metric]

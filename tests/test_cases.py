"""The scripted cases under tests/cases/, replayed offline through the real tools and the real verifier
(the answers recorded, the clock frozen): each gets the verdict it expects, and each keeps to the
host's budget and the prescribed path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from replay import matches, replay

CASES = sorted((Path(__file__).resolve().parent / "cases").glob("*.json"))
TASK_TOOLS = {"get_daily", "report_alerts", "submit"}


def test_there_are_gold_wrong_and_integrity_cases() -> None:
    names = {case.stem for case in CASES}
    assert {"gold", "gold_1940", "gold_dst_santiago", "gold_seam_era5", "gold_seam_ifs", "boundary", "bankers_rounding", "extra_alert", "missing_alert"} <= names
    assert {"unexamined", "divergent_submission", "no_report", "tampered_receipt", "wrong_data_label", "refused_date", "check_date_today"} <= names
    assert {"replayed_earlier_run", "unknown_city_alert", "matched_reordered"} <= names


@pytest.mark.parametrize("case_file", CASES, ids=[case.stem for case in CASES])
def test_each_case_gets_its_verdict(offline, case_file: Path) -> None:
    case = json.loads(case_file.read_text(encoding="utf-8"))
    problems = matches(replay(case, case_file.parent), case["expect"])
    assert not problems, problems


@pytest.mark.parametrize("case_file", CASES, ids=[case.stem for case in CASES])
def test_each_case_keeps_to_the_hosted_budget(case_file: Path) -> None:
    case = json.loads(case_file.read_text(encoding="utf-8"))
    if case["task"] != "daily-alert" or "error_contains" in case["expect"]:
        return
    tools = [step["tool"] for step in case["steps"] if "tool" in step]
    assert set(tools) <= TASK_TOOLS and tools[-1] == "submit"
    assert len(tools) <= 5, "the hosted agent has six steps and the last may not call a tool"
    if case_file.stem.startswith("gold"):
        assert tools == ["get_daily", "report_alerts", "report_alerts", "submit"], "the prescribed path"
        assert all(len(step["arguments"]["cities"]) == 30 for step in case["steps"] if step.get("tool") == "get_daily")

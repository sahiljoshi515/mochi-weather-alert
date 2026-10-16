"""The daily-alert verdict: a gold run on every kind of data is valid, and each of the 21 constraints
fails on its own case with its own message; plus the per-call checks and the date check's verdict."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import scripted_judge
from conftest import T0
from fakes import Turns, index, recorded
from helpers import Run, failed, task_for, unchecked
from slimconfig import build_output, plain_output

import verifier
from contract import (
    AlertInput,
    AlertReportOutput,
    DailyInput,
    DailyOutput,
    DateInput,
    DateOutput,
    PinOutput,
    ReportInput,
)
from plugin import alerts, openmeteo
from plugin.rules import CITY_NAMES, WINDOW_LAST_MINUTE, day_number, stamp_minutes

GOLD_DATES = sorted({scenario["date"] for scenario in index()["scenarios"] if scenario["clock"] == index()["clock"]})


@pytest.mark.parametrize("date", GOLD_DATES)
def test_a_gold_run_on_every_recorded_day_holds_every_constraint(offline, date: str) -> None:
    run = Run(task_for(date), offline, clock=T0)
    run.gold()
    verdict = run.verdict()
    assert verdict["valid"], verdict["message"]
    assert verdict["score"] == 1.0 and verdict["message"].startswith("All 21 checks hold")
    assert [entry["ok"] for entry in verdict["details"]["constraints"]] == [True] * 21
    assert verdict["details"]["source"] == run.daily["source"]["label"]
    assert len(json.dumps(verdict)) < 8000, "the verdict lands in the chat's context: keep it small"


def test_no_answer_key_fails_first_and_scores_zero(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = run.gold()
    found = verifier.verify_task("daily-alert", build_output(run.task, AlertInput), build_output(report, AlertReportOutput), None, str(run.workspace))
    assert not found["valid"] and found["score"] == 0.0
    assert failed(found)[0] == "answer-key" and "never fetched the day" in found["message"]


def pinned(run: Run) -> dict:
    return plain_output(run.pin)


def verdict_with_pin(run: Run, pin: dict) -> dict:
    return verifier.verify_task("daily-alert", build_output(run.task, AlertInput), build_output(run.last["report"], AlertReportOutput), build_output(pin, PinOutput), str(run.workspace))


def test_a_pin_for_another_date_fails_answer_key(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    pin = pinned(run)
    pin["date"] = "2026-07-08"
    found = verdict_with_pin(run, pin)
    assert failed(found)[0] == "answer-key" and found["score"] == 0.0


def test_a_tampered_pinned_reading_fails_answer_key_data(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    pin = pinned(run)
    pin["candidates"][0]["cities"][3]["temperature_2m"][5] += 0.1
    found = verdict_with_pin(run, pin)
    assert failed(found)[0] == "answer-key-data" and "sha256" in found["message"]
    assert "agent-source" in unchecked(found)


def test_pinned_alerts_that_disagree_with_their_readings_fail_answer_key_data(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    pin = pinned(run)
    pin["candidates"][0]["alerts"] = pin["candidates"][0]["alerts"][1:]
    found = verdict_with_pin(run, pin)
    assert failed(found)[0] == "answer-key-data" and "disagree with its readings" in found["message"]


def test_a_pin_on_data_the_rules_do_not_choose_fails_answer_key_rules(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    pin = pinned(run)
    # read a day before the date: the rules would choose a forecast run, not the archive
    pin["clock"] = "2026-07-06T00:00"
    pin["candidates"][0]["clock"] = "2026-07-06T00:00"
    pin["candidates"][0]["source"]["today"] = "2026-07-06"
    found = verdict_with_pin(run, pin)
    assert failed(found)[0] == "answer-key-rules" and "breaks the date rules" in found["message"]


def test_a_missing_run_marker_fails_run_marker(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    (run.workspace / "reports" / "run.json").unlink()
    found = run.verdict()
    assert failed(found)[0] == "run-marker" and found["score"] == 0.0


def test_a_receipt_from_an_earlier_run_fails_agent_read(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    path = run.workspace / "reports" / "2026-07-07.source.json"
    receipt = json.loads(path.read_text())
    receipt["run"] = "0123456789abcdef"
    path.write_text(json.dumps(receipt))
    found = run.verdict()
    assert failed(found)[0] == "agent-read" and "earlier run" in found["message"]


def test_a_receipt_changed_after_get_daily_fails_agent_read(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    path = run.workspace / "reports" / "2026-07-07.source.json"
    receipt = json.loads(path.read_text())
    receipt["cities"][0]["temperature_2m"][0] += 1
    path.write_text(json.dumps(receipt))
    found = run.verdict()
    assert failed(found)[0] == "agent-read" and "its own sha256" in found["message"]


def test_no_receipt_fails_agent_read(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    (run.workspace / "reports" / "2026-07-07.source.json").unlink()
    found = run.verdict()
    assert failed(found)[0] == "agent-read" and "never read" in found["message"]


def test_data_that_changed_upstream_between_the_fetches_fails_agent_data_with_run_again(offline) -> None:
    task = task_for("2026-07-07")
    url = recorded_url(offline, task)
    body = recorded(url)["body"]
    changed = copy.deepcopy(body)
    changed[0]["hourly"]["temperature_2m"][30] += 0.5
    offline.overrides[url] = Turns(body, changed)  # the answer key sees one version, the agent the next
    run = Run(task, offline, clock=T0)
    run.gold()
    found = run.verdict()
    assert failed(found) == ["agent-data"], found["message"]
    assert "changed upstream" in found["message"] and "run again" in found["message"] and "Athens temperature_2m" in found["message"]
    assert found["details"]["rerun"] is True and found["score"] == 0.0
    assert found["details"]["agent_data_check"] == "against the data the agent read, the report is exact"


def recorded_url(fake, task: dict) -> str:
    """The URL the archive request for the task's date uses (recorded under the frozen clock)."""
    probe = Run(task, fake, clock=T0)
    return probe.pin.candidates[0].request


def test_an_agent_reading_a_source_the_answer_key_never_pinned_fails_agent_source(offline) -> None:
    # Pinned at 15:05 UTC on 2026-10-15 for 2026-10-17: the runs of 00 and 06 UTC. An agent reading six
    # hours later picks the 12 UTC run, which the answer key never pinned. The recorded 18 UTC run of the
    # same day stands in for all three (its stamps cover the date's every local day).
    clock = datetime.fromisoformat("2026-10-15T15:05:00+00:00")
    task = task_for("2026-10-17")
    stand_in = recorded(next(url for url in index()["answers"] if "run=2026-10-15T18:00" in url))["body"]
    day = day_number("2026-10-17")
    for run in ("2026-10-15T00:00", "2026-10-15T06:00", "2026-10-15T12:00"):
        hours = (day * 1440 + WINDOW_LAST_MINUTE - stamp_minutes(run)) // 60 + 1
        offline.overrides[openmeteo.run_url(run, hours)] = stand_in
    run = Run(task, offline, clock=clock, agent_after=timedelta(hours=6, minutes=5))
    assert [candidate.source.run for candidate in run.pin.candidates] == ["2026-10-15T00:00", "2026-10-15T06:00"]
    run.gold()
    assert run.daily["source"]["run"] == "2026-10-15T12:00"
    found = run.verdict()
    assert failed(found)[0] == "agent-source", found["message"]
    assert "more than the 10 min allowed" in found["message"] and "run again" in found["message"]


def test_an_agent_reading_within_ten_minutes_reads_a_pinned_run(offline) -> None:
    clock = datetime.fromisoformat("2026-10-15T15:05:00+00:00")
    run = Run(task_for("2026-10-16"), offline, clock=clock, agent_after=timedelta(minutes=9))
    assert len(run.pin.candidates) == 2
    run.gold()
    assert run.daily["source"]["run"] == "2026-10-15T06:00"
    assert run.verdict()["valid"]
    run = Run(task_for("2026-10-16"), offline, clock=clock, agent_after=timedelta(seconds=10))
    run.gold()
    assert run.daily["source"]["run"] == "2026-10-15T00:00", "10 minutes behind the clock: the older run"
    assert run.verdict()["valid"]


def test_no_report_fails_report_saved(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    judged = scripted_judge.judge(run.task, run.daily)
    submitted = {"task": run.task, "data": run.daily["source"], **judged}
    found = run.verdict(submitted)
    assert failed(found)[0] == "report-saved" and "no report" in found["message"]


def test_a_report_left_by_an_earlier_run_fails_report_saved(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    path = run.workspace / "reports" / "2026-07-07.json"
    report = json.loads(path.read_text())
    report["run"] = "fedcba9876543210"
    path.write_text(json.dumps(report))
    found = run.verdict()
    assert failed(found)[0] == "report-saved" and "earlier one" in found["message"]


def test_a_submission_for_another_task_fails_submitted_task(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = run.gold()
    found = run.verdict({**report, "task": {**report["task"], "match": "all"}})
    assert "submitted-task" in failed(found) and found["score"] == 0.0


def test_a_malformed_submission_fails_submitted_shape(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = run.gold()
    broken = copy.deepcopy(report)
    broken["alerts"][0]["values"].pop(next(iter(broken["alerts"][0]["values"])))
    found = run.verdict(broken)
    assert failed(found) == ["submitted-shape"] and "malformed" in found["message"]


def test_a_submission_that_is_not_the_saved_report_fails_submitted_as_saved(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = run.gold()
    found = run.verdict({**report, "alerts": report["alerts"][1:]})
    assert failed(found)[0] == "submitted-as-saved" and found["score"] == 0.0
    assert "alerts-complete" in failed(found), "the alerts are still compared, for the details"


def test_a_submission_naming_other_data_fails_names_data(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = run.gold()
    found = run.verdict({**report, "data": {**report["data"], "model": "era5", "label": "ERA5 reanalysis (ECMWF), hourly"}})
    assert failed(found) == ["names-data"] and found["score"] == 0.0


def test_an_unexamined_half_fails_all_examined_and_costs_coverage(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    calls = scripted_judge.plan(run.task, run.daily)["calls"]
    report = run.report(calls[:1])["report"]
    found = run.verdict(report)
    assert "all-examined" in failed(found) and "15 cities unexamined" in found["message"]
    assert 0 < found["score"] < 1


@pytest.mark.parametrize(
    ("mutation", "constraint", "words"),
    [
        ("missing_alert", "alerts-complete", "Missing alert"),
        ("extra_alert", "no-extra-alerts", "Extra alert"),
        ("fabricated", "values-exact", "Wrong value"),
        ("wrong_matched", "matched-exact", "Wrong matched conditions"),
        ("empty", "alerts-complete", "Missing alert"),
        ("any_vs_all", "alerts-complete", "Missing alert"),
    ],
)
def test_each_wrong_judgement_fails_its_constraint(offline, mutation: str, constraint: str, words: str) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    run.report(scripted_judge.plan(run.task, run.daily, [mutation])["calls"])
    found = run.verdict()
    assert failed(found)[0] == constraint, (failed(found), found["message"])
    assert words in found["message"]
    assert 0 <= found["score"] < 1


def test_the_unavailable_constraints_on_1940s_missing_readings(offline) -> None:
    run = Run(task_for("1940-01-01"), offline, clock=T0)
    run.read()
    gold = scripted_judge.judge(run.task, run.daily)
    assert gold["unavailable"], "ERA5 has no 1939 hours: cities east of UTC miss readings on 1940-01-01"
    run.report(scripted_judge.halves(run.task, gold))
    assert run.verdict()["valid"]
    run2 = Run(task_for("1940-01-01"), offline, clock=T0)
    run2.read()
    run2.report(scripted_judge.plan(run2.task, run2.daily, ["drop_unavailable"])["calls"])
    found = run2.verdict()
    assert failed(found)[0] == "unavailable-complete" and "has no" in found["message"] and "reading at" in found["message"]


def test_wrong_unavailable_variables_fail_unavailable_exact(offline) -> None:
    task = task_for("1940-01-01", conditions=["max_temp >= 50", "rain > 0.5"], temperature_unit="C", rain_unit="mm")
    run = Run(task, offline, clock=T0)
    run.read()
    gold = scripted_judge.judge(task, run.daily)
    entry = next(entry for entry in gold["unavailable"] if len(entry["variables"]) == 2)
    entry["variables"] = entry["variables"][:1]
    run.report(scripted_judge.halves(task, gold))
    found = run.verdict()
    assert failed(found)[0] == "unavailable-exact" and "Wrong variables" in found["message"]


def test_an_extra_unavailable_city_fails_no_extra_unavailable(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    gold = scripted_judge.judge(run.task, run.daily)
    city = next(name for name in CITY_NAMES if name not in {entry["city"] for entry in gold["alerts"]})
    gold["unavailable"].append({"city": city, "variables": ["max_temp"]})
    run.report(scripted_judge.halves(run.task, gold))
    found = run.verdict()
    assert failed(found)[0] == "no-extra-unavailable" and "has every reading" in found["message"]


def test_bankers_rounding_fails_values_exact_where_a_tie_exists(offline) -> None:
    # Perth's mean humidity on 2026-07-07 is 1998/24 = 83.25 exactly: half up gives 83.3, half even 83.2.
    task = task_for("2026-07-07", conditions=["humidity >= 80"])
    run = Run(task, offline, clock=T0)
    run.read()
    perth = next(row for row in run.daily["cities"] if row["city"] == "Perth")
    assert perth["values"]["humidity"] == 83.25
    run.report(scripted_judge.plan(task, run.daily, ["bankers"])["calls"])
    found = run.verdict()
    assert failed(found) == ["values-exact"] and "Perth humidity is 83.3 (exact 83.250000000, rounded half up), reported 83.2" in found["message"]


def test_a_value_on_its_threshold_is_an_alert_for_greater_or_equal(offline) -> None:
    run = Run(task_for("2026-07-07", temperature_unit="C", conditions=["max_temp >= 33.3"]), offline, clock=T0)
    run.read()
    athens = next(row for row in run.daily["cities"] if row["city"] == "Athens")
    assert athens["values"]["max_temp"] == 33.3
    run.report(scripted_judge.plan(run.task, run.daily, ["boundary"])["calls"])
    found = run.verdict()
    assert failed(found)[0] == "alerts-complete" and "Athens" in found["message"]


def test_the_gate_failures_score_zero_and_the_others_score_f1(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    run.report(scripted_judge.plan(run.task, run.daily, ["missing_alert"])["calls"])
    found = run.verdict()
    alerts_count = len(scripted_judge.judge(run.task, run.daily)["alerts"])
    assert found["score"] == pytest.approx(2 * (alerts_count - 1) / (2 * alerts_count - 1))


# ----- The per-call checks -----


def test_verify_output_refuses_a_tampered_pin(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    pin = pinned(run)
    assert verifier.verify_output("pin_alerts", build_output(run.task, AlertInput), build_output(pin, PinOutput), str(run.workspace))["valid"]
    pin["candidates"][0]["cities"][0]["start"] = "2026-07-06T18:00"
    found = verifier.verify_output("pin_alerts", build_output(run.task, AlertInput), build_output(pin, PinOutput), str(run.workspace))
    assert not found["valid"] and "is not local midnight of 2026-07-07" in found["message"]
    pin = pinned(run)
    pin["candidates"][0]["cities"][0]["start"] = "2026-07-06T22:00"  # within the midnight-gap tolerance: the hash catches it
    found = verifier.verify_output("pin_alerts", build_output(run.task, AlertInput), build_output(pin, PinOutput), str(run.workspace))
    assert not found["valid"]


def test_verify_output_holds_get_daily_to_the_receipt(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    daily = run.read()
    given = build_output({"task": run.task, "cities": list(CITY_NAMES)}, DailyInput)
    assert verifier.verify_output("get_daily", given, build_output(daily, DailyOutput), str(run.workspace))["valid"]
    changed = copy.deepcopy(daily)
    changed["cities"][0]["values"]["max_temp"] += 0.000000001
    found = verifier.verify_output("get_daily", given, build_output(changed, DailyOutput), str(run.workspace))
    assert not found["valid"] and "not the rules' values" in found["message"]


def test_verify_input_refuses_unknown_and_repeated_cities_and_bad_conditions(offline) -> None:
    task = task_for("2026-07-07")
    assert not verifier.verify_input("get_daily", build_output({"task": task, "cities": ["Atlantis"]}, DailyInput))["valid"]
    assert not verifier.verify_input("get_daily", build_output({"task": task, "cities": ["Oslo", "Oslo"]}, DailyInput))["valid"]
    assert not verifier.verify_input("pin_alerts", build_output({**task, "conditions": ["max_temp => 9"]}, AlertInput))["valid"]
    assert not verifier.verify_input("pin_alerts", build_output({**task, "date": "١٩٤٠-٠١-٠١"}, AlertInput))["valid"]
    call = {"task": task, "examined": ["Oslo"], "alerts": [{"city": "Oslo", "values": {"max_temp": 1.0}, "matched": ["max_temp >= 95"]}], "unavailable": []}
    found = verifier.verify_input("report_alerts", build_output(call, ReportInput))
    assert not found["valid"] and "exactly the variables the conditions use" in found["message"]


# ----- The date check -----


def test_the_date_check_verdict_follows_its_clock(offline) -> None:
    given = build_output({"date": "today"}, DateInput)
    answer = alerts.check_date(given, Path("."))
    assert answer.today == "2026-10-16" and answer.date == "2026-10-16" and answer.kind.value == "forecast"
    found = verifier.verify_task("check-date", given, answer, None, ".")
    assert found["valid"] and found["score"] == 1.0
    wrong = plain_output(answer)
    wrong["forecast_last"] = "2026-10-31"
    found = verifier.verify_task("check-date", given, build_output(wrong, DateOutput), None, ".")
    assert not found["valid"] and found["score"] < 1 and "date range" in found["message"]


@pytest.mark.parametrize(
    ("date", "kind"),
    [("1939-12-31", None), ("1940-01-01", "archive"), ("2026-10-14", "archive"), ("2026-10-15", "forecast"), ("2026-10-29", "forecast"), ("2026-10-30", "outlook"), ("2027-04-30", "outlook"), ("2027-05-01", None)],
)
def test_the_date_check_names_each_kind_of_data(offline, date: str, kind: str | None) -> None:
    given = build_output({"date": date}, DateInput)
    answer = alerts.check_date(given, Path("."))
    assert (answer.kind.value if answer.kind else None) == kind
    assert verifier.verify_task("check-date", given, answer, None, ".")["valid"]
    if kind is None:
        assert "cannot be judged" in answer.message
        assert ("2027-04-30" if date > "2027" else "before 1940-01-01") in answer.source

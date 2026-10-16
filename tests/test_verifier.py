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
from plugin.rules import CITY_NAMES, key_of, series_sha256

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


def receipt_file(run: Run, date: str = "2026-07-07") -> Path:
    return run.folder / f"{date}.source.json"


def rewrite(path: Path, change) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_a_submission_naming_no_run_of_this_workspace_fails_this_run(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = run.gold()
    for token, words in (("0123456789abcdef", "no run 0123456789abcdef"), ("../../etc", "is not a run token"), ("", "is not a run token")):
        found = run.verdict({**report, "run": token})
        assert failed(found)[0] == "this-run" and words in found["message"] and found["score"] == 0.0


def test_a_report_left_by_an_earlier_task_fails_this_run(offline, monkeypatch) -> None:
    """An earlier task's complete, correct report in the folder is not this task's: the answer key saw its
    run, so submitting it (a replay) fails, while this task's own run passes."""
    task = task_for("2026-07-07")
    monkeypatch.setattr(alerts, "RUN", "aaaaaaaaaaaaaaaa")
    earlier = Run(task, offline, clock=T0)
    old_report = earlier.gold()
    assert earlier.verdict()["valid"]
    monkeypatch.setattr(alerts, "RUN", "bbbbbbbbbbbbbbbb")
    later = Run(task, offline, clock=T0, workspace=earlier.workspace)
    assert later.pin.prior_runs == ["aaaaaaaaaaaaaaaa"]
    found = later.verdict(old_report)
    assert failed(found)[0] == "this-run" and "began before this task" in found["message"] and found["score"] == 0.0
    later.gold()
    assert later.verdict()["valid"]


def test_a_receipt_naming_another_run_fails_agent_read_as_altered(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    rewrite(receipt_file(run), lambda receipt: receipt.update(run="0123456789abcdef"))
    found = run.verdict()
    assert failed(found)[0] == "agent-read" and "the receipt was altered after get_daily" in found["message"]


def test_a_receipt_changed_after_get_daily_fails_agent_read(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    rewrite(receipt_file(run), lambda receipt: receipt["cities"][0]["temperature_2m"].__setitem__(0, receipt["cities"][0]["temperature_2m"][0] + 1))
    found = run.verdict()
    assert failed(found)[0] == "agent-read" and "the receipt was altered after get_daily: its readings no longer match its own sha256" in found["message"]


def test_a_receipt_whose_request_is_not_its_sources_fails_agent_read(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    rewrite(receipt_file(run), lambda receipt: receipt.update(request=receipt["request"].replace("models=ecmwf_ifs", "models=best_match")))
    found = run.verdict()
    assert failed(found)[0] == "agent-read" and "its request is not the one that fetches" in found["message"]


def test_no_receipt_fails_agent_read(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    receipt_file(run).unlink()
    found = run.verdict()
    assert failed(found)[0] == "agent-read" and "never read" in found["message"]


def rehash(run: Run, date: str, change) -> None:
    """Edit the receipt's readings and fix every hash that names them, as a careful forger would."""
    path = receipt_file(run, date)
    receipt = json.loads(path.read_text())
    change(receipt)
    receipt["sha256"] = series_sha256(date, key_of(receipt["source"]), receipt["cities"])
    path.write_text(json.dumps(receipt))
    report = run.folder / f"{date}.json"
    rewrite(report, lambda saved: saved.update(sha256=receipt["sha256"]))


def test_a_rehashed_forecast_receipt_is_called_altered_without_run_again(offline) -> None:
    run = Run(task_for("2026-10-17"), offline, clock=T0)
    run.gold()
    rehash(run, "2026-10-17", lambda receipt: receipt["cities"][0]["temperature_2m"].__setitem__(3, receipt["cities"][0]["temperature_2m"][3] + 3.0))
    found = run.verdict()
    assert failed(found) == ["agent-data"], found["message"]
    assert "a run's data never changes, so the receipt was altered after get_daily" in found["message"]
    assert found["details"]["rerun"] is False and "upstream" not in found["message"]


def test_an_archive_receipt_claiming_a_fetch_before_the_answer_key_is_called_altered(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    def change(receipt):
        receipt["cities"][0]["temperature_2m"][3] = 9.0
        receipt["fetched_at"] = "2000-01-01T00:00:00Z"
    rehash(run, "2026-07-07", change)
    found = run.verdict()
    assert failed(found) == ["agent-data"] and "fetched before the answer key did: the receipt was altered" in found["message"]
    assert found["details"]["rerun"] is False


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
    assert "can be updated upstream between two fetches: run again" in found["message"] and "Athens temperature_2m" in found["message"]
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
    for run in ("2026-10-15T00:00", "2026-10-15T06:00", "2026-10-15T12:00"):
        offline.overrides[openmeteo.run_url(run, "2026-10-17")] = stand_in
    run = Run(task, offline, clock=clock, agent_after=timedelta(hours=6, minutes=5))
    assert [candidate.source.run for candidate in run.pin.candidates] == ["2026-10-15T00:00", "2026-10-15T06:00"]
    run.gold()
    assert run.daily["source"]["run"] == "2026-10-15T12:00"
    found = run.verdict()
    assert failed(found)[0] == "agent-source", found["message"]
    assert "more than the 10 min allowed" in found["message"] and "run again" in found["message"]
    assert found["details"]["rerun"] is True


def test_an_agent_that_could_not_get_the_newer_run_reads_the_older_with_its_own_message(offline) -> None:
    # Pinned at 15:05: runs 00 and 06 UTC. The agent, nine minutes later, reads 10 minutes behind (14:54):
    # the rules choose run 00. Here the agent's fetch of 00 fails (not published for it), so it reads 18 UTC
    # of the day before, which the answer key never pinned.
    clock = datetime.fromisoformat("2026-10-15T15:05:00+00:00")
    task = task_for("2026-10-16")
    stand_in = recorded(next(url for url in index()["answers"] if "run=2026-10-15T00:00" in url))["body"]
    newest = openmeteo.run_url("2026-10-15T00:00", "2026-10-16")
    offline.overrides[newest] = Turns(stand_in, {"status": 400, "reason": "The requested model run is not available."})
    offline.overrides[openmeteo.run_url("2026-10-14T18:00", "2026-10-16")] = stand_in
    run = Run(task, offline, clock=clock, agent_after=timedelta(minutes=1))
    run.gold()
    assert run.daily["source"]["run"] == "2026-10-14T18:00"
    found = run.verdict()
    assert failed(found)[0] == "agent-source" and "could not get run 2026-10-15T00:00" in found["message"]
    assert "older run 2026-10-14T18:00: run again" in found["message"] and found["details"]["rerun"] is True


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
    submitted = {"task": run.task, "run": run.daily["run"], "data": run.daily["source"], **judged}
    found = run.verdict(submitted)
    assert failed(found)[0] == "report-saved" and "no report" in found["message"]


def test_a_report_naming_another_run_fails_report_saved(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.gold()
    rewrite(run.folder / "2026-07-07.json", lambda report: report.update(run="fedcba9876543210"))
    found = run.verdict()
    assert failed(found)[0] == "report-saved" and "altered after report_alerts" in found["message"]


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
    assert failed(found) == ["values-exact"] and "Perth humidity 83.25 -> 83.3 (1 decimal, ties away from zero), reported 83.2" in found["message"]


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


# ----- The challenger's findings, each pinned by a test -----


@pytest.mark.parametrize("name", ["Atlantis", "mexico city", "Athens ", " Oslo", "Aardvark"])
@pytest.mark.parametrize("where", ["alerts", "unavailable"])
def test_a_city_outside_the_thirty_is_an_invalid_verdict_not_a_crash(offline, name: str, where: str) -> None:
    """WA-02: an unknown, miscased or space-padded city in the submission is a plain invalid verdict."""
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = copy.deepcopy(run.gold())
    if where == "alerts":
        report["alerts"].append({"city": name, "values": dict(report["alerts"][0]["values"]), "matched": list(report["alerts"][0]["matched"])})
    else:
        report["unavailable"].append({"city": name, "variables": ["max_temp"]})
    found = run.verdict(report)
    assert not found["valid"] and found["score"] == 0.0
    assert failed(found)[0] == "submitted-shape" and f"{name!r} is not one of the 30 cities" in found["message"]


def test_the_report_names_the_data_fields_that_differ(offline) -> None:
    """WA-07: names-data says which field differs, not the same label twice."""
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = run.gold()
    found = run.verdict({**report, "data": {**report["data"], "today": "2026-10-15"}})
    assert failed(found) == ["names-data"] and "today '2026-10-15' instead of '2026-10-16'" in found["message"]
    found = run.verdict({**report, "data": {**report["data"], "model": "era5"}})
    assert "model 'era5' instead of 'ecmwf_ifs'" in found["message"]


def test_a_missing_alert_shows_the_value_get_daily_gave_beside_its_report(offline) -> None:
    """WA-08: 'Athens has max_temp 91.94 -> report 91.9; it meets max_temp > 91.93', never the rounded value alone."""
    run = Run(task_for("2026-07-07", conditions=["max_temp > 91.93"]), offline, clock=T0)
    run.read()
    athens = next(row for row in run.daily["cities"] if row["city"] == "Athens")
    assert athens["values"]["max_temp"] == 91.94
    gold = scripted_judge.judge(run.task, run.daily)
    gold["alerts"] = [entry for entry in gold["alerts"] if entry["city"] != "Athens"]
    run.report(scripted_judge.halves(run.task, gold))
    found = run.verdict()
    assert "Missing alert Athens" in found["message"] and "max_temp 91.94 -> report 91.9; it meets max_temp > 91.93" in found["message"]


def test_negative_ties_round_away_from_zero(offline) -> None:
    """WA-09: a value of exactly -2.25 is reported -2.3 (ties away from zero); -2.2 fails values-exact."""
    task = task_for("2026-07-07", temperature_unit="C", conditions=["max_temp < 0"])
    url = recorded_url(offline, task)
    body = copy.deepcopy(recorded(url)["body"])
    hourly = body[0]["hourly"]  # Athens, every reading -2.25 °C
    hourly["temperature_2m"] = [-2.25] * len(hourly["temperature_2m"])
    offline.overrides[url] = body
    run = Run(task, offline, clock=T0)
    run.read()
    athens = next(row for row in run.daily["cities"] if row["city"] == "Athens")
    assert athens["values"]["max_temp"] == -2.25
    gold = scripted_judge.judge(task, run.daily)
    assert next(entry for entry in gold["alerts"] if entry["city"] == "Athens")["values"]["max_temp"] == -2.3
    run.report(scripted_judge.halves(task, gold))
    assert run.verdict()["valid"]
    wrong = copy.deepcopy(run.last["report"])
    for entry in wrong["alerts"]:
        if entry["city"] == "Athens":
            entry["values"]["max_temp"] = -2.2
    run2 = Run(task, offline, clock=T0)
    run2.read()
    run2.report(scripted_judge.halves(task, {"alerts": wrong["alerts"], "unavailable": wrong["unavailable"]}))
    found = run2.verdict()
    assert failed(found) == ["values-exact"] and "Athens max_temp -2.25 -> -2.3 (1 decimal, ties away from zero), reported -2.2" in found["message"]


def test_rounding_the_truncated_value_equals_rounding_the_exact_one_negatives_included() -> None:
    """WA-09: ties away from zero is symmetric, so truncating toward zero first never changes the report."""
    import random
    from fractions import Fraction

    from plugin.rules import round_half_up, truncate

    randomly = random.Random(9)
    for _ in range(20000):
        tie = Fraction(randomly.randint(-5000, 5000), 10) + Fraction(1, 20)
        value = tie + Fraction(randomly.randint(-10**4, 10**4), 10**13)  # within 1e-9 of a tie
        assert round_half_up(Fraction(truncate(value))) == round_half_up(value), value
    assert str(round_half_up(Fraction("-2.25"))) == "-2.3" and str(round_half_up(Fraction("2.25"))) == "2.3"


def test_a_submission_with_reordered_lists_is_still_the_saved_report(offline) -> None:
    """WA-10: matched conditions and unavailable variables are compared as sets, like the alert checks."""
    task = task_for("1940-01-01", temperature_unit="C", rain_unit="mm", conditions=["max_temp >= 25", "rain > 1", "humidity >= 0", "wind >= 0"])
    run = Run(task, offline, clock=T0)
    report = copy.deepcopy(run.gold())
    reordered = 0
    for entry in report["alerts"]:
        if len(entry["matched"]) > 1:
            entry["matched"].reverse()
            reordered += 1
    for entry in report["unavailable"]:
        if len(entry["variables"]) > 1:
            entry["variables"].reverse()
            reordered += 1
    assert reordered
    found = run.verdict(report)
    assert found["valid"], found["message"]


def test_no_submission_makes_the_verdict_raise(offline) -> None:
    """WA-02's audit: random malformed submissions and workspaces always give a verdict, never an exception."""
    import random

    run = Run(task_for("2026-07-07"), offline, clock=T0)
    report = run.gold()
    randomly = random.Random(3)
    names = [*CITY_NAMES[:5], "Atlantis", "", " ", "athens", "Athens\n"]
    for _ in range(300):
        broken = copy.deepcopy(report)
        choice = randomly.randrange(8)
        if choice == 0:
            broken["alerts"].append({"city": randomly.choice(names), "values": {"max_temp": randomly.choice([float("nan"), float("inf"), 1e308, -0.0])}, "matched": ["max_temp >= 95"]})
        elif choice == 1:
            broken["unavailable"].append({"city": randomly.choice(names), "variables": randomly.choice([[], ["x"], ["max_temp", "max_temp"]])})
        elif choice == 2 and broken["alerts"]:
            broken["alerts"][0]["matched"] = randomly.choice([[], ["nonsense"], ["max_temp >= 95", "max_temp>=95"], ["rain > 0.500"]])
        elif choice == 3:
            broken["run"] = randomly.choice(["", "../..", "x" * 16, "0" * 16, alerts.RUN.upper()])
        elif choice == 4:
            broken["data"] = {**broken["data"], randomly.choice(["kind", "run", "today", "label"]): randomly.choice(["", "outlook", "2026-13-01"])} if randomly.random() < 0.5 else broken["data"]
            if broken["data"]["kind"] not in ("archive", "forecast", "outlook"):
                broken["data"]["kind"] = "archive"
        elif choice == 5:
            broken["task"] = {**broken["task"], "conditions": randomly.choice([["max_temp >= 95"], ["bad"], ["max_temp >= 95", "max_temp >= 95.0"]])}
        elif choice == 6:
            broken["alerts"] = broken["alerts"] * 2
        else:
            broken["unavailable"] = [{"city": name, "variables": ["max_temp"]} for name in CITY_NAMES]
        found = run.verdict(broken)
        assert isinstance(found["valid"], bool) and 0.0 <= found["score"] <= 1.0

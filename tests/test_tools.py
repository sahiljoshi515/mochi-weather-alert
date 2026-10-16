"""The tools as the agent and the host call them: get_daily's values and receipt, report_alerts'
merging and refusals, the answer key, the date check, and the call log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import scripted_judge
from conftest import T0
from helpers import Run, task_for
from slimconfig import build_output, plain_output

from contract import AlertInput, DailyInput, DateInput, ReportInput
from plugin import alerts
from plugin.rules import CITY_NAMES, Units, day_metrics, given_values, parse_conditions


def test_get_daily_gives_each_needed_value_in_the_task_units_and_writes_a_receipt(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    daily = run.read()
    assert daily["source"]["label"] == "ECMWF IFS archive (analysis), hourly" and daily["source"]["today"] == "2026-10-16"
    assert [row["city"] for row in daily["cities"]] == list(CITY_NAMES)
    assert all(set(row) == {"city", "samples", "missing", "values"} for row in daily["cities"])
    assert all(set(row["values"]) == {"max_temp", "rain", "wind", "humidity"} and row["samples"] == 24 for row in daily["cities"])
    receipt = json.loads((run.workspace / "reports" / "2026-07-07.source.json").read_text())
    assert receipt["run"] == alerts.RUN and receipt["sha256"] == run.pin.candidates[0].sha256
    task = run.task
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    for row, series in zip(daily["cities"], receipt["cities"], strict=True):
        assert row["values"] == {name: float(value) for name, value in given_values(day_metrics(series, 1), conditions, units).items()}
    # about 1,300 tokens for four variables: 0.3.1's answer plus the source (about 200 characters)
    assert len(json.dumps(daily, separators=(",", ":"))) < 4500, "get_daily's answer stays small for the hosted model"


def test_get_daily_refuses_unknown_and_repeated_cities(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    with pytest.raises(ValueError, match="unknown city Atlantis"):
        alerts.get_daily(build_output({"task": run.task, "cities": ["Atlantis"]}, DailyInput), run.workspace)
    with pytest.raises(ValueError, match="distinct"):
        alerts.get_daily(build_output({"task": run.task, "cities": ["Oslo", "Oslo"]}, DailyInput), run.workspace)


def test_report_alerts_needs_the_data_read_first(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    call = {"task": run.task, "examined": ["Oslo"], "alerts": [], "unavailable": []}
    with pytest.raises(ValueError, match="call get_daily for 2026-07-07 first"):
        alerts.report_alerts(build_output(call, ReportInput), run.workspace)


def test_report_alerts_merges_halves_restates_cities_and_names_its_data(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    gold = scripted_judge.judge(run.task, run.daily)
    first, second = scripted_judge.halves(run.task, gold)
    out = run.report([first])
    assert out["examined"] == 15 and not out["complete"] and out["report"]["data"] == run.daily["source"]
    out = run.report([second])
    assert out["examined"] == 30 and out["complete"]
    saved = json.loads((run.workspace / "reports" / "2026-07-07.json").read_text())
    assert saved["sha256"] == run.pin.candidates[0].sha256 and saved["source"] == run.daily["source"]
    # restating a city replaces its entry: a call that examines an alert city with no entry removes its alert
    city = out["report"]["alerts"][0]["city"]
    out = run.report([{"task": run.task, "examined": [city], "alerts": [], "unavailable": []}])
    assert not any(entry["city"] == city for entry in out["report"]["alerts"]) and out["complete"]


def test_a_report_left_by_an_earlier_run_is_replaced_not_merged(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    (run.workspace / "reports" / "2026-07-07.json").write_text(json.dumps({"date": "2026-07-07", "run": "old", "examined": list(CITY_NAMES)}))
    first = scripted_judge.halves(run.task, scripted_judge.judge(run.task, run.daily))[0]
    assert run.report([first])["examined"] == 15


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"alerts": [{"city": "Oslo", "values": {"max_temp": 1.0}, "matched": ["max_temp >= 95"]}]}, "exactly the variables"),
        ({"alerts": [{"city": "Oslo", "values": {"max_temp": 1.0, "rain": 0.0, "wind": 1.0, "humidity": 1.0}, "matched": ["wind > 1"]}]}, "does not have"),
        ({"examined": ["Oslo"], "unavailable": [{"city": "Lima", "variables": ["rain"]}]}, "must be listed in examined"),
        ({"examined": ["Oslo", "Oslo"]}, "distinct"),
    ],
)
def test_report_alerts_refuses_malformed_judgements(offline, change: dict, words: str) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    call = {"task": run.task, "examined": ["Oslo"], "alerts": [], "unavailable": [], **change}
    with pytest.raises(ValueError, match=words):
        alerts.report_alerts(build_output(call, ReportInput), run.workspace)


def test_the_answer_key_refuses_a_date_too_far_ahead_naming_today(offline) -> None:
    with pytest.raises(ValueError, match=r"Today is 2026-10-16 \(UTC\)"):
        alerts.pin_alerts(build_output(task_for("2027-06-01"), AlertInput), Path("."))


def test_check_date_answers_today_from_the_clock(offline) -> None:
    answer = plain_output(alerts.check_date(build_output({"date": "today"}, DateInput), Path(".")))
    assert answer["today"] == answer["date"] == "2026-10-16" and answer["weekday"] == "Friday"
    assert answer["kind"] == "forecast" and "ECMWF IFS forecast issued 2026-10-15 06:00 UTC" in answer["source"]
    assert answer["forecast_last"] == "2026-10-29" and answer["outlook_last"] == "2027-04-30"


def test_every_call_leaves_one_line_in_the_call_log(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    alerts.log_call(run.workspace, "get_daily", {"task": run.task, "cities": list(CITY_NAMES)}, "ok", 0.5)
    alerts.log_call(run.workspace, "report_alerts", {"task": run.task, "examined": ["Oslo"], "alerts": [], "unavailable": []}, "error: x", 0.1)
    lines = [json.loads(line) for line in (run.workspace / "reports" / "calls.log").read_text().splitlines()]
    assert [line["tool"] for line in lines] == ["get_daily", "report_alerts"]
    assert lines[0]["cities"] == 30 and lines[1]["examined"] == 1 and lines[1]["result"] == "error: x"

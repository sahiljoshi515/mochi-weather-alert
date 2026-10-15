"""The verifier on contract instances, as NanoAgent's sandbox builds them from JSON: tool inputs and
outputs, and the task's verdict on a submitted report against a pinned reference, including every
adversarial row and every way the report protocol can be broken."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest
from slimconfig import build_output

import verifier
from contract import TOOLS, AlertInput, AlertReportOutput, PinOutput, Tool
from verifier.rules import truncate

DATE = "2026-09-06"
ARGUMENTS = {"date": DATE, "match": "any", "temperature_unit": "F", "wind_unit": "mph", "rain_unit": "in", "conditions": ["max_temp >= 95", "wind >= 25", "rain > 0.5"]}


def city(name, max_temp, min_temp, rain, wind, humidity_sum, *, hours=24, humidity_hours=None, missing=()):
    return {
        "city": name, "country": "Testland", "hours": hours, "max_temp_c": max_temp, "min_temp_c": min_temp, "rain_mm": rain,
        "max_wind_kmh": wind, "humidity_sum_pct": humidity_sum, "humidity_hours": hours if humidity_hours is None else humidity_hours,
        "missing": list(missing),
    }


# The pinned day: a boundary city with a rounding tie, a rounding trap, a city that misses a needed hour, and plain ones.
METRIC = [
    city("Boundary", 35.0, 20.0, 11.43, 10.0, 1710),  # 95.0 °F exactly: alert via max_temp >= 95; 0.45 in: 0.5 half up, 0.4 half even
    city("Breeze", 30.0, 18.0, 0.0, 40.2, 1200),  # 24.979 mph: reported 25.0, not >= 25
    city("Gale", 30.0, 18.0, 12.7, 48.28032, 1200),  # 30 mph exactly, 0.5 in exactly: wind only
    city("Soaked", 25.0, 15.0, 13.0, 5.0, 1722),  # 0.512 in > 0.5: alert via rain
    city("Gap", 40.0, 25.0, 0.0, 10.0, 1200, missing=["temperature_2m"]),  # unavailable for max_temp
    city("Calm", 20.0, 10.0, 0.0, 5.0, 1200),
]
EXPECTED_ALERTS = [
    {"city": "Boundary", "values": {"max_temp": 95.0, "rain": 0.5, "wind": 6.2}, "matched": ["max_temp >= 95"]},
    {"city": "Gale", "values": {"max_temp": 86.0, "rain": 0.5, "wind": 30.0}, "matched": ["wind >= 25"]},
    {"city": "Soaked", "values": {"max_temp": 77.0, "rain": 0.5, "wind": 3.1}, "matched": ["rain > 0.5"]},
]
EXPECTED_UNAVAILABLE = [{"city": "Gap", "variables": ["max_temp"]}]
EXAMINED = [row["city"] for row in METRIC]
RUN = "0123456789abcdef"
REFERENCE = {"date": DATE, "metric": METRIC, "alerts": EXPECTED_ALERTS, "unavailable": EXPECTED_UNAVAILABLE}
# What a perfect run submits, and the one report_alerts call that judges every city the same way.
OUTPUT = {"task": ARGUMENTS, "alerts": EXPECTED_ALERTS, "unavailable": EXPECTED_UNAVAILABLE}
CALL = {"task": ARGUMENTS, "examined": EXAMINED, "alerts": EXPECTED_ALERTS, "unavailable": EXPECTED_UNAVAILABLE}
REPORT_PATH = f"reports/{DATE}.json"


def file_of(output, *, examined=EXAMINED, run=RUN):
    """The report file a run that submitted ``output`` after examining ``examined`` leaves behind."""
    return {"date": output["task"]["date"], "run": run, "task": output["task"], "examined": list(examined), "alerts": output["alerts"], "unavailable": output["unavailable"]}


def verify_input(tool: str, value: dict) -> dict:
    return verifier.verify_input(tool, build_output(value, TOOLS[Tool(tool)][0]))


def verify_output(tool: str, value: dict, output: dict, workspace: Path) -> dict:
    given, made = TOOLS[Tool(tool)]
    return verifier.verify_output(tool, build_output(value, given), build_output(output, made), str(workspace))


def save(tmp_path: Path, report, *, date=DATE, marker=RUN) -> None:
    (tmp_path / "reports").mkdir(exist_ok=True)
    if marker is not None:
        (tmp_path / "reports" / "run.json").write_text(json.dumps({"run": marker}))
    if report is not None:
        (tmp_path / "reports" / f"{date}.json").write_text(report if isinstance(report, str) else json.dumps(report))


def verdict(tmp_path: Path, output, *, saved="same", arguments=ARGUMENTS, reference=REFERENCE, marker=RUN) -> dict:
    """The task's verdict on ``output``; the file is the one a run that submitted it leaves, unless given."""
    save(tmp_path, file_of(output) if saved == "same" else saved, date=arguments["date"], marker=marker)
    pinned = None if reference is None else build_output(reference, PinOutput)
    submitted = build_output(output, AlertReportOutput)
    return verifier.verify_task("daily-alert", build_output(arguments, AlertInput), submitted, pinned, str(tmp_path))


def changed(**changes):
    output = json.loads(json.dumps(OUTPUT))
    for key, value in changes.items():
        output[key] = value
    return output


def with_alert(name, **changes):
    output = json.loads(json.dumps(OUTPUT))
    for alert in output["alerts"]:
        if alert["city"] == name:
            alert.update(changes)
    return output


# ----- The task -----


def test_approves_the_exact_report_whatever_its_order(tmp_path: Path) -> None:
    shuffled = {"task": ARGUMENTS, "alerts": list(reversed(EXPECTED_ALERTS)), "unavailable": EXPECTED_UNAVAILABLE}
    shuffled["alerts"][0] = {**shuffled["alerts"][0], "values": {"wind": 3.1, "max_temp": 77, "rain": 0.5}}
    found = verdict(tmp_path, shuffled, saved=file_of(OUTPUT, examined=list(reversed(EXAMINED))))
    assert found["valid"] is True and found["score"] == 1.0
    assert found["details"]["expected"] == OUTPUT
    assert found["details"]["conditions"] == ["max_temp >= 95", "wind >= 25", "rain > 0.5"]
    assert found["details"]["units"] == {"temperature": "F", "wind": "mph", "rain": "in"}
    assert found["details"]["examined"] == 6 and found["details"]["unexamined"] == [] and found["details"]["unknown_examined"] == []
    assert "every city examined" in found["message"]


def test_accepts_the_conditions_in_any_spelling_that_means_the_same(tmp_path: Path) -> None:
    spelled = {**ARGUMENTS, "conditions": ["max_temp>=95.0", "wind >= 25", "rain > .5"]}
    output = {**OUTPUT, "task": spelled}
    assert verdict(tmp_path, output)["valid"] is True
    assert verdict(tmp_path, OUTPUT, saved=file_of(output))["valid"] is True


def test_recomputes_from_the_metric_rows_not_the_pins_own_list(tmp_path: Path) -> None:
    lying = {**REFERENCE, "alerts": [], "unavailable": []}
    assert verdict(tmp_path, OUTPUT, reference=lying)["valid"] is True
    assert verdict(tmp_path, changed(alerts=[], unavailable=[]), reference=lying)["valid"] is False


def test_approves_an_empty_report_when_nothing_qualifies(tmp_path: Path) -> None:
    arguments = {**ARGUMENTS, "conditions": ["max_temp >= 200"]}
    reference = {**REFERENCE, "metric": [row for row in METRIC if row["city"] != "Gap"]}
    examined = [name for name in EXAMINED if name != "Gap"]
    output = {"task": arguments, "alerts": [], "unavailable": []}
    found = verdict(tmp_path, output, saved=file_of(output, examined=examined), arguments=arguments, reference=reference)
    assert found["valid"] is True and found["score"] == 1.0


@pytest.mark.parametrize(
    ("name", "output", "message", "key", "score"),
    [
        ("wrong unit", with_alert("Boundary", values={"max_temp": 35.0, "rain": 11.4, "wind": 10.0}), "wrong value", "value_mismatches", 0.75),
        ("banker's rounding", with_alert("Boundary", values={"max_temp": 95.0, "rain": 0.4, "wind": 6.2}), "wrong value", "value_mismatches", 0.75),
        ("> instead of >=", changed(alerts=[a for a in EXPECTED_ALERTS if a["city"] != "Boundary"]), "alert(s) missing", "missing_alerts", 6 / 7),
        ("rounded value compared", changed(alerts=EXPECTED_ALERTS + [{"city": "Breeze", "values": {"max_temp": 86.0, "rain": 0.0, "wind": 25.0}, "matched": ["wind >= 25"]}]), "extra alert(s)", "extra_alerts", 8 / 9),
        ("missing hour as zero", changed(alerts=EXPECTED_ALERTS + [{"city": "Gap", "values": {"max_temp": 104.0, "rain": 0.0, "wind": 6.2}, "matched": ["max_temp >= 95"]}], unavailable=[]), "extra alert(s)", "extra_alerts", 0.75),
        ("any as all", changed(alerts=[]), "alert(s) missing", "missing_alerts", 0.4),
        ("extra city", changed(alerts=EXPECTED_ALERTS + [{"city": "Calm", "values": {"max_temp": 68.0, "rain": 0.0, "wind": 3.1}, "matched": ["max_temp >= 95"]}]), "extra alert(s)", "extra_alerts", 8 / 9),
        ("fabricated value", with_alert("Gale", values={"max_temp": 86.0, "rain": 0.5, "wind": 30.1}), "wrong value", "value_mismatches", 0.75),
        ("wrong matched rules", with_alert("Gale", matched=["wind >= 25", "rain > 0.5"]), "wrong matched conditions", "matched_mismatches", 0.75),
        ("unavailable forgotten", changed(unavailable=[]), "unavailable city(ies) missing", "missing_unavailable", 6 / 7),
        ("unavailable wrong variables", changed(unavailable=[{"city": "Gap", "variables": ["max_temp", "wind"]}]), "wrong variables", "unavailable_mismatches", 0.75),
        ("unavailable extra", changed(unavailable=EXPECTED_UNAVAILABLE + [{"city": "Calm", "variables": ["rain"]}]), "extra unavailable", "extra_unavailable", 8 / 9),
        ("empty report", changed(alerts=[], unavailable=[]), "alert(s) missing", "missing_alerts", 0.0),
    ],
)
def test_refuses_every_wrong_report_and_scores_it(tmp_path: Path, name: str, output: dict, message: str, key: str, score: float) -> None:
    found = verdict(tmp_path, output)
    assert found["valid"] is False, name
    assert message in found["message"], name
    assert found["details"][key], name
    assert found["score"] == pytest.approx(score), name
    assert found["details"]["expected"] == OUTPUT


@pytest.mark.parametrize(
    ("name", "output", "examined", "message", "key", "score"),
    [
        ("unknown city", changed(alerts=EXPECTED_ALERTS + [{"city": "Atlantis", "values": {"max_temp": 99.0, "rain": 0.0, "wind": 0.0}, "matched": ["max_temp >= 95"]}]), EXAMINED + ["Atlantis"], "extra alert(s)", "extra_alerts", 8 / 9 * 6 / 7),
        ("a city unexamined", OUTPUT, [name for name in EXAMINED if name != "Calm"], "1 city(ies) unexamined (Calm)", "unexamined", 5 / 6),
        ("unexamined and wrong", changed(alerts=[a for a in EXPECTED_ALERTS if a["city"] != "Boundary"]), [name for name in EXAMINED if name != "Boundary"], "unexamined", "missing_alerts", 6 / 7 * 5 / 6),
        ("unknown examined name", OUTPUT, EXAMINED + ["Atlantis"], "not in the snapshot", "unknown_examined", 6 / 7),
    ],
)
def test_every_city_must_be_examined(tmp_path: Path, name: str, output: dict, examined: list[str], message: str, key: str, score: float) -> None:
    found = verdict(tmp_path, output, saved=file_of(output, examined=examined))
    assert found["valid"] is False, name
    assert message in found["message"], name
    assert found["details"][key], name
    assert found["score"] == pytest.approx(score), name


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (with_alert("Gale", values={"max_temp": 86.0, "wind": 30.0}), "exactly the variables"),
        (with_alert("Gale", values={"max_temp": 86.0, "rain": 0.5, "wind": 30.0, "humidity": 50.0}), "exactly the variables"),
        (with_alert("Gale", matched=[]), "at least one condition"),
        (with_alert("Gale", matched=["humidity >= 1"]), "does not have"),
        (changed(unavailable=[{"city": "Gap", "variables": ["humidity"]}]), "variables must name"),
        (changed(alerts=EXPECTED_ALERTS + [EXPECTED_ALERTS[0]]), "once"),
    ],
)
def test_refuses_a_malformed_submission(tmp_path: Path, output: dict, message: str) -> None:
    found = verdict(tmp_path, output)
    assert found["valid"] is False and found["score"] == 0.0
    assert "malformed" in found["message"] and message in found["message"]


def test_refuses_a_submission_for_another_task_input(tmp_path: Path) -> None:
    found = verdict(tmp_path, {**OUTPUT, "task": {**ARGUMENTS, "match": "all"}})
    assert found["valid"] is False and found["score"] == 0.0 and "another task input" in found["message"]
    found = verdict(tmp_path, {**OUTPUT, "task": {**ARGUMENTS, "conditions": ["max_temp >= 95"]}}, saved=file_of(OUTPUT))
    assert found["valid"] is False and "another task input" in found["message"]


@pytest.mark.parametrize(
    ("saved", "message"),
    [
        (None, f"no report at {REPORT_PATH}: call report_alerts"),
        ("not json", "is not a report of this run: not JSON"),
        ({key: value for key, value in file_of(OUTPUT).items() if key != "task"}, "is not a report of this run: the report must hold date, run, task, examined, alerts and unavailable"),
        (file_of(OUTPUT, run="deadbeefdeadbeef"), "is not a report of this run: it was left by an earlier one"),
        ({**file_of(OUTPUT), "date": "2026-09-05"}, "is not a report of this run: the report's date is not its task's"),
        ({**file_of({**OUTPUT, "task": {**ARGUMENTS, "date": "2026-09-05"}}), "date": "2026-09-05"}, "the report is for another day"),
        (file_of({**OUTPUT, "task": {**ARGUMENTS, "temperature_unit": "C"}}), "the saved report is for another task input"),
        (file_of({**OUTPUT, "task": {**ARGUMENTS, "match": "all"}}), "is not a report of this run: Boundary: with match all"),
        ({**file_of(OUTPUT), "examined": EXAMINED + ["Gale"]}, "distinct city names"),
        ({**file_of(OUTPUT), "alerts": [{"city": "Gale", "values": {"wind": "30"}, "matched": []}], "unavailable": []}, "values must map"),
        (file_of(with_alert("Gale", values={"max_temp": 86.0, "rain": 0.5, "wind": 30.1})), "differs from the saved report"),
        (file_of(changed(unavailable=[])), "differs from the saved report"),
    ],
)
def test_refuses_a_missing_malformed_or_foreign_report_file(tmp_path: Path, saved, message: str) -> None:
    found = verdict(tmp_path, OUTPUT, saved=saved)
    assert found["valid"] is False and found["score"] == 0.0
    assert message in found["message"]
    assert found["details"]["expected"] == OUTPUT


def test_refuses_a_workspace_without_a_run_marker(tmp_path: Path) -> None:
    found = verdict(tmp_path, OUTPUT, marker=None)
    assert found["valid"] is False and found["score"] == 0.0 and "no run marker" in found["message"]


def test_refuses_a_pin_for_another_day_or_no_pin(tmp_path: Path) -> None:
    found = verdict(tmp_path, OUTPUT, arguments={**ARGUMENTS, "date": "2026-09-05"}, reference={**REFERENCE})
    assert found["valid"] is False and "another date" in found["message"]
    found = verdict(tmp_path, OUTPUT, reference=None)
    assert found["valid"] is False and "no pinned reference" in found["message"]


def test_is_deterministic(tmp_path: Path) -> None:
    output = with_alert("Gale", values={"max_temp": 86.0, "rain": 0.5, "wind": 30.1})
    assert verdict(tmp_path, output) == verdict(tmp_path, output)


# ----- Tool inputs and outputs -----


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"date": "2026-10-01"}, "outside the snapshot"),
        ({"date": "2026-09-31"}, "not a calendar day"),
        ({"date": "9/6/2026"}, "YYYY-MM-DD"),
        ({"conditions": ["max_temp >= 95", "max_temp>=95.0"]}, "given twice"),
        ({"conditions": ["max_temp => 95"]}, "not a condition"),
        ({"conditions": ["max_temp >= 95.123"]}, "not a condition"),
        ({"conditions": []}, "1 to 6"),
        ({"conditions": [f"wind > {n}" for n in range(7)]}, "1 to 6"),
    ],
)
def test_refuses_bad_task_input(change: dict, message: str) -> None:
    found = verify_input("pin_alerts", {**ARGUMENTS, **change})
    assert found["valid"] is False and message in found["message"]
    assert verify_input("pin_alerts", ARGUMENTS)["valid"] is True
    found = verify_input("report_alerts", {**CALL, "task": {**ARGUMENTS, **change}})
    assert found["valid"] is False and message in found["message"]


def test_checks_the_other_tool_inputs() -> None:
    names = [f"City {n}" for n in range(31)]
    assert verify_input("get_daily", {"task": ARGUMENTS, "cities": names})["valid"] is False
    assert verify_input("get_daily", {"task": ARGUMENTS, "cities": []})["valid"] is False
    assert verify_input("get_daily", {"task": ARGUMENTS, "cities": ["Athens", "Athens"]})["valid"] is False
    assert verify_input("get_daily", {"task": ARGUMENTS, "cities": [" "]})["valid"] is False
    assert verify_input("get_daily", {"task": {**ARGUMENTS, "date": "2026-10-02"}, "cities": names[:30]})["valid"] is False
    assert verify_input("get_daily", {"task": {**ARGUMENTS, "conditions": ["hot > 1"]}, "cities": names[:30]})["valid"] is False
    assert verify_input("get_daily", {"task": ARGUMENTS, "cities": names[:30]})["valid"] is True
    assert verify_input("list_cities", {"date": "2026-06-30"})["valid"] is False
    assert verify_input("list_cities", {"date": DATE})["valid"] is True
    assert verify_input("get_hourly", {"city": "", "date": DATE})["valid"] is False
    assert verify_input("get_hourly", {"city": "Athens", "date": DATE})["valid"] is True


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"alerts": CALL["alerts"] + [CALL["alerts"][0]]}, "once"),
        ({"alerts": [{**CALL["alerts"][0], "matched": ["hot"]}]}, "not a condition"),
        ({"alerts": [{**CALL["alerts"][0], "matched": ["humidity >= 1"]}]}, "does not have"),
        ({"alerts": [{**CALL["alerts"][0], "matched": []}]}, "at least one condition"),
        ({"alerts": [{**CALL["alerts"][0], "values": {"gust": 1.0}}]}, "values must map"),
        ({"alerts": [{**CALL["alerts"][0], "values": {"max_temp": 95.0, "rain": 0.5}}]}, "exactly the variables"),
        ({"alerts": [{**CALL["alerts"][0], "values": {"max_temp": 95.0, "rain": 0.5, "wind": 6.2, "humidity": 1.0}}]}, "exactly the variables"),
        ({"unavailable": [{"city": "Gap", "variables": ["humidity"]}]}, "variables must name"),
        ({"unavailable": [{"city": "Gap", "variables": []}]}, "variables must name"),
        ({"unavailable": [{"city": "Gap", "variables": ["max_temp", "max_temp"]}]}, "distinct"),
        ({"examined": [f"City {n}" for n in range(31)], "alerts": [], "unavailable": []}, "1 to 30"),
        ({"examined": [], "alerts": [], "unavailable": []}, "1 to 30"),
        ({"examined": ["Gale", "Gale"], "alerts": [], "unavailable": []}, "distinct city names"),
        ({"examined": ["Calm"]}, "must be listed in examined"),
        ({"task": {**ARGUMENTS, "match": "all"}}, "every condition"),
    ],
)
def test_checks_a_report_call(change: dict, message: str) -> None:
    assert verify_input("report_alerts", CALL)["valid"] is True
    found = verify_input("report_alerts", {**CALL, **change})
    assert found["valid"] is False and message in found["message"]


def test_a_report_call_under_match_all_lists_every_condition() -> None:
    everything = {"task": {**ARGUMENTS, "match": "all"}, "examined": ["Storm"], "alerts": [{"city": "Storm", "values": {"max_temp": 99.0, "rain": 0.7, "wind": 40.0}, "matched": ["max_temp >= 95", "wind >= 25", "rain > 0.5"]}], "unavailable": []}
    assert verify_input("report_alerts", everything)["valid"] is True


def test_checks_the_pin_against_its_own_aggregates(tmp_path: Path) -> None:
    assert verify_output("pin_alerts", ARGUMENTS, REFERENCE, tmp_path)["valid"] is True
    assert verify_output("pin_alerts", {**ARGUMENTS, "date": "2026-09-05"}, REFERENCE, tmp_path)["valid"] is False
    lying = {**REFERENCE, "alerts": EXPECTED_ALERTS[:1]}
    found = verify_output("pin_alerts", ARGUMENTS, lying, tmp_path)
    assert found["valid"] is False and "disagree" in found["message"]
    twisted = {**REFERENCE, "metric": [city("Odd", 10.0, 20.0, 0.0, 1.0, 1200)]}
    assert verify_output("pin_alerts", ARGUMENTS, twisted, tmp_path)["valid"] is False
    inconsistent = {**REFERENCE, "metric": METRIC + [city("Hole", None, None, 0.0, 1.0, 1200)]}
    assert verify_output("pin_alerts", ARGUMENTS, inconsistent, tmp_path)["valid"] is False


def test_checks_list_cities_and_get_daily_outputs(tmp_path: Path) -> None:
    listed = {"date": DATE, "cities": [{"city": "Athens", "country": "Greece", "timezone": "Europe/Athens", "hours_available": 24}, {"city": "Santiago", "country": "Chile", "timezone": "America/Santiago", "hours_available": 23}]}
    assert verify_output("list_cities", {"date": DATE}, listed, tmp_path)["valid"] is True
    assert verify_output("list_cities", {"date": "2026-09-05"}, listed, tmp_path)["valid"] is False
    short = {**listed, "cities": [{**listed["cities"][1], "hours_available": 22}]}
    assert verify_output("list_cities", {"date": DATE}, short, tmp_path)["valid"] is False
    values = {"max_temp": 96.26, "rain": 0.0, "wind": float(truncate(Fraction("9.2") / Fraction("1.609344")))}
    athens = {"city": "Athens", "hours": 24, "missing": [], "values": values}
    daily = {"date": DATE, "cities": [athens]}
    asked = {"task": ARGUMENTS, "cities": ["Athens"]}
    assert verify_output("get_daily", asked, daily, tmp_path)["valid"] is True
    assert verify_output("get_daily", {"task": ARGUMENTS, "cities": ["Santiago"]}, daily, tmp_path)["valid"] is False
    assert verify_output("get_daily", {"task": {**ARGUMENTS, "date": "2026-09-05"}, "cities": ["Athens"]}, daily, tmp_path)["valid"] is False
    assert verify_output("get_daily", asked, {**daily, "cities": [{**athens, "hours": 26}]}, tmp_path)["valid"] is False
    assert verify_output("get_daily", asked, {**daily, "cities": [{**athens, "missing": ["clouds"]}]}, tmp_path)["valid"] is False
    # the values are exactly the needed variables the day has every hour of, plausible, with at most 9 decimals
    for wrong in (
        {"max_temp": 96.26, "rain": 0.0},  # a needed variable missing
        {**values, "humidity": 31.458333333},  # a variable no condition uses
        {**values, "wind": 5.7166149246},  # 10 decimals
        {**values, "rain": -0.1},
        {**values, "max_temp": 200.0},
    ):
        found = verify_output("get_daily", asked, {**daily, "cities": [{**athens, "values": wrong}]}, tmp_path)
        assert found["valid"] is False, wrong
    # a variable with a missing hour is not given
    gap = {**athens, "missing": ["temperature_2m"], "values": {"rain": 0.0, "wind": values["wind"]}}
    assert verify_output("get_daily", asked, {**daily, "cities": [gap]}, tmp_path)["valid"] is True
    assert verify_output("get_daily", asked, {**daily, "cities": [{**gap, "values": values}]}, tmp_path)["valid"] is False


def test_checks_get_hourly_output(tmp_path: Path) -> None:
    rows = [{"time": f"2026-09-06T{hour:02d}:00-03:00", "temp_c": 10.0, "humidity_pct": 50.0, "rain_mm": 0.0, "wind_kmh": 1.0} for hour in range(1, 24)]
    hours = {"city": "Santiago", "date": DATE, "timezone": "America/Santiago", "rows": rows}
    assert verify_output("get_hourly", {"city": "Santiago", "date": DATE}, hours, tmp_path)["valid"] is True
    assert verify_output("get_hourly", {"city": "Lima", "date": DATE}, hours, tmp_path)["valid"] is False
    assert verify_output("get_hourly", {"city": "Santiago", "date": DATE}, {**hours, "rows": rows[:5]}, tmp_path)["valid"] is False
    stray = {**hours, "rows": rows[:-1] + [{**rows[-1], "time": "2026-09-07T00:00-03:00"}]}
    assert verify_output("get_hourly", {"city": "Santiago", "date": DATE}, stray, tmp_path)["valid"] is False


def test_checks_the_returned_report_and_the_saved_file(tmp_path: Path) -> None:
    answer = {"path": REPORT_PATH, "examined": 6, "complete": False, "report": OUTPUT}
    assert verify_output("report_alerts", CALL, answer, tmp_path)["valid"] is False  # nothing saved yet
    save(tmp_path, file_of(OUTPUT), marker=None)
    missing_marker = verify_output("report_alerts", CALL, answer, tmp_path)
    assert missing_marker["valid"] is False and "no run marker" in missing_marker["message"]
    save(tmp_path, file_of(OUTPUT))
    found = verify_output("report_alerts", CALL, answer, tmp_path)
    assert found["valid"] is True and found["details"] == {"examined": 6, "complete": False, "alerts": 3, "unavailable": 1}
    assert verify_output("report_alerts", CALL, {**answer, "examined": 30, "complete": True}, tmp_path)["valid"] is True
    assert verify_output("report_alerts", CALL, {**answer, "complete": True}, tmp_path)["valid"] is False  # 6 is not every city
    assert verify_output("report_alerts", CALL, {**answer, "examined": 30}, tmp_path)["valid"] is False  # 30 is every city
    assert verify_output("report_alerts", CALL, {**answer, "examined": 5}, tmp_path)["valid"] is False  # fewer than this call's cities
    assert verify_output("report_alerts", CALL, {**answer, "examined": 31, "complete": False}, tmp_path)["valid"] is False
    assert verify_output("report_alerts", CALL, {**answer, "path": "report.json"}, tmp_path)["valid"] is False
    # one chunk of the same run: the returned report holds its judgement, whatever else it holds
    chunk = {"task": ARGUMENTS, "examined": ["Gale", "Calm"], "alerts": [EXPECTED_ALERTS[1]], "unavailable": []}
    assert verify_output("report_alerts", chunk, {**answer, "examined": 2}, tmp_path)["valid"] is True
    assert verify_output("report_alerts", chunk, {**answer, "examined": 1}, tmp_path)["valid"] is False
    stale = {**answer, "report": changed(alerts=[a for a in EXPECTED_ALERTS if a["city"] != "Gale"])}
    found = verify_output("report_alerts", chunk, stale, tmp_path)
    assert found["valid"] is False and "judgement of Gale" in found["message"]
    judged = {"task": ARGUMENTS, "examined": ["Gale"], "alerts": [], "unavailable": []}
    found = verify_output("report_alerts", judged, answer, tmp_path)
    assert found["valid"] is False and "judgement of Gale" in found["message"]  # the returned report still alerts Gale
    # a returned report for another task, or malformed under this one
    foreign = {**answer, "report": {**OUTPUT, "task": {**ARGUMENTS, "match": "all"}}}
    found = verify_output("report_alerts", chunk, foreign, tmp_path)
    assert found["valid"] is False and "another task input" in found["message"]
    broken = {**answer, "report": with_alert("Gale", matched=[])}
    found = verify_output("report_alerts", chunk, broken, tmp_path)
    assert found["valid"] is False and "malformed" in found["message"]
    # a call whose examined cities the file never recorded
    elsewhere = {"task": ARGUMENTS, "examined": ["Nowhere"], "alerts": [], "unavailable": []}
    assert verify_output("report_alerts", elsewhere, answer, tmp_path)["valid"] is False
    # a file for another task, or another run
    save(tmp_path, file_of({**OUTPUT, "task": {**ARGUMENTS, "temperature_unit": "C"}}))
    found = verify_output("report_alerts", CALL, answer, tmp_path)
    assert found["valid"] is False and "another task input" in found["message"]
    save(tmp_path, file_of({**OUTPUT, "task": {**ARGUMENTS, "conditions": ["max_temp >= 95"]}}))
    found = verify_output("report_alerts", CALL, answer, tmp_path)
    assert found["valid"] is False and "not a report of this run" in found["message"]  # malformed under its own task
    save(tmp_path, file_of(OUTPUT, run="deadbeefdeadbeef"))
    foreign = verify_output("report_alerts", CALL, answer, tmp_path)
    assert foreign["valid"] is False and "not a report of this run" in foreign["message"]

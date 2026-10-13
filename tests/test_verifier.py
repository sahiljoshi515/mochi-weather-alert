"""The verifier on contract instances, as NanoAgent's sandbox builds them from JSON: tool inputs and
outputs, and the task's outcome against a pinned reference, including every adversarial row."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from slimconfig import build_output

import verifier
from contract import TOOLS, AlertInput, PinOutput, Tool

DATE = "2026-09-06"
ARGUMENTS = {"date": DATE, "match": "any", "temperature_unit": "F", "wind_unit": "mph", "rain_unit": "in", "conditions": ["max_temp >= 95", "wind >= 25", "rain > 0.5"]}


def city(name, max_temp, min_temp, rain, wind, humidity_sum, *, hours=24, humidity_hours=None, missing=()):
    return {
        "city": name, "country": "Testland", "hours": hours, "max_temp_c": max_temp, "min_temp_c": min_temp, "rain_mm": rain,
        "max_wind_kmh": wind, "humidity_sum_pct": humidity_sum, "humidity_hours": hours if humidity_hours is None else humidity_hours,
        "missing": list(missing),
    }


# The pinned day: a boundary city, a rounding trap, a city that misses a needed hour, and plain ones.
METRIC = [
    city("Boundary", 35.0, 20.0, 0.0, 10.0, 1710),  # 95.0 °F exactly: alert via max_temp >= 95
    city("Breeze", 30.0, 18.0, 0.0, 40.2, 1200),  # 24.979 mph: reported 25.0, not >= 25
    city("Gale", 30.0, 18.0, 12.7, 48.28032, 1200),  # 30 mph exactly, 0.5 in exactly: wind only
    city("Soaked", 25.0, 15.0, 13.0, 5.0, 1722),  # 0.512 in > 0.5: alert via rain
    city("Gap", 40.0, 25.0, 0.0, 10.0, 1200, missing=["temperature_2m"]),  # unavailable for max_temp
    city("Calm", 20.0, 10.0, 0.0, 5.0, 1200),
]
EXPECTED_ALERTS = [
    {"city": "Boundary", "values": {"max_temp": 95.0, "rain": 0.0, "wind": 6.2}, "matched": ["max_temp >= 95"]},
    {"city": "Gale", "values": {"max_temp": 86.0, "rain": 0.5, "wind": 30.0}, "matched": ["wind >= 25"]},
    {"city": "Soaked", "values": {"max_temp": 77.0, "rain": 0.5, "wind": 3.1}, "matched": ["rain > 0.5"]},
]
EXPECTED_UNAVAILABLE = [{"city": "Gap", "variables": ["max_temp"]}]
EXAMINED = [row["city"] for row in METRIC]
RUN = "0123456789abcdef"
REFERENCE = {"date": DATE, "metric": METRIC, "alerts": EXPECTED_ALERTS, "unavailable": EXPECTED_UNAVAILABLE}
GOLD = {"date": DATE, "run": RUN, "examined": EXAMINED, "alerts": EXPECTED_ALERTS, "unavailable": EXPECTED_UNAVAILABLE}
CALL = {key: value for key, value in GOLD.items() if key != "run"}  # the same judgement as one report_alerts call


def verify_input(tool: str, value: dict) -> dict:
    return verifier.verify_input(tool, build_output(value, TOOLS[Tool(tool)][0]))


def verify_output(tool: str, value: dict, output: dict, workspace: Path) -> dict:
    given, made = TOOLS[Tool(tool)]
    return verifier.verify_output(tool, build_output(value, given), build_output(output, made), str(workspace))


def verdict(tmp_path: Path, report, *, arguments=ARGUMENTS, reference=REFERENCE, marker=RUN) -> dict:
    (tmp_path / "weather-alert").mkdir(exist_ok=True)
    if marker is not None:
        (tmp_path / "weather-alert" / "run.json").write_text(json.dumps({"run": marker}))
    if report is not None:
        (tmp_path / "weather-alert" / f"{arguments['date']}.json").write_text(report if isinstance(report, str) else json.dumps(report))
    pinned = None if reference is None else build_output(reference, PinOutput)
    return verifier.verify_task("daily-alert", build_output(arguments, AlertInput), pinned, str(tmp_path))


def changed(**changes):
    report = json.loads(json.dumps(GOLD))
    for key, value in changes.items():
        report[key] = value
    return report


def with_alert(name, **changes):
    report = json.loads(json.dumps(GOLD))
    for alert in report["alerts"]:
        if alert["city"] == name:
            alert.update(changes)
    return report


# ----- The task -----


def test_approves_the_exact_report_whatever_its_order(tmp_path: Path) -> None:
    shuffled = {"date": DATE, "run": RUN, "examined": list(reversed(EXAMINED)), "alerts": list(reversed(EXPECTED_ALERTS)), "unavailable": EXPECTED_UNAVAILABLE}
    shuffled["alerts"][0] = {**shuffled["alerts"][0], "values": {"wind": 3.1, "max_temp": 77, "rain": 0.5}}
    found = verdict(tmp_path, shuffled)
    assert found["valid"] is True and found["score"] == 1.0
    assert found["details"]["expected"] == CALL  # the expected report carries no run marker
    assert found["details"]["conditions"] == ["max_temp >= 95", "wind >= 25", "rain > 0.5"]
    assert found["details"]["units"] == {"temperature": "F", "wind": "mph", "rain": "in"}
    assert found["details"]["examined"] == 6 and found["details"]["unexamined"] == [] and found["details"]["unknown_examined"] == []
    assert "every city examined" in found["message"]


def test_recomputes_from_the_metric_rows_not_the_pins_own_list(tmp_path: Path) -> None:
    lying = {**REFERENCE, "alerts": [], "unavailable": []}
    assert verdict(tmp_path, GOLD, reference=lying)["valid"] is True
    assert verdict(tmp_path, {"date": DATE, "run": RUN, "examined": EXAMINED, "alerts": [], "unavailable": []}, reference=lying)["valid"] is False


def test_approves_an_empty_report_when_nothing_qualifies(tmp_path: Path) -> None:
    arguments = {**ARGUMENTS, "conditions": ["max_temp >= 200"]}
    reference = {**REFERENCE, "metric": [row for row in METRIC if row["city"] != "Gap"]}
    examined = [city for city in EXAMINED if city != "Gap"]
    found = verdict(tmp_path, {"date": DATE, "run": RUN, "examined": examined, "alerts": [], "unavailable": []}, arguments=arguments, reference=reference)
    assert found["valid"] is True and found["score"] == 1.0


@pytest.mark.parametrize(
    ("name", "report", "message", "key", "score"),
    [
        ("wrong unit", with_alert("Boundary", values={"max_temp": 35.0, "rain": 0.0, "wind": 10.0}), "wrong value", "value_mismatches", 0.75),
        ("banker's rounding", with_alert("Boundary", values={"max_temp": 95.0, "rain": 0.0, "wind": 6.2, "humidity": 71.2}), "wrong value", "value_mismatches", 0.75),
        ("> instead of >=", changed(alerts=[a for a in EXPECTED_ALERTS if a["city"] != "Boundary"]), "alert(s) missing", "missing_alerts", 6 / 7),
        ("rounded value compared", changed(alerts=EXPECTED_ALERTS + [{"city": "Breeze", "values": {"max_temp": 86.0, "rain": 0.0, "wind": 25.0}, "matched": ["wind >= 25"]}]), "extra alert(s)", "extra_alerts", 8 / 9),
        ("missing hour as zero", changed(alerts=EXPECTED_ALERTS + [{"city": "Gap", "values": {"max_temp": 104.0, "rain": 0.0, "wind": 6.2}, "matched": ["max_temp >= 95"]}], unavailable=[]), "extra alert(s)", "extra_alerts", 0.75),
        ("any as all", changed(alerts=[]), "alert(s) missing", "missing_alerts", 0.4),
        ("extra city", changed(alerts=EXPECTED_ALERTS + [{"city": "Calm", "values": {"max_temp": 68.0, "rain": 0.0, "wind": 3.1}, "matched": ["max_temp >= 95"]}]), "extra alert(s)", "extra_alerts", 8 / 9),
        ("fabricated value", with_alert("Gale", values={"max_temp": 86.0, "rain": 0.5, "wind": 30.1}), "wrong value", "value_mismatches", 0.75),
        ("wrong matched rules", with_alert("Gale", matched=["wind >= 25", "rain > 0.5"]), "wrong matched conditions", "matched_mismatches", 0.75),
        ("missing variable", with_alert("Gale", values={"max_temp": 86.0, "wind": 30.0}), "wrong value", "value_mismatches", 0.75),
        ("extra variable", with_alert("Gale", values={"max_temp": 86.0, "rain": 0.5, "wind": 30.0, "humidity": 50.0}), "wrong value", "value_mismatches", 0.75),
        ("unavailable forgotten", changed(unavailable=[]), "unavailable city(ies) missing", "missing_unavailable", 6 / 7),
        ("unavailable wrong variables", changed(unavailable=[{"city": "Gap", "variables": ["max_temp", "wind"]}]), "wrong variables", "unavailable_mismatches", 0.75),
        ("unavailable extra", changed(unavailable=EXPECTED_UNAVAILABLE + [{"city": "Calm", "variables": ["rain"]}]), "extra unavailable", "extra_unavailable", 8 / 9),
        ("empty report", changed(alerts=[], unavailable=[]), "alert(s) missing", "missing_alerts", 0.0),
        ("unknown city", changed(examined=EXAMINED + ["Atlantis"], alerts=EXPECTED_ALERTS + [{"city": "Atlantis", "values": {"max_temp": 99.0, "rain": 0.0, "wind": 0.0}, "matched": ["max_temp >= 95"]}]), "extra alert(s)", "extra_alerts", 8 / 9 * 6 / 7),
        ("a city unexamined", changed(examined=[city for city in EXAMINED if city != "Calm"]), "1 city(ies) unexamined (Calm)", "unexamined", 5 / 6),
        ("unexamined and wrong", changed(examined=[city for city in EXAMINED if city != "Boundary"], alerts=[a for a in EXPECTED_ALERTS if a["city"] != "Boundary"]), "unexamined", "missing_alerts", 6 / 7 * 5 / 6),
        ("unknown examined name", changed(examined=EXAMINED + ["Atlantis"]), "not in the snapshot", "unknown_examined", 6 / 7),
    ],
)
def test_refuses_every_wrong_report_and_scores_it(tmp_path: Path, name: str, report: dict, message: str, key: str, score: float) -> None:
    found = verdict(tmp_path, report)
    assert found["valid"] is False, name
    assert message in found["message"], name
    assert found["details"][key], name
    assert found["score"] == pytest.approx(score), name
    assert found["details"]["expected"] == CALL  # the expected report carries no run marker


@pytest.mark.parametrize(
    ("report", "message"),
    [
        (None, "no report at weather-alert/2026-09-06.json"),
        ("not json", "is not a report of this run: not JSON"),
        ({"date": DATE, "examined": EXAMINED, "alerts": EXPECTED_ALERTS, "unavailable": EXPECTED_UNAVAILABLE}, "is not a report of this run: the report must hold date, run, examined, alerts and unavailable"),
        ({**GOLD, "run": "deadbeefdeadbeef"}, "is not a report of this run: it was left by an earlier one"),
        ({**GOLD, "date": "2026-09-05"}, "for another day"),
        ({**GOLD, "examined": EXAMINED + ["Gale"]}, "distinct city names"),
        ({**GOLD, "examined": ["Gale"], "alerts": [{"city": "Gap", "values": {}, "matched": []}], "unavailable": []}, "must be listed in examined"),
        ({**GOLD, "alerts": [{"city": "Gale", "values": {"wind": "30"}, "matched": []}], "unavailable": []}, "values must map"),
        ({**GOLD, "alerts": [{"city": "Gale", "values": {"gust": 30.0}, "matched": []}], "unavailable": []}, "values must map"),
        ({**GOLD, "alerts": [{"city": "Gale", "values": {}, "matched": ["wind >>= 25"]}], "unavailable": []}, "matched"),
        ({**GOLD, "alerts": [{"city": "Gale", "values": {}, "matched": ["wind >= 25", "wind >= 25.0"]}], "unavailable": []}, "listed twice"),
        ({**GOLD, "alerts": [{"city": "Gale", "values": {}, "matched": []}, {"city": "Gale", "values": {}, "matched": []}], "unavailable": []}, "once"),
        ({**GOLD, "alerts": [], "unavailable": [{"city": "Gap", "variables": []}]}, "variables must name"),
        ({**GOLD, "alerts": [], "unavailable": [{"city": "Gap", "variables": ["max_temp", "max_temp"]}]}, "distinct"),
    ],
)
def test_refuses_a_missing_or_malformed_report(tmp_path: Path, report, message: str) -> None:
    found = verdict(tmp_path, report)
    assert found["valid"] is False and found["score"] == 0.0
    assert message in found["message"]
    assert found["details"]["expected"] == CALL  # the expected report carries no run marker


def test_refuses_a_workspace_without_a_run_marker(tmp_path: Path) -> None:
    found = verdict(tmp_path, GOLD, marker=None)
    assert found["valid"] is False and found["score"] == 0.0 and "no run marker" in found["message"]


def test_refuses_a_pin_for_another_day_or_no_pin(tmp_path: Path) -> None:
    found = verdict(tmp_path, GOLD, arguments={**ARGUMENTS, "date": "2026-09-05"}, reference={**REFERENCE})
    assert found["valid"] is False and "another date" in found["message"]
    found = verdict(tmp_path, GOLD, reference=None)
    assert found["valid"] is False and "no pinned reference" in found["message"]


def test_is_deterministic(tmp_path: Path) -> None:
    report = with_alert("Gale", values={"max_temp": 86.0, "rain": 0.5, "wind": 30.1})
    assert verdict(tmp_path, report) == verdict(tmp_path, report)


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


def test_checks_the_other_tool_inputs() -> None:
    names = [f"City {n}" for n in range(31)]
    assert verify_input("get_daily", {"cities": names, "date": DATE})["valid"] is False
    assert verify_input("get_daily", {"cities": [], "date": DATE})["valid"] is False
    assert verify_input("get_daily", {"cities": ["Athens", "Athens"], "date": DATE})["valid"] is False
    assert verify_input("get_daily", {"cities": [" "], "date": DATE})["valid"] is False
    assert verify_input("get_daily", {"cities": names[:30], "date": "2026-10-02"})["valid"] is False
    assert verify_input("get_daily", {"cities": names[:30], "date": DATE})["valid"] is True
    assert verify_input("list_cities", {"date": "2026-06-30"})["valid"] is False
    assert verify_input("list_cities", {"date": DATE})["valid"] is True
    assert verify_input("get_hourly", {"city": "", "date": DATE})["valid"] is False
    assert verify_input("get_hourly", {"city": "Athens", "date": DATE})["valid"] is True
    assert verify_input("report_alerts", CALL)["valid"] is True
    assert verify_input("report_alerts", {**CALL, "alerts": CALL["alerts"] + [CALL["alerts"][0]]})["valid"] is False
    assert verify_input("report_alerts", {**CALL, "date": "2027-01-01"})["valid"] is False
    bad_matched = [{**CALL["alerts"][0], "matched": ["hot"]}]
    assert verify_input("report_alerts", {**CALL, "alerts": bad_matched})["valid"] is False
    bad_values = [{**CALL["alerts"][0], "values": {"gust": 1.0}}]
    assert verify_input("report_alerts", {**CALL, "alerts": bad_values})["valid"] is False
    crowd = [f"City {n}" for n in range(31)]
    found = verify_input("report_alerts", {"date": DATE, "examined": crowd, "alerts": [], "unavailable": []})
    assert found["valid"] is False and "1 to 30" in found["message"]
    assert verify_input("report_alerts", {"date": DATE, "examined": crowd[:30], "alerts": [], "unavailable": []})["valid"] is True
    assert verify_input("report_alerts", {"date": DATE, "examined": [], "alerts": [], "unavailable": []})["valid"] is False
    assert verify_input("report_alerts", {"date": DATE, "examined": ["Gale", "Gale"], "alerts": [], "unavailable": []})["valid"] is False
    outside = verify_input("report_alerts", {**CALL, "examined": ["Calm"]})
    assert outside["valid"] is False and "must be listed in examined" in outside["message"]


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
    daily = {"date": DATE, "cities": [{"city": "Athens", "country": "Greece", "hours": 24, "max_temp_c": 35.7, "min_temp_c": 23.1, "rain_mm": 0.0, "max_wind_kmh": 9.2, "mean_humidity_pct": 31.4583, "missing": []}]}
    assert verify_output("get_daily", {"cities": ["Athens"], "date": DATE}, daily, tmp_path)["valid"] is True
    assert verify_output("get_daily", {"cities": ["Santiago"], "date": DATE}, daily, tmp_path)["valid"] is False
    upside_down = {**daily, "cities": [{**daily["cities"][0], "min_temp_c": 40.0}]}
    assert verify_output("get_daily", {"cities": ["Athens"], "date": DATE}, upside_down, tmp_path)["valid"] is False
    unmarked = {**daily, "cities": [{**daily["cities"][0], "rain_mm": None}]}
    assert verify_output("get_daily", {"cities": ["Athens"], "date": DATE}, unmarked, tmp_path)["valid"] is False


def test_checks_get_hourly_output(tmp_path: Path) -> None:
    rows = [{"time": f"2026-09-06T{hour:02d}:00-03:00", "temp_c": 10.0, "humidity_pct": 50.0, "rain_mm": 0.0, "wind_kmh": 1.0} for hour in range(1, 24)]
    hours = {"city": "Santiago", "date": DATE, "timezone": "America/Santiago", "rows": rows}
    assert verify_output("get_hourly", {"city": "Santiago", "date": DATE}, hours, tmp_path)["valid"] is True
    assert verify_output("get_hourly", {"city": "Lima", "date": DATE}, hours, tmp_path)["valid"] is False
    assert verify_output("get_hourly", {"city": "Santiago", "date": DATE}, {**hours, "rows": rows[:5]}, tmp_path)["valid"] is False
    stray = {**hours, "rows": rows[:-1] + [{**rows[-1], "time": "2026-09-07T00:00-03:00"}]}
    assert verify_output("get_hourly", {"city": "Santiago", "date": DATE}, stray, tmp_path)["valid"] is False


def test_checks_the_saved_report(tmp_path: Path) -> None:
    counts = {"path": f"weather-alert/{DATE}.json", "alerts": 3, "unavailable": 1, "examined": 6}
    assert verify_output("report_alerts", CALL, counts, tmp_path)["valid"] is False  # nothing saved yet
    (tmp_path / "weather-alert").mkdir()
    (tmp_path / "weather-alert" / f"{DATE}.json").write_text(json.dumps(GOLD))
    missing_marker = verify_output("report_alerts", CALL, counts, tmp_path)
    assert missing_marker["valid"] is False and "no run marker" in missing_marker["message"]
    (tmp_path / "weather-alert" / "run.json").write_text(json.dumps({"run": RUN}))
    assert verify_output("report_alerts", CALL, counts, tmp_path)["valid"] is True
    # one chunk of the same file: its judgement is in the file, and the counts are the chunk's own
    chunk = {"date": DATE, "examined": ["Gale", "Calm"], "alerts": [EXPECTED_ALERTS[1]], "unavailable": []}
    assert verify_output("report_alerts", chunk, {**counts, "alerts": 1, "unavailable": 0, "examined": 2}, tmp_path)["valid"] is True
    assert verify_output("report_alerts", chunk, counts, tmp_path)["valid"] is False  # cumulative totals are not the call's
    assert verify_output("report_alerts", chunk, {**counts, "alerts": 1, "unavailable": 0, "examined": 2, "path": "report.json"}, tmp_path)["valid"] is False
    # a call whose judgement the file does not hold: an examined city still has a stale entry
    stale = {"date": DATE, "examined": ["Gale"], "alerts": [], "unavailable": []}
    found = verify_output("report_alerts", stale, {**counts, "alerts": 0, "unavailable": 0, "examined": 1}, tmp_path)
    assert found["valid"] is False and "judgement of Gale" in found["message"]
    # a call whose examined cities the file never recorded
    elsewhere = {"date": DATE, "examined": ["Nowhere"], "alerts": [], "unavailable": []}
    assert verify_output("report_alerts", elsewhere, {**counts, "alerts": 0, "unavailable": 0, "examined": 1}, tmp_path)["valid"] is False
    # a file another run left behind
    (tmp_path / "weather-alert" / f"{DATE}.json").write_text(json.dumps({**GOLD, "run": "deadbeefdeadbeef"}))
    foreign = verify_output("report_alerts", CALL, counts, tmp_path)
    assert foreign["valid"] is False and "not a report of this run" in foreign["message"]

"""report_alerts: a well-formed call at input, the merged report at output, and the comparison of a
judgement with the expected alerts that verify_task scores."""

import json
from decimal import Decimal
from pathlib import Path

from .checks import result
from .rules import (
    CITY_COUNT,
    MAX_REPORT_ENTRIES,
    canonical_conditions,
    canonical_task,
    judgement_problem,
    plain_task,
    report_problem,
    task_problem,
)

# The report lives in the run's folder, which is the member's own folder for this plugin.
REPORT_DIR = "reports"
RUN_MARKER = "run.json"
NO_MARKER = "no run marker in the workspace: the plugin did not start here"


def report_path(date):
    return f"{REPORT_DIR}/{date}.json"


def report_file(workspace, date):
    return Path(workspace) / REPORT_DIR / f"{date}.json"


def run_of(workspace):
    """The run marker the plugin process wrote at its start (reports/run.json), or None."""
    path = Path(workspace) / REPORT_DIR / RUN_MARKER
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    run = found.get("run") if isinstance(found, dict) else None
    return run if isinstance(run, str) and run.strip() else None


def saved_report(workspace, date):
    """(the report file as JSON, None) or (None, why it is not a report of this run)."""
    path = report_file(workspace, date)
    where = report_path(date)
    if not path.is_file():
        return None, f"no report at {where}: call report_alerts for every city before submitting"
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None, f"{where} is not a report of this run: not JSON"
    problem = report_problem(found)
    if problem:
        return None, f"{where} is not a report of this run: {problem}"
    run = run_of(workspace)
    if run is None:
        return None, NO_MARKER
    if found["run"] != run:
        return None, f"{where} is not a report of this run: it was left by an earlier one"
    if found["date"] != date:
        return None, "the report is for another day"
    return found, None


def entries_of(value):
    """The alerts and unavailable cities of a contract ReportInput, AlertReportOutput or PinOutput, as JSON."""
    return {
        "alerts": [{"city": a.city, "values": dict(a.values), "matched": list(a.matched)} for a in value.alerts],
        "unavailable": [{"city": u.city, "variables": list(u.variables)} for u in value.unavailable],
    }


def entries_by_city(judgement):
    return {entry["city"]: entry for entry in judgement["alerts"]}, {entry["city"]: entry for entry in judgement["unavailable"]}


def verify_input(value):
    task = plain_task(value.task)
    problem = task_problem(task)
    if problem:
        return result(False, problem)
    if not isinstance(value.examined, list) or not 1 <= len(value.examined) <= MAX_REPORT_ENTRIES:
        return result(False, f"examined must name 1 to {MAX_REPORT_ENTRIES} distinct cities")
    entries = entries_of(value)
    problem = judgement_problem(task, list(value.examined), entries["alerts"], entries["unavailable"])
    if problem:
        return result(False, problem)
    return result(True, "input is valid")


def verify_output(input_value, output_value, workspace):
    """The file is a report of this run for this task and records this call's examined cities, and the
    returned report holds this call's judgement of every city it examined. Nothing cumulative is
    checked against the file: other calls of the same turn may have merged more since."""
    task = plain_task(input_value.task)
    date = task["date"]
    if output_value.path != report_path(date) or not report_file(workspace, date).is_file():
        return result(False, f"the report is not at {report_path(date)}")
    saved, problem = saved_report(workspace, date)
    if problem:
        return result(False, problem)
    if canonical_task(saved["task"]) != canonical_task(task):
        return result(False, "the saved report is for another task input")
    if not set(input_value.examined) <= set(saved["examined"]):
        return result(False, "the saved report does not record this call's examined cities")
    if canonical_task(plain_task(output_value.report.task)) != canonical_task(task):
        return result(False, "the returned report is for another task input")
    returned = entries_of(output_value.report)
    problem = judgement_problem(task, None, returned["alerts"], returned["unavailable"])
    if problem:
        return result(False, f"the returned report is malformed: {problem}")
    returned_alerts, returned_unavailable = entries_by_city(returned)
    given_alerts, given_unavailable = entries_by_city(entries_of(input_value))
    for city in input_value.examined:
        if returned_alerts.get(city) != given_alerts.get(city) or returned_unavailable.get(city) != given_unavailable.get(city):
            return result(False, f"the returned report does not hold this call's judgement of {city}")
    if not len(input_value.examined) <= output_value.examined <= CITY_COUNT:
        return result(False, f"examined must count this call's cities and at most the {CITY_COUNT} cities")
    if output_value.complete != (output_value.examined == CITY_COUNT):
        return result(False, "complete must say whether every city is examined")
    return result(
        True,
        "report is saved",
        examined=output_value.examined,
        complete=output_value.complete,
        alerts=len(input_value.alerts),
        unavailable=len(input_value.unavailable),
    )


def exact(value):
    """A reported JSON number as the decimal its shortest text means."""
    return Decimal(str(value))


def compare(expected, judgement):
    """Score a well-formed ``judgement`` ({"alerts", "unavailable"} as JSON) against the ``expected``
    alerts (a rules.Expected).

    Every entry is an item, (alert, city) or (unavailable, city); an item counts as a hit only
    when it is exactly right: an alert's values and matched conditions, an unavailable entry's
    variables. The score is the F1 of the judgement's items against the expected ones, so it is 1.0
    only for an exact match. Returns (score, problems, differences).
    """
    want_alerts = {alert.city: alert for alert in expected.alerts}
    want_unavailable = dict(expected.unavailable)
    got_alerts = {alert["city"]: alert for alert in judgement["alerts"]}
    got_unavailable = {entry["city"]: entry["variables"] for entry in judgement["unavailable"]}
    differences = {
        "missing_alerts": sorted(set(want_alerts) - set(got_alerts)),
        "extra_alerts": sorted(set(got_alerts) - set(want_alerts)),
        "value_mismatches": {},
        "matched_mismatches": {},
        "missing_unavailable": sorted(set(want_unavailable) - set(got_unavailable)),
        "extra_unavailable": sorted(set(got_unavailable) - set(want_unavailable)),
        "unavailable_mismatches": {},
    }
    hits = 0
    for city in sorted(set(want_alerts) & set(got_alerts)):
        want, got = want_alerts[city], got_alerts[city]
        values_off = {}
        for variable in sorted(set(want.values) | set(got["values"])):
            wanted = want.values.get(variable)
            reported = got["values"].get(variable)
            if wanted is None or reported is None or exact(reported) != wanted:
                values_off[variable] = {
                    "expected": None if wanted is None else float(wanted),
                    "reported": None if reported is None else float(reported),
                }
        matched = canonical_conditions(got["matched"])
        matched_off = set(matched) != set(want.matched)
        if values_off:
            differences["value_mismatches"][city] = values_off
        if matched_off:
            differences["matched_mismatches"][city] = {"expected": list(want.matched), "reported": matched}
        if not values_off and not matched_off:
            hits += 1
    for city in sorted(set(want_unavailable) & set(got_unavailable)):
        if set(got_unavailable[city]) != set(want_unavailable[city]):
            differences["unavailable_mismatches"][city] = {
                "expected": list(want_unavailable[city]),
                "reported": list(got_unavailable[city]),
            }
        else:
            hits += 1
    submitted = len(got_alerts) + len(got_unavailable)
    wanted = len(want_alerts) + len(want_unavailable)
    score = 2 * hits / (submitted + wanted) if submitted or wanted else 1.0
    problems = []
    for key, noun in (
        ("missing_alerts", "alert(s) missing"),
        ("extra_alerts", "extra alert(s)"),
        ("value_mismatches", "alert(s) with a wrong value"),
        ("matched_mismatches", "alert(s) with wrong matched conditions"),
        ("missing_unavailable", "unavailable city(ies) missing"),
        ("extra_unavailable", "extra unavailable city(ies)"),
        ("unavailable_mismatches", "unavailable city(ies) with wrong variables"),
    ):
        count = len(differences[key])
        if count:
            problems.append(f"{count} {noun}")
    return score, problems, differences

"""report_alerts: a well-formed call at input, the merged file at output, and the comparison of a
report with the expected alerts that verify_task scores."""

import json
from decimal import Decimal
from pathlib import Path

from .checks import date_check, result
from .rules import MAX_REPORT_ENTRIES, canonical_conditions, entries_problem, names_ok, report_problem

REPORT_DIR = "weather-alert"
RUN_MARKER = "run.json"


def report_path(date):
    return f"{REPORT_DIR}/{date}.json"


def report_file(workspace, date):
    return Path(workspace) / REPORT_DIR / f"{date}.json"


def run_of(workspace):
    """The run marker the plugin process wrote at its start (weather-alert/run.json), or None."""
    path = Path(workspace) / REPORT_DIR / RUN_MARKER
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    run = found.get("run") if isinstance(found, dict) else None
    return run if isinstance(run, str) and run.strip() else None


NO_MARKER = "no run marker in the workspace: the plugin did not start here"


def plain(input_value):
    """A ReportInput as JSON: one call's judgement."""
    return {
        "date": input_value.date,
        "examined": list(input_value.examined),
        "alerts": [{"city": a.city, "values": dict(a.values), "matched": list(a.matched)} for a in input_value.alerts],
        "unavailable": [{"city": u.city, "variables": list(u.variables)} for u in input_value.unavailable],
    }


def verify_input(value):
    if not names_ok(value.examined) or not 1 <= len(value.examined) <= MAX_REPORT_ENTRIES:
        return result(False, f"examined must name 1 to {MAX_REPORT_ENTRIES} distinct cities")
    problem = entries_problem(value.examined, plain(value)["alerts"], plain(value)["unavailable"])
    if problem:
        return result(False, problem)
    return date_check(value.date) or result(True, "input is valid")


def entries_by_city(report):
    return {entry["city"]: entry for entry in report["alerts"]}, {entry["city"]: entry for entry in report["unavailable"]}


def verify_output(input_value, output_value, workspace):
    """The merged file is a report of this run and holds this call's judgement of every city it examined.
    Nothing cumulative is checked: other calls of the same turn may have merged more since."""
    path = report_file(workspace, input_value.date)
    if output_value.path != report_path(input_value.date) or not path.is_file():
        return result(False, f"the report is not at {report_path(input_value.date)}")
    run = run_of(workspace)
    if run is None:
        return result(False, NO_MARKER)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return result(False, f"{report_path(input_value.date)} is not a report of this run: not JSON")
    problem = report_problem(saved)
    if problem:
        return result(False, f"{report_path(input_value.date)} is not a report of this run: {problem}")
    if saved["date"] != input_value.date or saved["run"] != run:
        return result(False, f"{report_path(input_value.date)} is not a report of this run")
    if not set(input_value.examined) <= set(saved["examined"]):
        return result(False, "the saved report does not record this call's examined cities")
    given = plain(input_value)
    saved_alerts, saved_unavailable = entries_by_city(saved)
    given_alerts, given_unavailable = entries_by_city(given)
    for city in input_value.examined:
        if saved_alerts.get(city) != given_alerts.get(city) or saved_unavailable.get(city) != given_unavailable.get(city):
            return result(False, f"the saved report does not hold this call's judgement of {city}")
    counts = (len(input_value.alerts), len(input_value.unavailable), len(input_value.examined))
    if (output_value.alerts, output_value.unavailable, output_value.examined) != counts:
        return result(False, "the counts are not this call's entries")
    return result(True, "report is saved", alerts=counts[0], unavailable=counts[1], examined=counts[2])


def exact(value):
    """A reported JSON number as the decimal its shortest text means."""
    return Decimal(str(value))


def compare(expected, report):
    """Score a well-formed ``report`` against the ``expected`` alerts (a rules.Expected).

    Every report entry is an item, (alert, city) or (unavailable, city); an item counts as a hit only
    when it is exactly right: an alert's values and matched conditions, an unavailable entry's
    variables. The score is the F1 of the report's items against the expected ones, so it is 1.0
    only for an exact match. Returns (score, problems, differences).
    """
    want_alerts = {alert.city: alert for alert in expected.alerts}
    want_unavailable = dict(expected.unavailable)
    got_alerts = {alert["city"]: alert for alert in report["alerts"]}
    got_unavailable = {entry["city"]: entry["variables"] for entry in report["unavailable"]}
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

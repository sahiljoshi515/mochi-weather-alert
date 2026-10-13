"""report_alerts: a well-formed report at input, the saved file at output, and the comparison of a
report with the expected alerts that verify_task scores."""

import json
from decimal import Decimal
from pathlib import Path

from .checks import date_check, result
from .rules import VARIABLES, parse_condition

REPORT_DIR = "weather-alert"
KINDS = ("alert", "unavailable")


def report_path(date):
    return f"{REPORT_DIR}/{date}.json"


def report_file(workspace, date):
    return Path(workspace) / REPORT_DIR / f"{date}.json"


def plain(input_value):
    """A ReportInput as the JSON report_alerts writes."""
    return {
        "date": input_value.date,
        "alerts": [{"city": a.city, "values": dict(a.values), "matched": list(a.matched)} for a in input_value.alerts],
        "unavailable": [{"city": u.city, "variables": list(u.variables)} for u in input_value.unavailable],
    }


def canonical_conditions(texts):
    """The conditions' canonical forms, or ValueError when one is malformed or two mean the same."""
    found = [parse_condition(text).text for text in texts]
    if len(set(found)) != len(found):
        raise ValueError("a condition is listed twice")
    return found


def is_number(value):
    return isinstance(value, int | float) and not isinstance(value, bool)


def report_problem(report):
    """What is wrong with a report's shape (a parsed JSON value), or None."""
    if not isinstance(report, dict) or set(report) != {"date", "alerts", "unavailable"}:
        return "the report must hold date, alerts and unavailable"
    if not isinstance(report["date"], str):
        return "the report's date must be a string"
    if not isinstance(report["alerts"], list) or not isinstance(report["unavailable"], list):
        return "alerts and unavailable must be lists"
    cities = []
    for alert in report["alerts"]:
        if not isinstance(alert, dict) or set(alert) != {"city", "values", "matched"}:
            return "each alert must hold city, values and matched"
        if not isinstance(alert["city"], str) or not alert["city"].strip():
            return "an alert's city must be a city name"
        values = alert["values"]
        if not isinstance(values, dict) or not all(key in VARIABLES and is_number(value) for key, value in values.items()):
            return f"{alert['city']}: values must map condition variables to numbers"
        if not isinstance(alert["matched"], list) or not all(isinstance(text, str) for text in alert["matched"]):
            return f"{alert['city']}: matched must be a list of conditions"
        try:
            canonical_conditions(alert["matched"])
        except ValueError as error:
            return f"{alert['city']}: matched: {error}"
        cities.append(alert["city"])
    for entry in report["unavailable"]:
        if not isinstance(entry, dict) or set(entry) != {"city", "variables"}:
            return "each unavailable entry must hold city and variables"
        if not isinstance(entry["city"], str) or not entry["city"].strip():
            return "an unavailable entry's city must be a city name"
        variables = entry["variables"]
        if not isinstance(variables, list) or not variables or not all(name in VARIABLES for name in variables):
            return f"{entry['city']}: variables must name the condition variables the city lacks"
        if len(set(variables)) != len(variables):
            return f"{entry['city']}: variables must be distinct"
        cities.append(entry["city"])
    if len(set(cities)) != len(cities):
        return "a city may appear once, as an alert or as unavailable"
    return None


def verify_input(value):
    problem = report_problem(plain(value))
    if problem:
        return result(False, problem)
    return date_check(value.date) or result(True, "input is valid")


def verify_output(input_value, output_value, workspace):
    path = report_file(workspace, input_value.date)
    if output_value.path != report_path(input_value.date) or not path.is_file():
        return result(False, f"the report is not at {report_path(input_value.date)}")
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return result(False, "the saved report is not JSON")
    if saved != plain(input_value):
        return result(False, "the saved report differs from the submitted alerts")
    if (output_value.alerts, output_value.unavailable) != (len(input_value.alerts), len(input_value.unavailable)):
        return result(False, "the counts differ from the submitted alerts")
    return result(True, "report is saved", alerts=output_value.alerts, unavailable=output_value.unavailable)


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

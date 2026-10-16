"""report_alerts: a well-formed call at input, the merged report at output, and the comparison of a
judgement with the expected alerts that the task's verdict scores."""

from decimal import Decimal
from pathlib import Path

from .checks import entries_by_city, entries_of, read_json, result, run_problem
from .daily import cities_problem
from .receipt import receipt_of
from .rules import (
    CITY_COUNT,
    MAX_REPORT_ENTRIES,
    canonical_conditions,
    canonical_task,
    judgement_problem,
    plain_source,
    plain_task,
    report_path,
    report_problem,
    task_problem,
)


def saved_report(workspace, date, run):
    """(run ``run``'s report file for ``date`` as JSON, None) or (None, why it is not one)."""
    problem = run_problem(workspace, run)
    if problem:
        return None, problem
    where = report_path(date, run)
    found, problem = read_json(Path(workspace) / where)
    if problem == "missing":
        return None, f"no report at {where}: call report_alerts for every city before submitting"
    if problem:
        return None, f"{where} is not a report: {problem}"
    problem = report_problem(found)
    if problem:
        return None, f"{where} is not a report report_alerts wrote: {problem}"
    if found["run"] != run or found["date"] != date:
        return None, f"{where} names run {found['run']} and date {found['date']}: it was altered after report_alerts"
    return found, None


def verify_input(value):
    task = plain_task(value.task)
    problem = task_problem(task) or cities_problem(list(value.examined), MAX_REPORT_ENTRIES)
    if problem:
        return result(False, problem)
    entries = entries_of(value)
    problem = judgement_problem(task, list(value.examined), entries["alerts"], entries["unavailable"])
    return result(False, problem) if problem else result(True, "input is valid")


def verify_output(input_value, output_value, workspace):
    """The file is a report of this run for this task on the data this run read, and records this call's
    examined cities; the returned report names that data and holds this call's judgement of every city
    it examined. Nothing cumulative is checked against the file: other calls may have merged since."""
    task = plain_task(input_value.task)
    date = task["date"]
    run = output_value.report.run
    problem = run_problem(workspace, run)
    if problem:
        return result(False, problem)
    if output_value.path != report_path(date, run):
        return result(False, f"the report is not at {report_path(date, run)}")
    saved, problem = saved_report(workspace, date, run)
    if problem:
        return result(False, problem)
    receipt, problem = receipt_of(workspace, date, run)
    if problem:
        return result(False, problem)
    if canonical_task(saved["task"]) != canonical_task(task):
        return result(False, "the saved report is for another task input")
    if saved["source"] != receipt["source"] or saved["sha256"] != receipt["sha256"]:
        return result(False, "the saved report does not name the data get_daily read")
    if not set(input_value.examined) <= set(saved["examined"]):
        return result(False, "the saved report does not record this call's examined cities")
    if canonical_task(plain_task(output_value.report.task)) != canonical_task(task):
        return result(False, "the returned report is for another task input")
    if plain_source(output_value.report.data) != receipt["source"]:
        return result(False, "the returned report does not name the data get_daily read")
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
    return result(True, "report is saved", examined=output_value.examined, complete=output_value.complete)


def exact(value):
    """A reported JSON number as the decimal its shortest text means."""
    return Decimal(str(value))


def compare(expected, judgement):
    """Compare a well-formed ``judgement`` ({"alerts", "unavailable"} as JSON) with the ``expected``
    alerts (a rules.Expected). Every entry is an item, (alert, city) or (unavailable, city); an item is
    a hit only when it is exactly right. Returns (F1 of the items, differences)."""
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
            differences["unavailable_mismatches"][city] = {"expected": list(want_unavailable[city]), "reported": list(got_unavailable[city])}
        else:
            hits += 1
    submitted = len(got_alerts) + len(got_unavailable)
    wanted = len(want_alerts) + len(want_unavailable)
    score = 2 * hits / (submitted + wanted) if submitted or wanted else 1.0
    return score, differences

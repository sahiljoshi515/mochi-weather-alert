"""pin_alerts (the daily-alert reference) and the daily-alert task's outcome check."""

import json

from .checks import date_check, day_problem, names_problem, result
from .report import compare, report_file, report_path, report_problem
from .rules import Units, evaluate, metrics_from_plain, parse_conditions, plain_report


def conditions_check(value):
    try:
        parse_conditions(value.conditions)
    except ValueError as error:
        return result(False, str(error))
    return None


def verify_input(value):
    return date_check(value.date) or conditions_check(value) or result(True, "input is valid")


def units_of(arguments):
    return Units(arguments.temperature_unit.value, arguments.wind_unit.value, arguments.rain_unit.value)


def metrics_of(reference):
    return [
        metrics_from_plain(
            row.city,
            row.hours,
            row.max_temp_c,
            row.min_temp_c,
            row.rain_mm,
            row.max_wind_kmh,
            row.humidity_sum_pct,
            row.humidity_hours,
            row.missing,
        )
        for row in reference.metric
    ]


def expected_of(arguments, reference):
    """The alerts the pinned metric aggregates give under the task's rules: the pin's own alert list
    is never trusted."""
    conditions = parse_conditions(arguments.conditions)
    return evaluate(metrics_of(reference), conditions, arguments.match.value, units_of(arguments))


def verify_output(input_value, output_value, workspace):
    if output_value.date != input_value.date:
        return result(False, "the pin is for another date")
    problem = names_problem([row.city for row in output_value.metric], what="metric cities")
    if problem or not output_value.metric:
        return result(False, problem or "no city is pinned")
    for row in output_value.metric:
        problem = day_problem(row, humidity=row.humidity_sum_pct)
        if problem:
            return result(False, problem)
        if not 0 <= row.humidity_hours <= row.hours or (row.humidity_sum_pct is None) != (row.humidity_hours == 0):
            return result(False, f"{row.city}: the humidity sum and its hour count disagree")
    pinned = {"date": output_value.date, **plain_report(output_value.date, expected_of(input_value, output_value))}
    own = {
        "date": output_value.date,
        "alerts": [{"city": a.city, "values": dict(a.values), "matched": list(a.matched)} for a in output_value.alerts],
        "unavailable": [{"city": u.city, "variables": list(u.variables)} for u in output_value.unavailable],
    }
    if own != pinned:
        return result(False, "the pin's alerts disagree with its own metric aggregates")
    return result(
        True,
        "the day is pinned",
        cities=len(output_value.metric),
        alerts=len(output_value.alerts),
        unavailable=len(output_value.unavailable),
    )


def verify_task(arguments, reference, workspace):
    """Approve the report only when it holds exactly the alerts the pinned aggregates give, each with
    exactly the expected values and matched conditions, and exactly the unavailable cities."""
    if reference is None:
        return result(False, "no pinned reference", score=0.0)
    problem = date_check(arguments.date) or conditions_check(arguments)
    if problem:
        return result(False, problem["message"], score=0.0)
    if reference.date != arguments.date:
        return result(False, "the pinned day is another date", score=0.0)
    expected = expected_of(arguments, reference)
    report = {
        "expected": plain_report(arguments.date, expected),
        "conditions": [condition.text for condition in parse_conditions(arguments.conditions)],
        "match": arguments.match.value,
        "units": {
            "temperature": arguments.temperature_unit.value,
            "wind": arguments.wind_unit.value,
            "rain": arguments.rain_unit.value,
        },
        "cities": len(reference.metric),
    }
    path = report_file(workspace, arguments.date)
    if not path.is_file():
        return result(False, f"no report at {report_path(arguments.date)}", score=0.0, **report)
    try:
        submitted = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return result(False, f"{report_path(arguments.date)} is not JSON", score=0.0, **report)
    problem = report_problem(submitted)
    if problem:
        return result(False, f"{report_path(arguments.date)}: {problem}", score=0.0, **report)
    if submitted["date"] != arguments.date:
        return result(False, "the report is for another day", score=0.0, **report)
    score, problems, differences = compare(expected, submitted)
    counts = f"{len(expected.alerts)} alert(s) and {len(expected.unavailable)} unavailable city(ies)"
    if problems:
        message = f"{'; '.join(problems)}: expected exactly {counts} (F1 {score:.2f})"
        return result(False, message, score=score, **differences, **report)
    return result(True, f"exactly the {counts}", score=score, **report)

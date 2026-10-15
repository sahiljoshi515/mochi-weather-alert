"""pin_alerts (the daily-alert reference) and the daily-alert task's verdict on the submitted report."""

from .checks import date_check, day_problem, names_problem, result
from .report import compare, entries_by_city, entries_of, saved_report
from .rules import (
    Units,
    canonical_task,
    evaluate,
    expected_output,
    judgement_problem,
    metrics_from_plain,
    parse_conditions,
    plain_entries,
    plain_task,
)


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
    expected = expected_of(input_value, output_value)
    own = {"examined": [row.city for row in output_value.metric], **entries_of(output_value)}
    pinned = {"examined": list(expected.examined), **plain_entries(expected)}
    if own != pinned:
        return result(False, "the pin's alerts disagree with its own metric aggregates")
    return result(
        True,
        "the day is pinned",
        cities=len(output_value.metric),
        alerts=len(output_value.alerts),
        unavailable=len(output_value.unavailable),
    )


def verify_task(arguments, output, reference, workspace):
    """Approve the submitted report only when it is the report this run saved with every city
    examined, and holds exactly the alerts the pinned aggregates give, each with exactly the expected
    values and matched conditions, and exactly the unavailable cities."""
    if reference is None:
        return result(False, "no pinned reference", score=0.0)
    problem = date_check(arguments.date) or conditions_check(arguments)
    if problem:
        return result(False, problem["message"], score=0.0)
    if reference.date != arguments.date:
        return result(False, "the pinned day is another date", score=0.0)
    task = plain_task(arguments)
    expected = expected_of(arguments, reference)
    report = {
        "expected": expected_output(task, expected),
        "conditions": [condition.text for condition in parse_conditions(arguments.conditions)],
        "match": arguments.match.value,
        "units": {
            "temperature": arguments.temperature_unit.value,
            "wind": arguments.wind_unit.value,
            "rain": arguments.rain_unit.value,
        },
        "cities": len(reference.metric),
    }
    if canonical_task(plain_task(output.task)) != canonical_task(task):
        return result(False, "the submitted report is for another task input: submit the report the last report_alerts call returned", score=0.0, **report)
    submitted = entries_of(output)
    problem = judgement_problem(task, None, submitted["alerts"], submitted["unavailable"])
    if problem:
        return result(False, f"the submitted report is malformed: {problem}", score=0.0, **report)
    saved, problem = saved_report(workspace, arguments.date)
    if problem:
        return result(False, problem, score=0.0, **report)
    if canonical_task(saved["task"]) != canonical_task(task):
        return result(False, "the saved report is for another task input", score=0.0, **report)
    cities = set(expected.examined)
    examined = set(saved["examined"])
    coverage = {
        "examined": len(examined & cities),
        "unexamined": sorted(cities - examined),
        "unknown_examined": sorted(examined - cities),
    }
    score, problems, differences = compare(expected, submitted)
    counts = f"{len(expected.alerts)} alert(s) and {len(expected.unavailable)} unavailable city(ies)"
    if entries_by_city(saved) != entries_by_city(submitted):
        message = "the submitted report differs from the saved report: submit the report the last report_alerts call returned, as it is"
        return result(False, message, score=0.0, **differences, **coverage, **report)
    if coverage["unexamined"] or coverage["unknown_examined"]:
        # Every city must be judged: the entries' F1 is scaled by the share of the pinned cities among
        # the pinned and the examined ones, so an unexamined city and a name not in the snapshot both cost.
        score = score * len(examined & cities) / len(examined | cities)
        parts = []
        if coverage["unexamined"]:
            named = ", ".join(coverage["unexamined"][:5]) + (", …" if len(coverage["unexamined"]) > 5 else "")
            parts.append(f"{len(coverage['unexamined'])} city(ies) unexamined ({named})")
        if coverage["unknown_examined"]:
            parts.append(f"{len(coverage['unknown_examined'])} examined name(s) not in the snapshot")
        message = "; ".join(parts + problems)
        return result(False, f"{message}: expected all {len(cities)} cities examined and exactly {counts} (F1 {score:.2f})", score=score, **differences, **coverage, **report)
    if problems:
        message = f"{'; '.join(problems)}: expected exactly {counts} (F1 {score:.2f})"
        return result(False, message, score=score, **differences, **coverage, **report)
    return result(True, f"every city examined, exactly the {counts}", score=score, **coverage, **report)

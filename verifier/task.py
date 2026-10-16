"""The daily-alert verdict: 21 constraints, in order, on the answer key, the data the agent read, the
saved and submitted report, and the alerts themselves. The verdict is valid only when every one holds;
any failure among the first 13 (the data and the report's integrity) scores 0, otherwise the score is
the F1 of the reported alert and unavailable cities, scaled by the share of cities examined."""

from .checks import compact, constraint, entries_by_city, entries_of, plain_candidate, result, run_of
from .pin import pin_problems
from .receipt import receipt_of
from .report import compare, saved_report
from .rules import (
    CITY_NAMES,
    DELTA_MINUTES,
    SOURCE_OF,
    SOURCE_VARIABLES,
    Units,
    canonical_task,
    convert,
    day_metrics,
    exact_metric,
    expected_from_series,
    holds,
    judgement_problem,
    key_of,
    parse_conditions,
    plain_source,
    plain_task,
    stamp_minutes,
    stamp_text,
    stamps_inside,
    task_problem,
    truncate,
)

TEXT = {
    "answer-key": "The answer key pinned the data for this date",
    "task-input": "The task input is a valid date and 1 to 6 conditions",
    "answer-key-data": "The answer key's data is well formed: every city's local day, its readings and their hash",
    "answer-key-rules": "The answer key used the data the date rules choose at its clock",
    "run-marker": "The plugin marked this run in the workspace",
    "agent-read": "The agent read this date's data with get_daily in this run",
    "agent-source": "The agent read a source the answer key pinned",
    "agent-data": "The agent's data is the pinned data, reading for reading",
    "report-saved": "report_alerts saved this run's report for this task, on the data the agent read",
    "submitted-task": "The submitted report is for the task input",
    "submitted-shape": "The submitted report is well formed",
    "submitted-as-saved": "The submitted report is the saved report, unchanged",
    "names-data": "The submitted report names the data the agent read",
    "all-examined": "All 30 cities were examined",
    "alerts-complete": "Every city whose day meets the rules is reported as an alert",
    "no-extra-alerts": "No city is reported as an alert that is not one",
    "values-exact": "Every alert's values are exact: the task's units, rounded half up to 1 decimal",
    "matched-exact": "Every alert's matched conditions are exactly those that hold",
    "unavailable-complete": "Every city missing a reading a condition needs is reported unavailable",
    "no-extra-unavailable": "No city that has its readings is reported unavailable",
    "unavailable-exact": "Every unavailable city names exactly the variables it lacks",
}
ORDER = tuple(TEXT)
GATES = ORDER[:13]  # the data and the report's integrity: any failure scores 0
MOST_NAMED = 3  # cities a failure message names


def first_gap(series, variable, step_hours):
    """The first stamp of the day with no reading of ``variable``, as text, or None."""
    start, end = stamp_minutes(series["start"]), stamp_minutes(series["end"])
    index = {stamp_minutes(time): position for position, time in enumerate(series["times"])}
    for stamp in stamps_inside(start, end, step_hours * 60, accumulated=variable == "precipitation"):
        if stamp not in index or series[variable][index[stamp]] is None:
            return f"{stamp_text(stamp)}Z"
    return None


def first_difference(pinned, read):
    """Where two versions of the same source's data first differ, in words."""
    pinned_by_city = {series["city"]: series for series in pinned["cities"]}
    for series in read["cities"]:
        other = pinned_by_city.get(series["city"])
        if other is None:
            return f"{series['city']} is not in the pinned data"
        if series["times"] != other["times"]:
            return f"{series['city']}'s stamps differ"
        for variable in SOURCE_VARIABLES:
            for time, now, then in zip(series["times"], series[variable], other[variable], strict=True):
                if now != then:
                    return f"{series['city']} {variable} at {time}Z was {then} when pinned and {now} when read"
    return "the readings differ"


def why_not_alert(task, series, step_hours):
    """Why a city that is not an alert is not one: each condition that fails, on the exact value."""
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    day = day_metrics(series, step_hours)
    lacking = [variable for variable in {condition.variable for condition in conditions} if SOURCE_OF[variable] in day.missing]
    if lacking:
        return f"{series['city']} lacks a reading for {', '.join(sorted(lacking))}, so it is unavailable, never an alert"
    reasons = []
    for condition in conditions:
        value = convert(condition.variable, exact_metric(day, condition.variable), units)
        if not holds(value, condition):
            reasons.append(f"{condition.variable} {truncate(value)} is not {condition.op} {condition.threshold}")
    rule = "every condition must hold" if task["match"] == "all" else "no condition holds"
    return f"{series['city']} is no alert ({rule}): {'; '.join(reasons[:2])}"


def named(cities):
    shown = ", ".join(cities[:MOST_NAMED])
    return shown + (f" and {len(cities) - MOST_NAMED} more" if len(cities) > MOST_NAMED else "")


def entry_messages(task, expected, differences, series_of, step_hours):
    """Plain messages for the alert constraints (15 to 21), each naming the first cities concerned."""
    alerts = {alert.city: alert for alert in expected.alerts}
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    messages = {}
    if differences["missing_alerts"]:
        cities = differences["missing_alerts"]
        first = alerts[cities[0]]
        values = ", ".join(f"{name} {value}" for name, value in first.values.items())
        messages["alerts-complete"] = f"Missing alert {named(cities)}: {cities[0]} has {values} and meets {', '.join(first.matched)}"
    if differences["extra_alerts"]:
        cities = differences["extra_alerts"]
        messages["no-extra-alerts"] = f"Extra alert {named(cities)}: {why_not_alert(task, series_of[cities[0]], step_hours)}"
    if differences["value_mismatches"]:
        city, wrong = next(iter(differences["value_mismatches"].items()))
        variable, values = next(iter(wrong.items()))
        exact = truncate(convert(variable, exact_metric(day_metrics(series_of[city], step_hours), variable), units))
        messages["values-exact"] = (
            f"Wrong value in {named(sorted(differences['value_mismatches']))}: {city} {variable} is {values['expected']} "
            f"(exact {exact}, rounded half up), reported {values['reported']}"
        )
    if differences["matched_mismatches"]:
        city, both = next(iter(differences["matched_mismatches"].items()))
        messages["matched-exact"] = f"Wrong matched conditions for {named(sorted(differences['matched_mismatches']))}: {city} meets {both['expected']}, reported {both['reported']}"
    if differences["missing_unavailable"]:
        cities = differences["missing_unavailable"]
        variable = expected.unavailable[cities[0]][0]
        gap = first_gap(series_of[cities[0]], SOURCE_OF[variable], step_hours)
        messages["unavailable-complete"] = f"Missing unavailable {named(cities)}: {cities[0]} has no {SOURCE_OF[variable]} reading at {gap}, so no {variable}"
    if differences["extra_unavailable"]:
        cities = differences["extra_unavailable"]
        needed = sorted({condition.variable for condition in conditions})
        messages["no-extra-unavailable"] = f"Extra unavailable {named(cities)}: {cities[0]} has every reading of {', '.join(needed)}"
    if differences["unavailable_mismatches"]:
        city, both = next(iter(differences["unavailable_mismatches"].items()))
        messages["unavailable-exact"] = f"Wrong variables for {named(sorted(differences['unavailable_mismatches']))}: {city} lacks {both['expected']}, reported {both['reported']}"
    return messages


def verify_task(arguments, output, reference, workspace):
    """The verdict on a submitted daily-alert report (see the module's docstring)."""
    task = plain_task(arguments)
    date = arguments.date
    checks = {}
    details = {"rerun": False}

    def put(identifier, ok, expected=None, actual=None, message=None):
        checks[identifier] = constraint(identifier, TEXT[identifier], ok, expected, actual, message)

    def holds_(*identifiers):
        return all(checks[identifier]["ok"] is True for identifier in identifiers)

    # 1-4: the answer key.
    if reference is None:
        put("answer-key", False, date, None, f"No pinned data for {date}: the answer key never fetched the day.")
    else:
        put("answer-key", reference.date == date, date, reference.date, f"The answer key pinned {reference.date}, not {date}.")
    problem = task_problem(task)
    put("task-input", problem is None, None, None, problem)
    candidates = []
    if holds_("answer-key", "task-input"):
        shape, rules = pin_problems(task, reference)
        put("answer-key-data", shape is None, None, None, shape)
        put("answer-key-rules", None if shape else rules is None, None, None, rules)
        if shape is None:
            candidates = [plain_candidate(candidate) for candidate in reference.candidates]
    else:
        put("answer-key-data", None)
        put("answer-key-rules", None)

    # 5-8: the data the agent read.
    run = run_of(workspace)
    put("run-marker", run is not None, None, None, "No run marker in the workspace: the plugin did not start here.")
    receipt = None
    if holds_("run-marker", "task-input"):
        receipt, problem = receipt_of(workspace, date)
        put("agent-read", receipt is not None, None, receipt["source"]["label"] if receipt else None, problem)
    else:
        put("agent-read", None)
    match = None
    if receipt is not None and holds_("answer-key-data", "answer-key-rules"):
        labels = [candidate["source"]["label"] for candidate in candidates]
        found = [candidate for candidate in candidates if key_of(candidate["source"]) == key_of(receipt["source"])]
        message = None
        if not found:
            pinned_at = stamp_minutes(reference.clock)
            gap = stamp_minutes(receipt["clock"]) + DELTA_MINUTES - pinned_at
            message = f"The agent read the {receipt['source']['label']}; the answer key pinned the {' and the '.join(labels)}."
            if gap > DELTA_MINUTES:
                message += f" The agent read {gap} min after the answer key, more than the {DELTA_MINUTES} min allowed: run again."
            else:
                message += " Open-Meteo published a newer run between the two fetches: run again."
        put("agent-source", bool(found), labels, receipt["source"]["label"], message)
        match = found[0] if found else None
    else:
        put("agent-source", None)
    if match is not None:
        identical = match["sha256"] == receipt["sha256"]
        message = None
        if not identical:
            details["rerun"] = True
            message = (
                f"The data changed upstream between the answer key's fetch and the agent's: {first_difference(match, receipt)}. "
                "Nothing the agent did; run again."
            )
        put("agent-data", identical, match["sha256"][:16], receipt["sha256"][:16], message)
    else:
        put("agent-data", None)

    # 9-13: the saved and the submitted report.
    saved = None
    if holds_("run-marker", "task-input"):
        saved, problem = saved_report(workspace, date)
        if saved is not None and canonical_task(saved["task"]) != canonical_task(task):
            saved, problem = None, "The saved report is for another task input."
        if saved is not None and receipt is not None and (saved["source"] != receipt["source"] or saved["sha256"] != receipt["sha256"]):
            saved, problem = None, "The saved report judges other data than the agent read with get_daily."
        put("report-saved", saved is not None, None, None, problem)
    else:
        put("report-saved", None)
    submitted_task = plain_task(output.task)
    entries = entries_of(output)
    if holds_("task-input"):
        same = task_problem(submitted_task) is None and canonical_task(submitted_task) == canonical_task(task)
        put("submitted-task", same, task, submitted_task, "The submitted report is for another task input: submit the report the last report_alerts call returned.")
        problem = judgement_problem(task, None, entries["alerts"], entries["unavailable"]) if same else None
        put("submitted-shape", problem is None if same else None, None, None, f"The submitted report is malformed: {problem}")
    else:
        put("submitted-task", None)
        put("submitted-shape", None)
    if saved is not None and holds_("submitted-task", "submitted-shape"):
        same = entries_by_city(saved) == entries_by_city(entries)
        put("submitted-as-saved", same, None, None, "The submitted report differs from the saved report: submit the report the last report_alerts call returned, as it is.")
    else:
        put("submitted-as-saved", None)
    if receipt is not None:
        named_data = plain_source(output.data)
        put("names-data", named_data == receipt["source"], receipt["source"]["label"], named_data.get("label"), f"The report names the {named_data.get('label')}; the agent read the {receipt['source']['label']}.")
    else:
        put("names-data", None)

    # 14-21: the alerts, against the pinned data the agent read.
    expected = None
    differences = None
    if match is not None and checks["agent-data"]["ok"] is True:
        step = match["source"]["step_hours"]
        expected = expected_from_series(task, step, match["cities"])
        series_of = {series["city"]: series for series in match["cities"]}
    elif match is not None and receipt is not None:
        # Diagnostic only: what the submission is worth on the data the agent itself read.
        diagnostic = expected_from_series(task, receipt["source"]["step_hours"], receipt["cities"])
        if checks["submitted-shape"]["ok"] is True:
            score, _ = compare(diagnostic, entries)
            details["agent_data_check"] = "against the data the agent read, the report is exact" if score == 1.0 else f"against the data the agent read, the report scores F1 {score:.2f}"
    coverage = None
    if saved is not None:
        examined = set(saved["examined"])
        cities = set(CITY_NAMES)
        coverage = len(examined & cities) / len(examined | cities)
        unexamined = sorted(cities - examined)
        put("all-examined", not unexamined, len(cities), len(examined & cities), f"{len(unexamined)} cities unexamined ({named(unexamined)}): every one of the 30 must be judged.")
    else:
        put("all-examined", None)
    score = None
    if expected is not None and checks["submitted-shape"]["ok"] is True:
        score, differences = compare(expected, entries)
        messages = entry_messages(task, expected, differences, series_of, step)
        rows = (
            ("alerts-complete", "missing_alerts"),
            ("no-extra-alerts", "extra_alerts"),
            ("values-exact", "value_mismatches"),
            ("matched-exact", "matched_mismatches"),
            ("unavailable-complete", "missing_unavailable"),
            ("no-extra-unavailable", "extra_unavailable"),
            ("unavailable-exact", "unavailable_mismatches"),
        )
        for identifier, key in rows:
            wrong = differences[key]
            listed = sorted(wrong) if isinstance(wrong, dict) else list(wrong)
            put(identifier, not wrong, [], listed, messages.get(identifier))
    else:
        for identifier in ORDER[14:]:
            put(identifier, None)

    constraints = [checks[identifier] for identifier in ORDER]
    failed = [entry for entry in constraints if entry["ok"] is False]
    unchecked = [entry for entry in constraints if entry["ok"] is None]
    valid = not failed and not unchecked
    if any(checks[identifier]["ok"] is not True for identifier in GATES) or score is None:
        final = 0.0
    else:
        final = score * (coverage if coverage is not None else 0.0)
    source = (match or receipt or {}).get("source") if (match or receipt) else None
    details.update(
        constraints=constraints,
        source=source["label"] if source else None,
        today=source["today"] if source else None,
    )
    if expected is not None:
        details["expected"] = {
            "alerts": compact([alert.city for alert in expected.alerts]),
            "unavailable": compact(sorted(expected.unavailable)),
        }
    if differences is not None:
        details["f1"] = round(score, 4)
    if valid:
        message = (
            f"All {len(constraints)} checks hold: 30 cities examined; exactly {len(expected.alerts)} alert(s) and "
            f"{len(expected.unavailable)} unavailable from the {source['label']}."
        )
        return result(True, message, score=1.0, **details)
    first = failed[0] if failed else unchecked[0]
    count = f"{len(failed)} of {len(constraints)} checks failed" + (f", {len(unchecked)} not checked" if unchecked else "")
    reason = first.get("message") or first["constraint"]
    return result(False, f"{count}. First: {reason}", score=min(final, 0.99), **details)

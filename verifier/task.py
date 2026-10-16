"""The daily-alert verdict: 21 constraints, in order, on the answer key, the run the submission names,
the data that run's agent read, the saved and submitted report, and the alerts themselves. The verdict
is valid only when every one holds; any failure among the first 13 (the data and the report's
integrity) scores 0, otherwise the score is the F1 of the reported alert and unavailable cities,
scaled by the share of cities examined.

Every file the verdict reads is the named run's own (reports/<run>/), so two task calls at once in one
workspace are judged apart; the run must have begun after the answer key (it is not among the runs the
answer key saw in the workspace, and its marker's time is not before the answer key's last fetch), so a
report left by an earlier task never passes for this one."""

from .checks import compact, constraint, entries_of, plain_candidate, result, run_marker
from .pin import pin_problems
from .receipt import receipt_of
from .report import compare, saved_report
from .rules import (
    CITY_NAMES,
    DELTA_MINUTES,
    SOURCE_OF,
    SOURCE_VARIABLES,
    DataKind,
    Units,
    canonical_judgement,
    canonical_number,
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
    seconds_text,
    stamp_minutes,
    stamp_seconds,
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
    "this-run": "The report names a run of this task: one that began after the answer key",
    "agent-read": "That run's agent read this date's data with get_daily",
    "agent-source": "The agent read a source the answer key pinned",
    "agent-data": "The agent's data is the pinned data, reading for reading",
    "report-saved": "report_alerts saved that run's report for this task, on the data the agent read",
    "submitted-task": "The submitted report is for the task input",
    "submitted-shape": "The submitted report is well formed",
    "submitted-as-saved": "The submitted report is the saved report, unchanged",
    "names-data": "The submitted report names the data the agent read",
    "all-examined": "All 30 cities were examined",
    "alerts-complete": "Every city whose day meets the rules is reported as an alert",
    "no-extra-alerts": "No city is reported as an alert that is not one",
    "values-exact": "Every alert's values are exact: the task's units, rounded to 1 decimal, ties away from zero",
    "matched-exact": "Every alert's matched conditions are exactly those that hold",
    "unavailable-complete": "Every city missing a reading a condition needs is reported unavailable",
    "no-extra-unavailable": "No city that has its readings is reported unavailable",
    "unavailable-exact": "Every unavailable city names exactly the variables it lacks",
}
ORDER = tuple(TEXT)
GATES = ORDER[:13]  # the data and the report's integrity: any failure scores 0
MOST_NAMED = 3  # cities a failure message names
# Why a source's data cannot change between two fetches minutes apart; the seasonal outlook alone can.
FINAL = {
    DataKind.FORECAST.value: "a forecast run's data never changes",
    DataKind.ARCHIVE.value: "an archived day is final (ERA5 to 2017-01-01, the IFS archive to T-2)",
}


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
                    return f"{series['city']} {variable} at {time}Z is {then} in the pinned data and {now} in the receipt"
    return "the readings differ"


def given(task, series, step_hours, variable):
    """The value get_daily gives a city for a variable (exact, cut to 9 decimals), as its shortest text."""
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    return canonical_number(truncate(convert(variable, exact_metric(day_metrics(series, step_hours), variable), units)))


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
            reasons.append(f"{condition.variable} {canonical_number(truncate(value))} is not {condition.op} {condition.threshold}")
    rule = "every condition must hold" if task["match"] == "all" else "no condition holds"
    return f"{series['city']} is no alert ({rule}): {'; '.join(reasons[:2])}"


def named(cities):
    shown = ", ".join(cities[:MOST_NAMED])
    return shown + (f" and {len(cities) - MOST_NAMED} more" if len(cities) > MOST_NAMED else "")


def entry_messages(task, expected, differences, series_of, step_hours):
    """Plain messages for the alert constraints (15 to 21), each naming the first cities concerned and
    showing a value as get_daily gave it beside its rounded report (91.94 -> 91.9)."""
    alerts = {alert.city: alert for alert in expected.alerts}
    conditions = parse_conditions(task["conditions"])
    messages = {}
    if differences["missing_alerts"]:
        cities = differences["missing_alerts"]
        first = alerts[cities[0]]
        series = series_of[cities[0]]
        values = ", ".join(f"{name} {given(task, series, step_hours, name)} -> report {value}" for name, value in first.values.items())
        messages["alerts-complete"] = f"Missing alert {named(cities)}: {cities[0]} has {values}; it meets {', '.join(first.matched)}"
    if differences["extra_alerts"]:
        cities = differences["extra_alerts"]
        series = series_of.get(cities[0])
        reason = why_not_alert(task, series, step_hours) if series else f"{cities[0]!r} is not one of the 30 cities"
        messages["no-extra-alerts"] = f"Extra alert {named(cities)}: {reason}"
    if differences["value_mismatches"]:
        city, wrong = next(iter(differences["value_mismatches"].items()))
        variable, values = next(iter(wrong.items()))
        exact = given(task, series_of[city], step_hours, variable)
        messages["values-exact"] = (
            f"Wrong value in {named(sorted(differences['value_mismatches']))}: {city} {variable} {exact} -> {values['expected']} "
            f"(1 decimal, ties away from zero), reported {values['reported']}"
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


def source_mismatch(receipt, candidates, pinned_at):
    """Why the agent's source is none the answer key pinned, in words: it read late, or a run was
    published (or not yet published for the agent) between the two fetches. Always: run again."""
    read = receipt["source"]
    labels = [candidate["source"]["label"] for candidate in candidates]
    head = f"The agent read the {read['label']}; the answer key pinned the {' and the '.join(labels)}."
    gap = stamp_minutes(receipt["clock"]) + DELTA_MINUTES - pinned_at
    if gap > DELTA_MINUTES:
        return f"{head} The agent read {gap} min after the answer key, more than the {DELTA_MINUTES} min allowed: run again."
    runs = [candidate["source"]["run"] for candidate in candidates if candidate["source"]["kind"] == DataKind.FORECAST.value]
    if read["kind"] == DataKind.FORECAST.value and runs:
        if read["run"] < min(runs):
            missed = ", ".join(sorted(set(receipt["skipped"]) & set(runs))) or max(runs)
            return f"{head} The agent's fetch could not get run {missed} (not yet published completely when it read), so it read the older run {read['run']}: run again."
        if read["run"] > max(runs):
            return f"{head} Open-Meteo published run {read['run']} between the answer key's fetch and the agent's: run again."
    return f"{head} The date rules chose differently between the two readings: run again."


def data_mismatch(match, receipt):
    """(why the agent's data differs from the pinned data of the same source, whether to run again). Only
    the seasonal outlook can be updated upstream between the two fetches; any other difference, or one in
    a receipt that says it was fetched before the answer key's, means the receipt was altered."""
    difference = first_difference(match, receipt)
    source = receipt["source"]
    if source["kind"] in FINAL:
        message = (
            f"The receipt's data differs from the pinned data of the same source, the {source['label']} "
            f"({difference}); {FINAL[source['kind']]}, so the receipt was altered after get_daily."
        )
        return message, False
    if stamp_seconds(receipt["fetched_at"]) < stamp_seconds(match["fetched_at"]):
        message = (
            f"The receipt's data differs from the pinned data ({difference}), and it says the agent fetched before the "
            "answer key did: the receipt was altered after get_daily."
        )
        return message, False
    message = (
        f"The data the agent read differs from the pinned data of the same request ({difference}). The "
        f"{receipt['source']['label']} can be updated upstream between two fetches: run again."
    )
    return message, True


def pinned_second(reference):
    """When the answer key finished pinning, in UTC seconds: its sources' last fetched_at (its clock's minute
    when none reads as a time), or None. The host starts the task's agent only after the answer key returns,
    so the run of this task made its folder no earlier."""
    fetched = [second for second in (stamp_seconds(candidate.fetched_at) for candidate in reference.candidates) if second is not None]
    clock = stamp_minutes(reference.clock)
    if fetched:
        return max(fetched)
    return None if clock is None else clock * 60


def source_fields(named_data, read):
    """The data source fields the submission gives differently from the receipt, in words."""
    keys = [key for key in ("kind", "model", "run", "step_hours", "today", "label") if named_data.get(key) != read.get(key)]
    return "; ".join(f"{key} {named_data.get(key)!r} instead of {read.get(key)!r}" for key in keys)


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

    # 5-8: the run the submission names, and the data its agent read.
    run = output.run
    marker, problem = run_marker(workspace, run)
    if problem is None and reference is not None:
        pinned = pinned_second(reference)
        if run in reference.prior_runs:
            problem = f"run {run} began before this task (it was in the workspace when the answer key was pinned): submit the report this task's report_alerts returned"
        elif pinned is not None and stamp_seconds(marker["started"]) < pinned:
            problem = (
                f"run {run} began before this task (at {marker['started']}, before the answer key finished pinning the "
                f"data at {seconds_text(pinned)}): submit the report this task's report_alerts returned"
            )
    put("this-run", problem is None, None, run, problem)
    receipt = None
    if holds_("this-run", "task-input"):
        receipt, problem = receipt_of(workspace, date, run)
        put("agent-read", receipt is not None, None, receipt["source"]["label"] if receipt else None, problem)
    else:
        put("agent-read", None)
    match = None
    if receipt is not None and holds_("answer-key-data", "answer-key-rules"):
        found = [candidate for candidate in candidates if key_of(candidate["source"]) == key_of(receipt["source"])]
        message = None
        if not found:
            details["rerun"] = True
            message = source_mismatch(receipt, candidates, stamp_minutes(reference.clock))
        put("agent-source", bool(found), [candidate["source"]["label"] for candidate in candidates], receipt["source"]["label"], message)
        match = found[0] if found else None
    else:
        put("agent-source", None)
    if match is not None:
        identical = match["sha256"] == receipt["sha256"]
        message = None
        if not identical:
            message, rerun = data_mismatch(match, receipt)
            details["rerun"] = details["rerun"] or rerun
        put("agent-data", identical, match["sha256"][:16], receipt["sha256"][:16], message)
    else:
        put("agent-data", None)

    # 9-13: the saved and the submitted report.
    saved = None
    if holds_("this-run", "task-input"):
        saved, problem = saved_report(workspace, date, run)
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
        same = canonical_judgement(saved) == canonical_judgement(entries)
        put("submitted-as-saved", same, None, None, "The submitted report differs from the saved report: submit the report the last report_alerts call returned, as it is.")
    else:
        put("submitted-as-saved", None)
    if receipt is not None:
        named_data = plain_source(output.data)
        put("names-data", named_data == receipt["source"], receipt["source"]["label"], named_data.get("label"), f"The report names other data than the agent read: {source_fields(named_data, receipt['source'])}.")
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

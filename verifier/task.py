"""The daily-alert verdict: 21 constraints, in order, on the answer key, the run the submission names,
the data that run's agent read, the saved and submitted report, and the alerts themselves. The verdict
is valid only when every one holds; any failure among the first 13 (the data and the report's
integrity) scores 0, otherwise the score is the F1 of the reported alert and unavailable cities,
scaled by the share of cities examined.

Every file the verdict reads is the named run's own (reports/<run>/), so two task calls at once in one
workspace are judged apart; the run must have begun after the answer key (it is not among the runs the
answer key saw in the workspace, and its marker's time is not before the answer key's last fetch), so a
report left by an earlier task never passes for this one."""

from .checks import compact, constraint, entries_of, plain_candidate, result, run_marker, summary
from .pin import pin_problems
from .receipt import receipt_of
from .report import compare, saved_report
from .rules import (
    CITY_NAMES,
    DELTA_MINUTES,
    SOURCE_KEYS,
    SOURCE_OF,
    SOURCE_VARIABLES,
    TASK_KEYS,
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
    needed_variables,
    parse_conditions,
    plain_source,
    plain_task,
    report_path,
    seconds_text,
    stamp_minutes,
    stamp_seconds,
    stamp_text,
    stamps_inside,
    task_problem,
    truncate,
)

TEXT = {
    "answer-key": "The answer key pinned data for this date",
    "task-input": "The task is a valid date with 1 to 6 conditions",
    "answer-key-data": "The answer key's data is well formed: each city's local day, its readings and their hash",
    "answer-key-rules": "The answer key used the data the date rules choose",
    "this-run": "The report names this task's own run, begun after the answer key",
    "agent-read": "The agent read the date's data with get_daily",
    "agent-source": "The agent read a source the answer key pinned",
    "agent-data": "The agent's readings equal the answer key's",
    "report-saved": "report_alerts saved the run's report for this task and data",
    "submitted-task": "The submitted report is for this task",
    "submitted-shape": "The submitted report is well formed",
    "submitted-as-saved": "The submitted report equals the saved report",
    "names-data": "The submitted report names the data the agent read",
    "all-examined": "All 30 cities were examined",
    "alerts-complete": "Every city that meets the rules is reported as an alert",
    "no-extra-alerts": "No city is reported as an alert without meeting the rules",
    "values-exact": "Each alert's values are right: the task's units, 1 decimal, ties away from zero",
    "matched-exact": "Each alert lists exactly the conditions that hold",
    "unavailable-complete": "Every city missing a needed reading is reported unavailable",
    "no-extra-unavailable": "No city with all its needed readings is reported unavailable",
    "unavailable-exact": "Each unavailable city names exactly the variables it lacks",
}
ORDER = tuple(TEXT)
GATES = ORDER[:13]  # the data and the report's integrity: any failure scores 0
ALERT_CHECKS = ORDER[14:]  # the alert and unavailable cities, judged against the pinned data
MOST_NAMED = 3  # cities a failure message names
SUBMIT = "Submit the report the last report_alerts call returned, as it is."
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


def changed_fields(given, wanted, keys):
    """The fields ``given`` holds differently from ``wanted``, in words: "match 'all' instead of 'any'"."""
    return "; ".join(f"{key} {given.get(key)!r} instead of {wanted.get(key)!r}" for key in keys if given.get(key) != wanted.get(key))


def given(task, series, step_hours, variable):
    """The value get_daily gives a city for a variable (exact, cut to 9 decimals), as its shortest text."""
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    return canonical_number(truncate(convert(variable, exact_metric(day_metrics(series, step_hours), variable), units)))


def reading(task, series, step_hours, variable):
    """A variable with the value get_daily gives a city for it: "max_temp 91.94"."""
    return f"{variable} {given(task, series, step_hours, variable)}"


def why_not_alert(task, series, step_hours):
    """Why a city that is not an alert is not one: it lacks a reading, or each condition that fails, on
    the value get_daily gives."""
    conditions = parse_conditions(task["conditions"])
    day = day_metrics(series, step_hours)
    lacking = [variable for variable in needed_variables(conditions) if SOURCE_OF[variable] in day.missing]
    if lacking:
        return f"it lacks a reading for {', '.join(lacking)}, so it is unavailable, never an alert"
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    failing = []
    for condition in conditions:
        value = convert(condition.variable, exact_metric(day, condition.variable), units)
        if not holds(value, condition):
            failing.append(f"{condition.variable} {canonical_number(truncate(value))} is not {condition.op} {condition.threshold}")
    rule = "every condition must hold" if task["match"] == "all" else "at least one condition must hold"
    return f"{'; '.join(failing)} (with match {task['match']}, {rule})"


def named(cities):
    shown = ", ".join(cities[:MOST_NAMED])
    return shown + (f" and {len(cities) - MOST_NAMED} more" if len(cities) > MOST_NAMED else "")


def also(words, cities):
    """The sentence naming a failure's other cities, " Also <words>: B, C, D and 2 more.", or "" for one city."""
    return f" Also {words}: {named(cities[1:])}." if len(cities) > 1 else ""


def alert_failures(task, expected, differences, series_of, step_hours):
    """For each alert constraint (15 to 21) that fails: (expected, actual, message). expected and actual
    hold only the cities concerned; the message explains the first of them, showing a value as get_daily
    gave it (exact, cut to 9 decimals) beside its rounded report."""
    alerts = {alert.city: alert for alert in expected.alerts}
    conditions = parse_conditions(task["conditions"])
    condition_of = {condition.text: condition for condition in conditions}
    found = {}
    cities = differences["missing_alerts"]
    if cities:
        first, series = alerts[cities[0]], series_of[cities[0]]
        reasons = "; ".join(f"{reading(task, series, step_hours, condition.variable)} meets {condition.text}" for condition in conditions if condition.text in first.matched)
        report = ", ".join(f"{name} {value}" for name, value in first.values.items())
        message = f"{cities[0]} is an alert but is not reported: {reasons}, so report it with {report}.{also('not reported', cities)}"
        found["alerts-complete"] = (cities, [], message)
    cities = differences["extra_alerts"]
    if cities:
        series = series_of.get(cities[0])
        reason = why_not_alert(task, series, step_hours) if series else f"{cities[0]!r} is not one of the 30 cities"
        found["no-extra-alerts"] = ([], cities, f"{cities[0]} is reported as an alert but is not one: {reason}.{also('reported but not alerts', cities)}")
    wrong = differences["value_mismatches"]
    if wrong:
        cities = sorted(wrong)
        variable, values = next(iter(wrong[cities[0]].items()))
        exact = given(task, series_of[cities[0]], step_hours, variable)
        message = (
            f"{cities[0]} {variable} is reported as {values['reported']}, but get_daily gave {exact}, which is "
            f"{values['expected']} to 1 decimal (ties away from zero).{also('wrong', cities)}"
        )
        found["values-exact"] = (
            {city: {name: pair["expected"] for name, pair in wrong[city].items()} for city in cities},
            {city: {name: pair["reported"] for name, pair in wrong[city].items()} for city in cities},
            message,
        )
    wrong = differences["matched_mismatches"]
    if wrong:
        cities = sorted(wrong)
        both, series = wrong[cities[0]], series_of[cities[0]]
        value = {text: reading(task, series, step_hours, condition.variable) for text, condition in condition_of.items()}
        parts = [f"{text} holds ({value[text]}) but is not listed" for text in both["expected"] if text not in both["reported"]]
        parts += [f"{text} is listed but does not hold ({value[text]})" for text in both["reported"] if text not in both["expected"]]
        found["matched-exact"] = (
            {city: wrong[city]["expected"] for city in cities},
            {city: wrong[city]["reported"] for city in cities},
            f"{cities[0]}'s matched conditions are wrong: {'; '.join(parts)}.{also('wrong', cities)}",
        )
    cities = differences["missing_unavailable"]
    if cities:
        lacks = expected.unavailable[cities[0]]
        source = SOURCE_OF[lacks[0]]
        gap = first_gap(series_of[cities[0]], source, step_hours)
        where = f"no {source} reading at {gap}" if gap else f"a missing {source} reading"
        message = f"{cities[0]} must be reported unavailable (it lacks {', '.join(lacks)}): it has {where}.{also('not reported unavailable', cities)}"
        found["unavailable-complete"] = (cities, [], message)
    cities = differences["extra_unavailable"]
    if cities:
        needed = ", ".join(needed_variables(conditions))
        found["no-extra-unavailable"] = ([], cities, f"{cities[0]} is reported unavailable but has every reading of {needed}.{also('reported unavailable', cities)}")
    wrong = differences["unavailable_mismatches"]
    if wrong:
        cities = sorted(wrong)
        both = wrong[cities[0]]
        message = f"{cities[0]} lacks {', '.join(both['expected'])}, but the report lists {', '.join(both['reported'])}.{also('wrong', cities)}"
        found["unavailable-exact"] = ({city: wrong[city]["expected"] for city in cities}, {city: wrong[city]["reported"] for city in cities}, message)
    return found


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


def saved_problem(saved, task, receipt, where):
    """Why a well-formed saved report does not count for this task and the data the agent read, or None."""
    if canonical_task(saved["task"]) != canonical_task(task):
        return f"The saved report {where} is for another task: {changed_fields(canonical_task(saved['task']), canonical_task(task), TASK_KEYS)}."
    if receipt is None:
        return None
    if saved["source"] != receipt["source"]:
        return f"The saved report {where} judges the {saved['source']['label']}, but get_daily read the {receipt['source']['label']}."
    if saved["sha256"] != receipt["sha256"]:
        return f"The saved report {where} judges other readings (sha256 {saved['sha256'][:16]}…) than get_daily read (sha256 {receipt['sha256'][:16]}…)."
    return None


def judgement_difference(saved, submitted):
    """The first way two judgements (canonical_judgement's alerts and unavailable cities) differ, in words,
    or None when they are the same."""
    for kind, want, got in (("alert", saved[0], submitted[0]), ("unavailable entry", saved[1], submitted[1])):
        for city in sorted(set(want) | set(got)):
            if city not in got:
                return f"it leaves out the saved {kind} for {city}"
            if city not in want:
                return f"it adds an {kind} for {city} that the saved report does not hold"
            if want[city] != got[city]:
                return f"{city}'s {kind} reads {shown_entry(got[city])} in the submission but {shown_entry(want[city])} in the saved report"
    return None


def shown_entry(entry):
    """A canonical alert (values, matched) or unavailable entry (variables) as short text."""
    if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], dict):
        values = ", ".join(f"{name} {value}" for name, value in entry[0].items())
        return f"{values} matching {', '.join(entry[1])}"
    return ", ".join(entry)


def counts(judgement):
    return {"alerts": len(judgement["alerts"]), "unavailable": len(judgement["unavailable"])}


def held(count, expected, label):
    """A valid verdict's message."""
    alerts, unavailable = len(expected.alerts), len(expected.unavailable)
    cities = f"{alerts} of the 30 cities {'is an alert' if alerts == 1 else 'are alerts'}"
    return f"All {count} checks hold: {cities} and {unavailable} {'is' if unavailable == 1 else 'are'} unavailable, judged on the {label}."


def verify_task(arguments, output, reference, workspace):
    """The verdict on a submitted daily-alert report (see the module's docstring)."""
    task = plain_task(arguments)
    date = arguments.date
    checks = {}
    details = {"rerun": False}

    def put(identifier, ok, expected=None, actual=None, message=""):
        checks[identifier] = constraint(identifier, TEXT[identifier], ok, expected, actual, message)

    def skip(identifier, *needs):
        """Not checked: one of the checks it needs did not hold."""
        blocking = [need for need in needs if checks[need]["ok"] is not True]
        checks[identifier] = constraint(identifier, TEXT[identifier], None, message=f"Not checked: {' and '.join(blocking)} did not hold.")

    def hold(*identifiers):
        return all(checks[identifier]["ok"] is True for identifier in identifiers)

    # 1-4: the answer key.
    if reference is None:
        put("answer-key", False, date, None, f"No answer key for {date}: pin_alerts never fetched the day.")
    else:
        put("answer-key", reference.date == date, date, reference.date, f"The answer key is for {reference.date}, not {date}.")
    problem = task_problem(task)
    put("task-input", problem is None, None, None, problem)
    candidates = []
    if hold("answer-key", "task-input"):
        shape, rules = pin_problems(task, reference)
        put("answer-key-data", shape is None, None, None, shape)
        if shape is None:
            put("answer-key-rules", rules is None, None, None, rules)
            candidates = [plain_candidate(candidate) for candidate in reference.candidates]
        else:
            skip("answer-key-rules", "answer-key-data")
    else:
        skip("answer-key-data", "answer-key", "task-input")
        skip("answer-key-rules", "answer-key", "task-input")

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
    if hold("this-run", "task-input"):
        receipt, problem = receipt_of(workspace, date, run)
        put("agent-read", receipt is not None, None, receipt["source"]["label"] if receipt else None, problem)
    else:
        skip("agent-read", "this-run", "task-input")
    match = None
    if receipt is not None and hold("answer-key-data", "answer-key-rules"):
        found = [candidate for candidate in candidates if key_of(candidate["source"]) == key_of(receipt["source"])]
        message = ""
        if not found:
            details["rerun"] = True
            message = source_mismatch(receipt, candidates, stamp_minutes(reference.clock))
        put("agent-source", bool(found), [candidate["source"]["label"] for candidate in candidates], receipt["source"]["label"], message)
        match = found[0] if found else None
    else:
        skip("agent-source", "agent-read", "answer-key-data", "answer-key-rules")
    if match is not None:
        identical = match["sha256"] == receipt["sha256"]
        message = ""
        if not identical:
            message, rerun = data_mismatch(match, receipt)
            details["rerun"] = details["rerun"] or rerun
        put("agent-data", identical, f"sha256 {match['sha256'][:16]}…", f"sha256 {receipt['sha256'][:16]}…", message)
    else:
        skip("agent-data", "agent-source")

    # 9-13: the saved and the submitted report.
    saved = None
    if hold("this-run", "task-input"):
        saved, problem = saved_report(workspace, date, run)
        if saved is not None:
            problem = saved_problem(saved, task, receipt, report_path(date, run))
            saved = None if problem else saved
        put("report-saved", saved is not None, None, None, problem)
    else:
        skip("report-saved", "this-run", "task-input")
    submitted_task = plain_task(output.task)
    entries = entries_of(output)
    if hold("task-input"):
        problem = task_problem(submitted_task)
        if problem:
            put("submitted-task", False, None, None, f"The submitted report's task is malformed: {problem}. {SUBMIT}")
        else:
            wanted, got = canonical_task(task), canonical_task(submitted_task)
            keys = [key for key in TASK_KEYS if got[key] != wanted[key]]
            message = f"The submitted report is for another task: {changed_fields(got, wanted, keys)}. {SUBMIT}"
            put("submitted-task", not keys, {key: wanted[key] for key in keys} or None, {key: got[key] for key in keys} or None, message)
        if hold("submitted-task"):
            problem = judgement_problem(task, None, entries["alerts"], entries["unavailable"])
            put("submitted-shape", problem is None, None, None, f"The submitted report is malformed: {problem}")
        else:
            skip("submitted-shape", "submitted-task")
    else:
        skip("submitted-task", "task-input")
        skip("submitted-shape", "task-input")
    if saved is not None and hold("submitted-task", "submitted-shape"):
        difference = judgement_difference(canonical_judgement(saved), canonical_judgement(entries))
        message = f"The submitted report is not the saved report {report_path(date, run)}: {difference}. {SUBMIT}"
        put("submitted-as-saved", difference is None, counts(saved), counts(entries), message)
    else:
        skip("submitted-as-saved", "report-saved", "submitted-task", "submitted-shape")
    if receipt is not None:
        named_data = plain_source(output.data)
        if named_data == receipt["source"]:
            put("names-data", True, receipt["source"]["label"], named_data["label"])
        else:
            keys = [key for key in SOURCE_KEYS if named_data.get(key) != receipt["source"].get(key)]
            message = f"The submitted report names other data than the agent read: {changed_fields(named_data, receipt['source'], keys)}."
            put("names-data", False, {key: receipt["source"].get(key) for key in keys}, {key: named_data.get(key) for key in keys}, message)
    else:
        skip("names-data", "agent-read")

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
            worth, _ = compare(diagnostic, entries)
            details["agent_data_check"] = "against the data the agent read, the report is exact" if worth == 1.0 else f"against the data the agent read, the report scores F1 {worth:.2f}"
    coverage = None
    if saved is not None:
        examined = set(saved["examined"])
        cities = set(CITY_NAMES)
        coverage = len(examined & cities) / len(examined | cities)
        unexamined = sorted(cities - examined)
        message = f"{len(unexamined)} of the 30 cities {'was' if len(unexamined) == 1 else 'were'} never examined ({named(unexamined)}): report_alerts must judge every city."
        put("all-examined", not unexamined, len(cities), len(examined & cities), message)
    else:
        skip("all-examined", "report-saved")
    score = None
    if expected is not None and checks["submitted-shape"]["ok"] is True:
        score, differences = compare(expected, entries)
        failures = alert_failures(task, expected, differences, series_of, step)
        for identifier in ALERT_CHECKS:
            put(identifier, identifier not in failures, *failures.get(identifier, (None, None, "")))
    else:
        for identifier in ALERT_CHECKS:
            skip(identifier, "agent-data", "submitted-shape")

    constraints = [checks[identifier] for identifier in ORDER]
    valid = all(entry["ok"] is True for entry in constraints)
    if any(checks[identifier]["ok"] is not True for identifier in GATES) or score is None:
        final = 0.0
    else:
        final = score * (coverage if coverage is not None else 0.0)
    source = (match or receipt or {}).get("source")
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
        return result(True, held(len(constraints), expected, source["label"]), score=1.0, **details)
    return result(False, summary(constraints), score=min(final, 0.99), **details)

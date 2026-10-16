"""pin_alerts, the daily-alert answer key: its input, and the data it pins, checked by hand."""

from .checks import plain_candidate, result
from .rules import (
    DELTA_MINUTES,
    RUN_TOKEN,
    SHA256,
    choice_problem,
    cities_problem,
    date_text,
    expected_from_series,
    key_of,
    plain_entries,
    plain_task,
    series_sha256,
    source_problem,
    source_request,
    stamp_minutes,
    task_problem,
)


def verify_input(value):
    problem = task_problem(plain_task(value))
    return result(False, problem) if problem else result(True, "input is valid")


def candidate_shape_problem(task, candidate):
    """What is wrong with one pinned source's data (a plain candidate), or None: its source and clock,
    every city's local day and readings, its hash, and its own alerts, re-derived from its readings."""
    source = candidate["source"]
    problem = source_problem(source)
    if problem:
        return problem
    clock = stamp_minutes(candidate["clock"])
    if clock is None:
        return "a pinned source's clock must be a UTC time"
    if source["today"] != date_text(clock // 1440):
        return f"a pinned source's today, {source['today']}, is not its clock's date"
    if not isinstance(candidate["sha256"], str) or not SHA256.fullmatch(candidate["sha256"]):
        return "a pinned source must carry the sha256 of its data"
    if candidate["request"] != source_request(task["date"], key_of(source)):
        return f"the pinned {source['label']} was not fetched with the request that fetches it for {task['date']}"
    problem = cities_problem(task["date"], source["step_hours"], candidate["cities"])
    if problem:
        return problem
    if series_sha256(task["date"], key_of(source), candidate["cities"]) != candidate["sha256"]:
        return f"the pinned {source['label']} does not match its own sha256"
    expected = plain_entries(expected_from_series(task, source["step_hours"], candidate["cities"]))
    if {"alerts": candidate["alerts"], "unavailable": candidate["unavailable"]} != expected:
        return f"the alerts pinned with the {source['label']} disagree with its readings"
    return None


def candidate_rules_problem(task, candidate):
    """Whether one pinned source is the data the date rules choose at its clock, or None."""
    source = candidate["source"]
    problem = choice_problem(task["date"], stamp_minutes(candidate["clock"]), key_of(source), candidate["skipped"])
    return f"the pinned {source['label']} breaks the date rules: {problem}" if problem else None


def pin_problems(task, pin):
    """(what is wrong with the pinned data's shape, what is wrong with its choice of data), each None
    when nothing is: one or two candidates with distinct sources, read at the pin's clock and
    DELTA_MINUTES before it, each well formed and the data the rules choose at its reading."""
    if pin.date != task["date"]:
        return "the answer key is for another date", None
    clock = stamp_minutes(pin.clock)
    if clock is None:
        return "the answer key's clock must be a UTC time", None
    if list(pin.prior_runs) != sorted(set(pin.prior_runs)) or not all(RUN_TOKEN.fullmatch(run) for run in pin.prior_runs):
        return "the answer key's prior runs must be distinct run tokens, sorted", None
    candidates = [plain_candidate(candidate) for candidate in pin.candidates]
    if not 1 <= len(candidates) <= 2:
        return "the answer key must pin one or two sources", None
    if len({tuple(key_of(candidate["source"])) for candidate in candidates}) != len(candidates):
        return "the answer key pins the same source twice", None
    readings = {stamp_minutes(candidate["clock"]) for candidate in candidates}
    if not readings <= {clock - DELTA_MINUTES, clock} or len(readings) != len(candidates):
        return f"each pinned source must be read at the answer key's clock or {DELTA_MINUTES} minutes before it", None
    for candidate in candidates:
        problem = candidate_shape_problem(task, candidate)
        if problem:
            return problem, None
    for candidate in candidates:
        problem = candidate_rules_problem(task, candidate)
        if problem:
            return None, problem
    return None, None


def verify_output(input_value, output_value, workspace):
    task = plain_task(input_value)
    shape, rules = pin_problems(task, output_value)
    if shape or rules:
        return result(False, shape or rules)
    return result(
        True,
        "the data is pinned",
        sources=[candidate.source.label for candidate in output_value.candidates],
        alerts=[len(candidate.alerts) for candidate in output_value.candidates],
    )

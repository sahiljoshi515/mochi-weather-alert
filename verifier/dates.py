"""check_date and the check-date task: today's UTC date from the clock, and which data a daily-alert task
on a date would use, re-derived by hand from the clock reading the answer carries."""

from .checks import constraint, result
from .rules import (
    DATE,
    choose,
    date_problem,
    date_text,
    month_length,
    plain,
    source_label,
    stamp_minutes,
    weekday,
    windows_of,
)

WINDOW_KEYS = ("archive_first", "archive_last", "forecast_first", "forecast_last", "outlook_first", "outlook_last")


def calendar_problem(text):
    """A check-date input must be today or a calendar day YYYY-MM-DD (any year: the answer says which work)."""
    if not isinstance(text, str):
        return "date must be YYYY-MM-DD or today"
    if text.strip().lower() == "today":
        return None
    found = DATE.fullmatch(text)
    if not found:
        return f"date must be YYYY-MM-DD or today; got {text!r}"
    year, month, day = (int(part) for part in found.groups())
    if not 1 <= month <= 12 or not 1 <= day <= month_length(year, month):
        return f"{text} is not a calendar day"
    return None


def verify_input(value):
    problem = calendar_problem(value.date)
    return result(False, problem) if problem else result(True, "input is valid")


def expected_answer(asked, clock):
    """What check_date answers for ``asked`` at ``clock`` (UTC minutes)."""
    today = date_text(clock // 1440)
    date = today if asked.strip().lower() == "today" else asked
    problem = date_problem(date)
    calendar_day = problem is None or problem.startswith(f"{date} is before")
    choice = None if problem else choose(date, clock)
    if choice is not None and choice.problem:
        problem = choice.problem
    if problem:
        kind, source, message = None, problem, f"{date} cannot be judged: {problem}"
    else:
        kind = choice.kind
        source = source_label(choice.kind, choice.model, choice.runs[0] if choice.runs else "", choice.step_hours)
        message = f"Today is {today} (UTC). {date} is a {weekday(date)}; a daily-alert task on it uses the {source}."
    return {
        "today": today,
        "date": date,
        "weekday": weekday(date) if calendar_day else "",
        "kind": kind,
        "source": source,
        "message": message,
        **windows_of(clock // 1440),
    }


def answer_constraints(asked, output):
    """The check-date answer's constraints, in order."""
    clock = stamp_minutes(output.clock)
    if clock is None:
        first = constraint("clock", "The answer carries a UTC clock reading", False, "YYYY-MM-DDTHH:MM", output.clock, f"the clock {output.clock!r} is not a UTC time")
        return [first] + [constraint(identifier, text, None) for identifier, text in REST]
    expected = expected_answer(asked, clock)
    actual = {
        "today": output.today,
        "date": output.date,
        "weekday": output.weekday,
        "kind": plain(output.kind) if output.kind is not None else None,
        "source": output.source,
        "message": output.message,
        **{key: range_field(output, key) for key in WINDOW_KEYS},
    }
    found = [constraint("clock", "The answer carries a UTC clock reading", True, None, output.clock)]
    for identifier, text in REST:
        keys = FIELDS[identifier]
        want = {key: expected[key] for key in keys}
        got = {key: actual[key] for key in keys}
        ok = want == got
        found.append(constraint(identifier, text, ok, want, got, None if ok else f"{text}: expected {want}, got {got}"))
    return found


def range_field(output, key):
    """One of a DateOutput's date range fields, by name."""
    return {
        "archive_first": output.archive_first,
        "archive_last": output.archive_last,
        "forecast_first": output.forecast_first,
        "forecast_last": output.forecast_last,
        "outlook_first": output.outlook_first,
        "outlook_last": output.outlook_last,
    }[key]


REST = (
    ("today", "Today is the clock's UTC date"),
    ("date", "The date is the one asked about (today's for today), with its weekday"),
    ("ranges", "Each kind of data's date range follows the rules at the clock"),
    ("kind", "The date's data follows the rules at the clock"),
    ("message", "The answer says so in one sentence"),
)
FIELDS = {
    "today": ("today",),
    "date": ("date", "weekday"),
    "ranges": WINDOW_KEYS,
    "kind": ("kind", "source"),
    "message": ("message",),
}


def verify_output(input_value, output_value, workspace):
    failed = [entry for entry in answer_constraints(input_value.date, output_value) if entry["ok"] is False]
    return result(False, failed[0]["message"]) if failed else result(True, "the answer follows the clock")


def verify_task(arguments, output, reference, workspace):
    constraints = answer_constraints(arguments.date, output)
    failed = [entry for entry in constraints if entry["ok"] is not True]
    held = len(constraints) - len(failed)
    if failed:
        first = next((entry for entry in failed if entry["ok"] is False), failed[0])
        message = f"{len(failed)} of {len(constraints)} checks failed. First: {first.get('message', first['constraint'])}"
        return result(False, message, score=held / len(constraints) * 0.99, constraints=constraints)
    return result(True, f"All {len(constraints)} checks hold: {output.message}", score=1.0, constraints=constraints)

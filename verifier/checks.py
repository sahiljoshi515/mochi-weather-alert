"""Shared verdict helpers: a result, the ordered constraint list every task verdict carries, and plain
forms of the contract instances the rules work on. Pure functions of their arguments and files."""

import json
import re
from pathlib import Path

from .rules import RUN_TOKEN, SOURCE_VARIABLES, plain_source, run_folder, shown, stamp_seconds

RUN_MARKER = "run.json"
MOST = 10  # list entries (or keys) a constraint's expected or actual shows
MAX_FILE_BYTES = 4_000_000  # far above any file a run writes (a receipt is about 40 KB)
# What reading a file the agent's folder holds could raise if a check let an odd value through: a last
# resort that turns it into a verdict naming the file (every field is checked before it is used).
UNREADABLE = (ArithmeticError, LookupError, TypeError, ValueError, RecursionError)
PLAIN_WORD = re.compile(r"[a-z]+ ")  # a first word that is a plain word: no path, snake_case name, number or quote


def result(valid, message, *, score=None, **details):
    return {
        "valid": valid,
        "score": float(valid) if score is None else score,
        "message": message,
        "details": details,
    }


def compact(value):
    """A value small enough for a verdict: lists cut to MOST entries, mappings to MOST keys."""
    if isinstance(value, list) and len(value) > MOST:
        return value[:MOST] + [f"… {len(value) - MOST} more"]
    if isinstance(value, dict) and len(value) > MOST:
        return {**dict(list(value.items())[:MOST]), "…": f"{len(value) - MOST} more"}
    return value


def sentence(text):
    """``text`` with a capital first letter when it starts with a plain word ("the receipt ..."); a path, a
    snake_case name, a number or a quote at its start stays as it is."""
    return text[0].upper() + text[1:] if PLAIN_WORD.match(text) else text


def constraint(identifier, text, ok, expected=None, actual=None, message=""):
    """One constraint's entry, always with the same keys: ok is True (holds), False (fails) or None (not
    checked: a check it needs did not hold); expected and actual where they say something, else None;
    and message, why it fails or why it was not checked ("" when it holds)."""
    return {
        "id": identifier,
        "constraint": text,
        "ok": ok,
        "expected": compact(expected),
        "actual": compact(actual),
        "message": "" if ok is True else sentence(message or text),
    }


def summary(constraints):
    """A failed verdict's message: the first failing constraint (or, when none fails, the first not
    checked), its id and why, then how many more fail: "values-exact: Auckland max_temp ... (+1 more)"."""
    failed = [entry for entry in constraints if entry["ok"] is False]
    first = failed[0] if failed else next(entry for entry in constraints if entry["ok"] is not True)
    reason = first["message"].removesuffix(".")
    return f"{first['id']}: {reason}" + (f" (+{len(failed) - 1} more)" if len(failed) > 1 else "")


def read_json(path):
    """(the file's JSON, None) or (None, why it cannot be read)."""
    path = Path(path)
    if not path.is_file():
        return None, "missing"
    if path.stat().st_size > MAX_FILE_BYTES:
        return None, "too large"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (ValueError, RecursionError):
        return None, "not JSON"


def run_marker(workspace, run):
    """(run ``run``'s marker {run, started}, None) or (None, what is wrong): the token's form (checked before
    it names any path), and its folder's marker naming it, with the UTC second the run made its folder."""
    if not isinstance(run, str) or not RUN_TOKEN.fullmatch(run):
        return None, f"{shown(run)} is not a run token (16 hex digits): submit the report that report_alerts returned"
    where = f"{run_folder(run)}/{RUN_MARKER}"
    found, problem = read_json(Path(workspace) / where)
    if problem == "missing":
        return None, f"this workspace has no run {run} (no {where}): submit the report that report_alerts returned"
    if problem:
        return None, f"{where} is not a run marker: {problem}"
    if not isinstance(found, dict) or set(found) != {"run", "started"} or found["run"] != run:
        return None, f"{where} is not the marker of run {run}: it must hold run and started"
    if stamp_seconds(found["started"]) is None:
        return None, f"{where}: started must be a UTC time YYYY-MM-DDTHH:MM:SSZ, not {shown(found['started'])}"
    return found, None


def run_problem(workspace, run):
    """What is wrong with ``run`` as a run of this workspace, or None (see run_marker)."""
    return run_marker(workspace, run)[1]


def plain_city(city):
    """A contract PinnedCity as the plain series the rules read."""
    return {
        "city": city.city,
        "timezone": city.timezone,
        "start": city.start,
        "end": city.end,
        "start_offset": city.start_offset,
        "end_offset": city.end_offset,
        "grid": list(city.grid),
        "times": list(city.times),
        **{variable: list(readings_of(city, variable)) for variable in SOURCE_VARIABLES},
    }


def readings_of(city, variable):
    """A PinnedCity's readings of one source variable."""
    if variable == "temperature_2m":
        return city.temperature_2m
    if variable == "relative_humidity_2m":
        return city.relative_humidity_2m
    if variable == "precipitation":
        return city.precipitation
    return city.wind_speed_10m


def plain_candidate(candidate):
    """A contract PinnedSource as plain JSON."""
    return {
        "source": plain_source(candidate.source),
        "clock": candidate.clock,
        "fetched_at": candidate.fetched_at,
        "request": candidate.request,
        "skipped": list(candidate.skipped),
        "sha256": candidate.sha256,
        "cities": [plain_city(city) for city in candidate.cities],
        "alerts": [{"city": a.city, "values": dict(a.values), "matched": list(a.matched)} for a in candidate.alerts],
        "unavailable": [{"city": u.city, "variables": list(u.variables)} for u in candidate.unavailable],
    }


def entries_of(value):
    """The alerts and unavailable cities of a contract ReportInput or AlertReportOutput, as JSON."""
    return {
        "alerts": [{"city": a.city, "values": dict(a.values), "matched": list(a.matched)} for a in value.alerts],
        "unavailable": [{"city": u.city, "variables": list(u.variables)} for u in value.unavailable],
    }


def entries_by_city(judgement):
    return {entry["city"]: entry for entry in judgement["alerts"]}, {entry["city"]: entry for entry in judgement["unavailable"]}

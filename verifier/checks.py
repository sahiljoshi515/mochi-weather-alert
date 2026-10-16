"""Shared verdict helpers: a result, the ordered constraint list every task verdict carries, and plain
forms of the contract instances the rules work on. Pure functions of their arguments and files."""

import json
from pathlib import Path

from .rules import RUN_TOKEN, SOURCE_VARIABLES, plain_source, run_folder

RUN_MARKER = "run.json"
MOST = 10  # list entries a constraint's expected or actual shows


def result(valid, message, *, score=None, **details):
    return {
        "valid": valid,
        "score": float(valid) if score is None else score,
        "message": message,
        "details": details,
    }


def compact(value):
    """A value small enough for a verdict: lists cut to MOST entries."""
    if isinstance(value, list) and len(value) > MOST:
        return value[:MOST] + [f"… {len(value) - MOST} more"]
    return value


def constraint(identifier, text, ok, expected=None, actual=None, message=None):
    """One constraint's entry: ok is True (holds), False (fails) or None (not checked: an earlier one it
    depends on failed), with what was expected and found when they say something. A failed one says
    why in plain words."""
    entry = {"id": identifier, "constraint": text, "ok": ok}
    if expected is not None:
        entry["expected"] = compact(expected)
    if actual is not None:
        entry["actual"] = compact(actual)
    if ok is False and message:
        entry["message"] = message
    return entry


def read_json(path):
    """(the file's JSON, None) or (None, why it cannot be read)."""
    path = Path(path)
    if not path.is_file():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except ValueError:
        return None, "not JSON"


def run_problem(workspace, run):
    """What is wrong with ``run`` as a run of this workspace, or None: its token's form (checked before
    it names any path) and its folder's marker."""
    if not isinstance(run, str) or not RUN_TOKEN.fullmatch(run):
        return f"{run!r} is not a run token (16 hex digits): submit the report report_alerts returned"
    found, problem = read_json(Path(workspace) / run_folder(run) / RUN_MARKER)
    if problem or not isinstance(found, dict) or found.get("run") != run:
        return f"this workspace has no run {run}: submit the report report_alerts returned"
    return None


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

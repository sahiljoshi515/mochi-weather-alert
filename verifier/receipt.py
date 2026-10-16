"""The receipt get_daily leaves of the data a run's agent read: reports/<run>/<date>.source.json."""

from pathlib import Path

from .checks import UNREADABLE, read_json, run_problem
from .rules import (
    SHA256,
    choice_problem,
    cities_problem,
    date_text,
    key_of,
    receipt_path,
    series_sha256,
    shown,
    source_problem,
    source_request,
    stamp_minutes,
    stamp_seconds,
)

RECEIPT_KEYS = ("run", "date", "source", "clock", "fetched_at", "request", "skipped", "sha256", "cities")


def altered(where):
    return f"the receipt {where} was altered after get_daily"


def receipt_of(workspace, date, run):
    """(the receipt of the data run ``run``'s agent read for ``date``, None) or (None, why there is none
    that counts): written by get_daily in that run, the data the date rules choose at its clock, its
    readings matching its own request and hash. A problem names the file and the field."""
    problem = run_problem(workspace, run)
    if problem:
        return None, problem
    where = receipt_path(date, run)
    try:
        return checked_receipt(workspace, date, run, where)
    except UNREADABLE as error:  # a last resort: every field is checked before it is used
        return None, f"{altered(where)}: it cannot be read ({str(error)[:80]})"


def checked_receipt(workspace, date, run, where):
    found, problem = read_json(Path(workspace) / where)
    if problem == "missing":
        return None, f"the agent never read {date}'s data with get_daily in run {run} (no {where})"
    if problem:
        return None, f"{where} is not a receipt: {problem}"
    if not isinstance(found, dict) or set(found) != set(RECEIPT_KEYS):
        return None, f"{where} is not a receipt get_daily wrote: it must hold {', '.join(RECEIPT_KEYS)}"
    if found["run"] != run or found["date"] != date:
        return None, f"{altered(where)}: it names run {shown(found['run'])} and date {shown(found['date'])}"
    problem = source_problem(found["source"])
    if problem:
        return None, f"{altered(where)}: source: {problem}"
    clock = stamp_minutes(found["clock"])
    if clock is None or found["source"]["today"] != date_text(clock // 1440):
        return None, f"{altered(where)}: clock must be a UTC time YYYY-MM-DDTHH:MM on the source's today"
    fetched = stamp_seconds(found["fetched_at"])
    if fetched is None or fetched < clock * 60:
        return None, f"{altered(where)}: fetched_at must be a UTC time YYYY-MM-DDTHH:MM:SSZ, at or after its clock"
    if not isinstance(found["skipped"], list) or not all(isinstance(entry, str) for entry in found["skipped"]):
        return None, f"{altered(where)}: skipped must list runs"
    problem = choice_problem(date, clock, key_of(found["source"]), found["skipped"])
    if problem:
        return None, f"the data the agent read breaks the date rules: {problem}"
    if found["request"] != source_request(date, key_of(found["source"])):
        return None, f"{altered(where)}: its request is not the one that fetches the {found['source']['label']} for {date}"
    problem = cities_problem(date, found["source"]["step_hours"], found["cities"])
    if problem:
        return None, f"{altered(where)}: {problem}"
    if not isinstance(found["sha256"], str) or not SHA256.fullmatch(found["sha256"]):
        return None, f"{altered(where)}: sha256 must name the data"
    if series_sha256(date, key_of(found["source"]), found["cities"]) != found["sha256"]:
        return None, f"{altered(where)}: its readings no longer match its own sha256"
    return found, None

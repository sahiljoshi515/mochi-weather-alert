"""The receipt get_daily leaves of the data this run's agent read: reports/<date>.source.json."""

from pathlib import Path

from .checks import REPORT_DIR, read_json, run_of
from .rules import (
    SHA256,
    choice_problem,
    cities_problem,
    date_text,
    key_of,
    series_sha256,
    source_problem,
    stamp_minutes,
)

RECEIPT_KEYS = ("run", "date", "source", "clock", "fetched_at", "request", "skipped", "sha256", "cities")


def receipt_path(date):
    return f"{REPORT_DIR}/{date}.source.json"


def receipt_of(workspace, date):
    """(the receipt of the data this run's agent read for ``date``, None) or (None, why there is none
    that counts): written by get_daily in this run, the data the date rules choose at its clock,
    well formed, and matching its own hash."""
    where = receipt_path(date)
    found, problem = read_json(Path(workspace) / where)
    if problem == "missing":
        return None, f"the agent never read {date}'s data with get_daily (no {where})"
    if problem:
        return None, f"{where} is not a receipt: {problem}"
    if not isinstance(found, dict) or set(found) != set(RECEIPT_KEYS):
        return None, f"{where} is not a receipt get_daily wrote"
    run = run_of(workspace)
    if run is None:
        return None, "no run marker in the workspace: the plugin did not start here"
    if found["run"] != run:
        return None, f"{where} was left by an earlier run: the agent never read {date}'s data in this run"
    if found["date"] != date:
        return None, f"{where} is for another date"
    problem = source_problem(found["source"])
    if problem:
        return None, f"{where}: {problem}"
    clock = stamp_minutes(found["clock"])
    if clock is None or found["source"]["today"] != date_text(clock // 1440):
        return None, f"{where}: its clock and today disagree"
    if not isinstance(found["skipped"], list) or not all(isinstance(run, str) for run in found["skipped"]):
        return None, f"{where}: skipped must list runs"
    problem = choice_problem(date, clock, key_of(found["source"]), found["skipped"])
    if problem:
        return None, f"the data the agent read breaks the date rules: {problem}"
    problem = cities_problem(date, found["source"]["step_hours"], found["cities"])
    if problem:
        return None, f"{where}: {problem}"
    if not isinstance(found["sha256"], str) or not SHA256.fullmatch(found["sha256"]):
        return None, f"{where}: sha256 must name the data"
    if series_sha256(date, key_of(found["source"]), found["cities"]) != found["sha256"]:
        return None, f"{where} does not match its own sha256: it was changed after get_daily wrote it"
    return found, None

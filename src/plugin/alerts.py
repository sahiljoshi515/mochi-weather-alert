"""The tools: the answer key, a day's values for the agent (with the receipt of the data it read), the
merged report, and the date check.

Each run (this plugin process: one task call's agent) keeps its files in its own folder,
reports/<run>/, so two task calls at once in one workspace never touch each other's files: the
receipt of the data read (<date>.source.json), the report (<date>.json) and the call log (calls.log).
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from slimconfig import build_output, plain_output

from contract import (
    Alert,
    AlertInput,
    AlertReportOutput,
    DailyCity,
    DailyInput,
    DailyOutput,
    DataKind,
    DataSource,
    DateInput,
    DateOutput,
    PinnedCity,
    PinnedSource,
    PinOutput,
    ReportInput,
    ReportOutput,
    Unavailable,
)
from plugin import sources
from plugin.rules import (
    CITY_NAMES,
    MAX_CITIES_PER_CALL,
    MAX_REPORT_ENTRIES,
    REPORT_DIR,
    RUN_TOKEN,
    Units,
    canonical_task,
    date_problem,
    date_text,
    day_metrics,
    expected_from_series,
    given_values,
    judgement_problem,
    parse_conditions,
    plain_task,
    receipt_path,
    report_path,
    report_problem,
    run_folder,
    source_label,
    stamp_text,
    task_problem,
    usable,
    weekday,
    windows_of,
)

RUN_MARKER = "run.json"
# Every tool call of the run, one JSON line each, kept short: what a run did when its agent went wrong.
CALL_LOG = "calls.log"
CALL_LOG_LINES = 300
# Pruning keeps the member's folder from growing with every run (the site stops saving it back past 5,000
# entries). Removing a folder can collide with a concurrent task call's staging copy on the host, so it is
# rare: only once more than PRUNE_ABOVE_RUNS run folders exist, only folders older than PRUNE_AFTER_SECONDS
# (all of them at once, so the next pruning is about PRUNE_ABOVE_RUNS runs away), at most once per process.
PRUNE_ABOVE_RUNS = 200
PRUNE_AFTER_SECONDS = 24 * 3600
PRUNED = {"done": False}
# This plugin process's run: its folder's name, carried by every receipt and report it writes and by
# the submitted report, so the verifier judges exactly this run's files.
RUN = secrets.token_hex(8)
LOCK = threading.Lock()
# Writes whose temporary file had to go beside the target (the call log notes how many), and whether the
# launch's TMPDIR is on another volume than the workspace, so that every write must.
WRITES = {"beside": 0, "cross_device": False}


def checked_task(given: AlertInput) -> dict:
    task = plain_task(given)
    problem = task_problem(task)
    if problem:
        raise ValueError(problem)
    return task


def checked_cities(names: list[str], most: int) -> None:
    if not 1 <= len(names) <= most or len(set(names)) != len(names):
        raise ValueError(f"cities must be 1 to {most} distinct names")
    unknown = [name for name in names if name not in CITY_NAMES]
    if unknown:
        raise ValueError(f"unknown city {', '.join(unknown)}; the cities are {', '.join(CITY_NAMES)}")


def data_source(found: sources.Fetched) -> DataSource:
    return build_output(found.source(), DataSource)


def pinned_source(task: dict, found: sources.Fetched) -> PinnedSource:
    expected = expected_from_series(task, found.key[3], list(found.cities))
    return PinnedSource(
        source=data_source(found),
        clock=stamp_text(found.clock),
        fetched_at=found.fetched_at,
        request=found.request,
        skipped=list(found.skipped),
        sha256=found.sha256,
        cities=[build_output(series, PinnedCity) for series in found.cities],
        alerts=[
            Alert(city=alert.city, values={k: float(v) for k, v in alert.values.items()}, matched=list(alert.matched))
            for alert in expected.alerts
        ],
        unavailable=[Unavailable(city=city, variables=list(variables)) for city, variables in expected.unavailable.items()],
    )


def runs_in(workspace: Path) -> list[str]:
    """The runs whose folders are in the workspace's reports/."""
    folder = workspace.resolve() / REPORT_DIR
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir() if path.is_dir() and RUN_TOKEN.fullmatch(path.name))


def pin_alerts(given: AlertInput, workspace: Path) -> PinOutput:
    """The answer key: the data the date rules choose at the clock and DELTA_MINUTES before it (one
    source, or two when they differ), every city's local day in it, the alerts the rules give, and the
    runs already in the workspace (this task's run starts after the answer key, so it is none of them)."""
    task = checked_task(given)
    clock, found = sources.pin(task["date"])
    prior = [run for run in runs_in(workspace) if run != RUN]
    return PinOutput(date=task["date"], clock=stamp_text(clock), candidates=[pinned_source(task, one) for one in found], prior_runs=prior)


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` in one rename, so a reader never sees a half-written file. The temporary
    file is written in the launch's own TMPDIR and renamed into place, so the workspace never holds one: a host
    copying the workspace at that moment (to stage another task call) would trip over a file that vanishes.
    When that fails (TMPDIR on another volume), the temporary file goes beside ``path`` instead."""
    if not WRITES["cross_device"] and replaced_from_tmpdir(path, text):
        return
    WRITES["beside"] += 1
    temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def replaced_from_tmpdir(path: Path, text: str) -> bool:
    """Whether ``text`` went to ``path`` through a temporary file in the launch's TMPDIR and one rename. A
    rename across volumes fails every time, so after one the process stops trying."""
    try:
        handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp")
    except OSError:
        return False
    temp = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)
        temp.chmod(0o644)
        os.replace(temp, path)
    except OSError as error:
        temp.unlink(missing_ok=True)
        WRITES["cross_device"] = WRITES["cross_device"] or error.errno == errno.EXDEV
        return False
    return True


def started_of(folder: Path) -> float:
    """When a run folder's run began (epoch seconds), from its marker, else the folder's mtime."""
    try:
        found = json.loads((folder / RUN_MARKER).read_text(encoding="utf-8"))
        return datetime.fromisoformat(found["started"]).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        try:
            return folder.stat().st_mtime
        except OSError:
            return sources.now().timestamp()


def remove_folder(folder: Path) -> None:
    """Remove a run folder, first renamed out of the workspace in one step (into the launch's TMPDIR), so a
    host copying the workspace never finds it half removed; removed in place when it cannot be moved."""
    try:
        holder = Path(tempfile.mkdtemp(prefix="weather-alert-pruned-"))
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        return
    try:
        os.replace(folder, holder / folder.name)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(holder, ignore_errors=True)


def prune(workspace: Path) -> None:
    """When more than PRUNE_ABOVE_RUNS run folders exist, remove every other run's folder older than
    PRUNE_AFTER_SECONDS. Checked at most once per process."""
    if PRUNED["done"]:
        return
    PRUNED["done"] = True
    runs = runs_in(workspace)
    if len(runs) <= PRUNE_ABOVE_RUNS:
        return
    base = workspace.resolve() / REPORT_DIR
    now = sources.now().timestamp()
    for run in runs:
        if run != RUN and now - started_of(base / run) > PRUNE_AFTER_SECONDS:
            remove_folder(base / run)


def ensure_run_folder(workspace: Path) -> Path:
    """This run's folder, reports/<run>/, made on first use with its marker: the run's token and when it
    began, by the clock the answer key reads (pruning old runs then, rarely: see prune). The run's first tool
    call makes it, after the answer key has pinned the data, so the verifier holds the run to have begun no
    earlier."""
    folder = workspace.resolve() / run_folder(RUN)
    if not (folder / RUN_MARKER).is_file():
        folder.mkdir(parents=True, exist_ok=True)
        started = sources.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        write_atomic(folder / RUN_MARKER, json.dumps({"run": RUN, "started": started}, indent=2) + "\n")
        prune(workspace)
    return folder


def write_receipt(workspace: Path, found: sources.Fetched) -> None:
    """Record the data this run's agent read, for the verifier to match against the answer key."""
    receipt = {
        "run": RUN,
        "date": found.date,
        "source": found.source(),
        "clock": stamp_text(found.clock),
        "fetched_at": found.fetched_at,
        "request": found.request,
        "skipped": list(found.skipped),
        "sha256": found.sha256,
        "cities": list(found.cities),
    }
    with LOCK:
        ensure_run_folder(workspace)
        write_atomic(workspace.resolve() / receipt_path(found.date, RUN), json.dumps(receipt, separators=(",", ":")) + "\n")


def get_daily(given: DailyInput, workspace: Path) -> DailyOutput:
    """Up to 30 cities' local days for a task, from the data the date rules choose: each needed variable
    in the task's units, the number of readings, and the source variables with a missing reading.
    Nothing else, so the agent has nothing to re-derive. The data is fetched once per run."""
    task = checked_task(given.task)
    checked_cities(given.cities, MAX_CITIES_PER_CALL)
    found = sources.read(task["date"])
    write_receipt(workspace, found)
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    series = {entry["city"]: entry for entry in found.cities}
    rows = []
    for name in given.cities:
        day = day_metrics(series[name], found.key[3])
        rows.append(
            DailyCity(
                city=name,
                samples=day.samples,
                missing=list(day.missing),
                values={variable: float(value) for variable, value in given_values(day, conditions, units).items()},
            )
        )
    return DailyOutput(date=task["date"], run=RUN, source=data_source(found), cities=rows)


def call_summary(name: str, arguments: dict) -> dict:
    """What a call asked for, in a few numbers: never its whole arguments."""
    task = arguments.get("task") if isinstance(arguments.get("task"), dict) else arguments
    summary = {"date": task.get("date") if isinstance(task, dict) else None}
    if name == "get_daily":
        summary["cities"] = len(arguments.get("cities") or [])
        found = sources.cached(summary["date"]) if isinstance(summary["date"], str) else None
        if found is not None:
            summary["source"] = found.source()["label"]
    elif name == "report_alerts":
        summary.update(
            examined=len(arguments.get("examined") or []),
            alerts=len(arguments.get("alerts") or []),
            unavailable=len(arguments.get("unavailable") or []),
        )
    return summary


def log_call(workspace: Path, name: str, arguments: dict, result: str, seconds: float) -> None:
    """Append one line for a tool call to this run's reports/<run>/calls.log in the workspace (the
    member's folder for this plugin), keeping the last CALL_LOG_LINES lines; a log that cannot be
    written never fails a call."""
    line = json.dumps(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "run": RUN,
            "tool": name,
            **call_summary(name, arguments),
            "seconds": round(seconds, 2),
            "result": result[:200],
            **({"temp_beside": WRITES["beside"]} if WRITES["beside"] else {}),
        }
    )
    try:
        with LOCK:
            path = ensure_run_folder(workspace) / CALL_LOG
            lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
            lines.append(line)
            write_atomic(path, "\n".join(lines[-CALL_LOG_LINES:]) + "\n")
    except OSError:
        pass


def mergeable_report(path: Path, task: dict, sha256: str) -> dict | None:
    """The report this run already wrote at ``path`` for ``task`` on the same data, or None when there is
    none or it is for another task or data: the call then starts afresh."""
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if report_problem(found) or found["run"] != RUN or found["sha256"] != sha256:
        return None
    if canonical_task(found["task"]) != canonical_task(task):
        return None
    return found


def report_alerts(given: ReportInput, workspace: Path) -> ReportOutput:
    """Merge this call's judgement into this run's reports/<run>/<date>.json: every examined city is
    restated by the call (its earlier entries go, this call's entries come in; no entry means no alert),
    every other city's entries are kept. The merge is atomic per call, and the answer is the merged
    report as it stands right after it, naming its run and the data it judges: how many cities are
    examined, whether that is every city, and the report itself, which the agent submits once complete."""
    task = checked_task(given.task)
    date = task["date"]
    found = sources.cached(date)
    if found is None:
        raise ValueError(f"call get_daily for {date} first: the report names the data it judges")
    examined = given.examined
    checked_cities(examined, MAX_REPORT_ENTRIES)
    given_plain = plain_output(given)
    problem = judgement_problem(task, list(examined), given_plain["alerts"], given_plain["unavailable"])
    if problem:
        raise ValueError(problem)
    path = workspace.resolve() / report_path(date, RUN)
    covered = set(examined)
    source = found.source()
    with LOCK:
        ensure_run_folder(workspace)
        earlier = mergeable_report(path, task, found.sha256) or {"examined": [], "alerts": [], "unavailable": []}
        merged = {
            "date": date,
            "run": RUN,
            "task": task,
            "source": source,
            "sha256": found.sha256,
            "examined": [name for name in CITY_NAMES if name in covered | set(earlier["examined"])],
            "alerts": [entry for entry in earlier["alerts"] if entry["city"] not in covered] + given_plain["alerts"],
            "unavailable": [entry for entry in earlier["unavailable"] if entry["city"] not in covered] + given_plain["unavailable"],
        }
        write_atomic(path, json.dumps(merged, indent=2) + "\n")
    report = AlertReportOutput(
        task=given.task,
        run=RUN,
        data=build_output(source, DataSource),
        alerts=[Alert(city=entry["city"], values=dict(entry["values"]), matched=list(entry["matched"])) for entry in merged["alerts"]],
        unavailable=[Unavailable(city=entry["city"], variables=list(entry["variables"])) for entry in merged["unavailable"]],
    )
    return ReportOutput(
        path=report_path(date, RUN),
        examined=len(merged["examined"]),
        complete=len(merged["examined"]) == len(CITY_NAMES),
        report=report,
    )


def check_date(given: DateInput, workspace: Path) -> DateOutput:
    """Today's UTC date from the clock, and which data a daily-alert task on a date would use: refused, as
    the task's answer key refuses it, when the rules allow it only at the clock and not 10 minutes before."""
    clock = sources.epoch_minutes(sources.now())
    today = date_text(clock // 1440)
    date = today if given.date.strip().lower() == "today" else given.date
    windows = windows_of(clock // 1440)
    problem = date_problem(date)
    calendar_day = problem is None or problem.startswith(f"{date} is before")
    choice = None if problem else usable(date, clock)
    if choice is not None and choice.problem:
        problem = choice.problem
    if problem:
        kind, source = None, problem
        message = f"{date} cannot be judged: {problem}"
    else:
        kind = DataKind(choice.kind)
        source = source_label(choice.kind, choice.model, choice.runs[0] if choice.runs else "", choice.step_hours)
        message = f"Today is {today} (UTC). {date} is a {weekday(date)}; a daily-alert task on it uses the {source}."
    return DateOutput(
        today=today,
        clock=stamp_text(clock),
        date=date,
        weekday=weekday(date) if calendar_day else "",
        kind=kind,
        source=source,
        message=message,
        **windows,
    )

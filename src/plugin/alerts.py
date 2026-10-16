"""The tools: the answer key, a day's values for the agent (with the receipt of the data it read), the
merged report, and the date check."""

from __future__ import annotations

import json
import os
import secrets
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
    Units,
    canonical_task,
    choose,
    date_problem,
    date_text,
    day_metrics,
    expected_from_series,
    given_values,
    judgement_problem,
    parse_conditions,
    plain_task,
    report_problem,
    source_label,
    stamp_text,
    task_problem,
    weekday,
    windows_of,
)

# Reports live in the run's folder, which is the member's own folder for this plugin.
REPORT_DIR = "reports"
RUN_MARKER = "run.json"
# Every tool call of every run, one JSON line each, kept short: what a run did when its agent went wrong.
CALL_LOG = "calls.log"
CALL_LOG_LINES = 300
# This plugin process's run: the server records it in the workspace at its start, and every report and
# receipt carries it, so a file left by an earlier run is never taken for this run's.
RUN = secrets.token_hex(8)
LOCK = threading.Lock()


def report_path(date: str) -> str:
    return f"{REPORT_DIR}/{date}.json"


def receipt_path(date: str) -> str:
    return f"{REPORT_DIR}/{date}.source.json"


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


def pin_alerts(given: AlertInput, workspace: Path) -> PinOutput:
    """The answer key: the data the date rules choose at the clock and DELTA_MINUTES before it (one
    source, or two when they differ), every city's local day in it, and the alerts the rules give."""
    task = checked_task(given)
    clock, found = sources.pin(task["date"])
    return PinOutput(date=task["date"], clock=stamp_text(clock), candidates=[pinned_source(task, one) for one in found])


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temporary file and one rename, so a reader never sees a half-written file."""
    temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def write_receipt(workspace: Path, found: sources.Fetched) -> None:
    """Record the data this run's agent read, for the verifier to match against the answer key."""
    path = workspace.resolve() / receipt_path(found.date)
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
        if not marker_is_current(workspace):
            write_run_marker(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(receipt, separators=(",", ":")) + "\n")


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
    return DailyOutput(date=task["date"], source=data_source(found), cities=rows)


def run_marker_path(workspace: Path) -> Path:
    return workspace.resolve() / REPORT_DIR / RUN_MARKER


def write_run_marker(workspace: Path) -> str:
    """Record this process's run token at reports/run.json (the server does it when it starts)."""
    path = run_marker_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps({"run": RUN, "started": datetime.now(UTC).isoformat(timespec="seconds")}, indent=2) + "\n")
    return RUN


def marker_is_current(workspace: Path) -> bool:
    path = run_marker_path(workspace)
    if not path.is_file():
        return False
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return isinstance(found, dict) and found.get("run") == RUN


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
    """Append one line for a tool call to reports/calls.log in the workspace (the member's folder for
    this plugin), keeping the last CALL_LOG_LINES lines; a log that cannot be written never fails a call."""
    line = json.dumps(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "run": RUN,
            "tool": name,
            **call_summary(name, arguments),
            "seconds": round(seconds, 2),
            "result": result[:200],
        }
    )
    try:
        path = workspace.resolve() / REPORT_DIR / CALL_LOG
        with LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
            lines.append(line)
            write_atomic(path, "\n".join(lines[-CALL_LOG_LINES:]) + "\n")
    except OSError:
        pass


def mergeable_report(path: Path, task: dict, sha256: str) -> dict | None:
    """The report this run already wrote at ``path`` for ``task`` on the same data, or None when there is
    none or the file is a leftover (not a well-formed report, another task, data or run's): the call
    then starts afresh."""
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
    """Merge this call's judgement into reports/<date>.json: every examined city is restated by the
    call (its earlier entries go, this call's entries come in; no entry means no alert), every other
    city's entries are kept. The merge is atomic per call, and the answer is the merged report as it
    stands right after it, naming the data it judges: how many cities are examined, whether that is
    every city, and the report itself, which the agent submits once it is complete."""
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
    path = workspace.resolve() / report_path(date)
    covered = set(examined)
    source = found.source()
    with LOCK:
        if not marker_is_current(workspace):
            write_run_marker(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
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
        data=build_output(source, DataSource),
        alerts=[Alert(city=entry["city"], values=dict(entry["values"]), matched=list(entry["matched"])) for entry in merged["alerts"]],
        unavailable=[Unavailable(city=entry["city"], variables=list(entry["variables"])) for entry in merged["unavailable"]],
    )
    return ReportOutput(
        path=report_path(date),
        examined=len(merged["examined"]),
        complete=len(merged["examined"]) == len(CITY_NAMES),
        report=report,
    )


def check_date(given: DateInput, workspace: Path) -> DateOutput:
    """Today's UTC date from the clock, and which data a daily-alert task on a date would use."""
    clock = sources.epoch_minutes(sources.now())
    today = date_text(clock // 1440)
    date = today if given.date.strip().lower() == "today" else given.date
    windows = windows_of(clock // 1440)
    problem = date_problem(date)
    calendar_day = problem is None or problem.startswith(f"{date} is before")
    choice = None if problem else choose(date, clock)
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

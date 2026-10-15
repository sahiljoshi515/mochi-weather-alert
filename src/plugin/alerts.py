"""The tools: the pinned answer key, the city list, a day's aggregates, a day's hours, and the merged report."""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from slimconfig import plain_output

from contract import (
    Alert,
    AlertInput,
    AlertReportOutput,
    CitiesInput,
    CitiesOutput,
    CityDay,
    DailyCity,
    DailyInput,
    DailyOutput,
    HourlyInput,
    HourlyOutput,
    HourlyRow,
    PinnedCity,
    PinOutput,
    ReportInput,
    ReportOutput,
    Unavailable,
)
from plugin.data import snapshot
from plugin.rules import (
    MAX_CITIES_PER_CALL,
    MAX_REPORT_ENTRIES,
    DayMetrics,
    Units,
    aggregate,
    canonical_task,
    date_problem,
    evaluate,
    given_values,
    judgement_problem,
    parse_conditions,
    plain_task,
    report_problem,
    task_problem,
)

# The report lives in the run's folder, which is the member's own folder for this plugin.
REPORT_DIR = "reports"
RUN_MARKER = "run.json"
# Every tool call of every run, one JSON line each, kept short: what a run did when its agent went wrong.
CALL_LOG = "calls.log"
CALL_LOG_LINES = 300
# This plugin process's run: the server records it in the workspace at its start, every report carries
# it, and a report left by an earlier run (another token, or an older shape) is replaced, never merged.
RUN = secrets.token_hex(8)
LOCK = threading.Lock()


def checked_date(date: str) -> str:
    problem = date_problem(date)
    if problem:
        raise ValueError(problem)
    return date


def number(value: Decimal | None) -> float | None:
    """A Decimal as the JSON number whose shortest text is that decimal."""
    return None if value is None else float(value)


def day_metrics(name: str, date: str) -> DayMetrics:
    return aggregate(name, [values for _, values in snapshot().day(name, date)])


def report_path(date: str) -> str:
    return f"{REPORT_DIR}/{date}.json"


def pin_alerts(given: AlertInput, workspace: Path) -> PinOutput:
    """Every city's local day in metric units, and the alerts and unavailable cities the rules give."""
    date = checked_date(given.date)
    conditions = parse_conditions(given.conditions)
    units = Units(given.temperature_unit.value, given.wind_unit.value, given.rain_unit.value)
    data = snapshot()
    metrics = [day_metrics(city.name, date) for city in data.cities]
    expected = evaluate(metrics, conditions, given.match.value, units)
    return PinOutput(
        date=date,
        metric=[
            PinnedCity(
                city=day.city,
                country=data.city(day.city).country,
                hours=day.hours,
                max_temp_c=number(day.max_temp_c),
                min_temp_c=number(day.min_temp_c),
                rain_mm=number(day.rain_mm),
                max_wind_kmh=number(day.max_wind_kmh),
                humidity_sum_pct=number(day.humidity_sum_pct),
                humidity_hours=day.humidity_hours,
                missing=list(day.missing),
            )
            for day in metrics
        ],
        alerts=[
            Alert(city=alert.city, values={k: float(v) for k, v in alert.values.items()}, matched=list(alert.matched))
            for alert in expected.alerts
        ],
        unavailable=[Unavailable(city=city, variables=list(variables)) for city, variables in expected.unavailable.items()],
    )


def list_cities(given: CitiesInput, workspace: Path) -> CitiesOutput:
    """Every city, with how many hourly rows its local calendar day has on the date."""
    date = checked_date(given.date)
    data = snapshot()
    return CitiesOutput(
        date=date,
        cities=[
            CityDay(
                city=city.name, country=city.country, timezone=city.timezone, hours_available=len(data.day(city.name, date))
            )
            for city in data.cities
        ],
    )


def get_daily(given: DailyInput, workspace: Path) -> DailyOutput:
    """Up to 30 cities' local days for a task: each needed variable in the task's units, the hour count, and
    the source variables with a missing hour. Nothing else, so the agent has nothing to re-derive."""
    task = plain_task(given.task)
    problem = task_problem(task)
    if problem:
        raise ValueError(problem)
    date = task["date"]
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    names = given.cities
    if not 1 <= len(names) <= MAX_CITIES_PER_CALL or len(set(names)) != len(names):
        raise ValueError(f"cities must be 1 to {MAX_CITIES_PER_CALL} distinct names")
    data = snapshot()
    unknown = [name for name in names if not any(city.name == name for city in data.cities)]
    if unknown:
        known = ", ".join(city.name for city in data.cities)
        raise ValueError(f"unknown city {', '.join(unknown)}; the cities are {known}")
    rows = []
    for name in names:
        day = day_metrics(name, date)
        rows.append(
            DailyCity(
                city=name,
                hours=day.hours,
                missing=list(day.missing),
                values={variable: float(value) for variable, value in given_values(day, conditions, units).items()},
            )
        )
    return DailyOutput(date=date, cities=rows)


def get_hourly(given: HourlyInput, workspace: Path) -> HourlyOutput:
    """One city's hourly rows over its local calendar day."""
    date = checked_date(given.date)
    data = snapshot()
    city = data.city(given.city)
    rows = [
        HourlyRow(
            time=time,
            temp_c=number(values["temperature_2m"]),
            humidity_pct=number(values["relative_humidity_2m"]),
            rain_mm=number(values["precipitation"]),
            wind_kmh=number(values["wind_speed_10m"]),
        )
        for time, values in data.day(city.name, date)
    ]
    return HourlyOutput(city=city.name, date=date, timezone=city.timezone, rows=rows)


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temporary file and one rename, so a reader never sees a half-written file."""
    temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def run_marker_path(workspace: Path) -> Path:
    return workspace.resolve() / REPORT_DIR / RUN_MARKER


def write_run_marker(workspace: Path) -> str:
    """Record this process's run token at reports/run.json (the server does it when it starts)."""
    path = run_marker_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps({"run": RUN, "started": datetime.now(UTC).isoformat(timespec="seconds")}, indent=2) + "\n")
    return RUN


def call_summary(name: str, arguments: dict) -> dict:
    """What a call asked for, in a few numbers: never its whole arguments."""
    if name == "get_daily":
        return {"date": (arguments.get("task") or {}).get("date"), "cities": len(arguments.get("cities") or [])}
    if name == "report_alerts":
        task = arguments.get("task") or {}
        return {
            "date": task.get("date"),
            "examined": len(arguments.get("examined") or []),
            "alerts": len(arguments.get("alerts") or []),
            "unavailable": len(arguments.get("unavailable") or []),
        }
    return {"date": arguments.get("date"), "city": arguments.get("city")}


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


def marker_is_current(workspace: Path) -> bool:
    path = run_marker_path(workspace)
    if not path.is_file():
        return False
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return isinstance(found, dict) and found.get("run") == RUN


def mergeable_report(path: Path, task: dict) -> dict | None:
    """The report this run already wrote at ``path`` for ``task`` (a plain task), or None when there is
    none or the file is a leftover (not a well-formed report, another task, or another run's): the
    call then starts afresh."""
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if report_problem(found) or found["run"] != RUN or canonical_task(found["task"]) != canonical_task(task):
        return None
    return found


def report_alerts(given: ReportInput, workspace: Path) -> ReportOutput:
    """Merge this call's judgement into reports/<date>.json: every examined city is restated by the
    call (its earlier entries go, this call's entries come in; no entry means no alert), every other
    city's entries are kept. The merge is atomic per call, and the answer is the merged report as it
    stands right after it: how many cities are examined, whether that is every city, and the report
    itself, which the agent submits once it is complete."""
    task = plain_task(given.task)
    problem = task_problem(task)
    if problem:
        raise ValueError(problem)
    date = task["date"]
    data = snapshot()
    examined = given.examined
    if not 1 <= len(examined) <= MAX_REPORT_ENTRIES or len(set(examined)) != len(examined):
        raise ValueError(f"examined must name 1 to {MAX_REPORT_ENTRIES} distinct cities")
    unknown = [name for name in examined if not any(city.name == name for city in data.cities)]
    if unknown:
        known = ", ".join(city.name for city in data.cities)
        raise ValueError(f"unknown city {', '.join(unknown)}; the cities are {known}")
    given_plain = plain_output(given)
    problem = judgement_problem(task, list(examined), given_plain["alerts"], given_plain["unavailable"])
    if problem:
        raise ValueError(problem)
    path = workspace.resolve() / REPORT_DIR / f"{date}.json"
    covered = set(examined)
    with LOCK:
        if not marker_is_current(workspace):
            write_run_marker(workspace)
        earlier = mergeable_report(path, task) or {"date": date, "run": RUN, "task": task, "examined": [], "alerts": [], "unavailable": []}
        merged = {
            "date": date,
            "run": RUN,
            "task": task,
            "examined": [city.name for city in data.cities if city.name in covered | set(earlier["examined"])],
            "alerts": [entry for entry in earlier["alerts"] if entry["city"] not in covered] + given_plain["alerts"],
            "unavailable": [entry for entry in earlier["unavailable"] if entry["city"] not in covered] + given_plain["unavailable"],
        }
        write_atomic(path, json.dumps(merged, indent=2) + "\n")
    report = AlertReportOutput(
        task=given.task,
        alerts=[Alert(city=entry["city"], values=dict(entry["values"]), matched=list(entry["matched"])) for entry in merged["alerts"]],
        unavailable=[Unavailable(city=entry["city"], variables=list(entry["variables"])) for entry in merged["unavailable"]],
    )
    return ReportOutput(
        path=report_path(date),
        examined=len(merged["examined"]),
        complete=len(merged["examined"]) == len(data.cities),
        report=report,
    )

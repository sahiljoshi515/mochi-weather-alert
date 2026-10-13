"""The tools: the pinned answer key, the city list, a day's aggregates, a day's hours, and the report."""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

from slimconfig import plain_output

from contract import (
    Alert,
    AlertInput,
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
    date_problem,
    evaluate,
    parse_conditions,
    report_problem,
)

REPORT_DIR = "weather-alert"
RUN_MARKER = "run.json"
# This plugin process's run: the server records it in the workspace at its start, every report carries
# it, and a report left by an earlier run (another token, or an older shape) is replaced, never merged.
RUN = secrets.token_hex(8)
LOCK = threading.Lock()
MEAN_PLACES = Decimal("0.0001")


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
    """Up to 10 cities' local-day aggregates in metric units."""
    date = checked_date(given.date)
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
        mean = None
        if day.humidity_sum_pct is not None and day.humidity_hours:
            exact = Fraction(day.humidity_sum_pct) / day.humidity_hours
            mean = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(MEAN_PLACES, ROUND_HALF_UP)
        rows.append(
            DailyCity(
                city=name,
                country=data.city(name).country,
                hours=day.hours,
                max_temp_c=number(day.max_temp_c),
                min_temp_c=number(day.min_temp_c),
                rain_mm=number(day.rain_mm),
                max_wind_kmh=number(day.max_wind_kmh),
                mean_humidity_pct=number(mean),
                missing=list(day.missing),
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
    """Record this process's run token at weather-alert/run.json (the server does it when it starts)."""
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


def mergeable_report(path: Path, date: str) -> dict | None:
    """The report this run already wrote at ``path``, or None when there is none or the file is a
    leftover (not a well-formed report, another date, or another run's): the call then starts afresh."""
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if report_problem(found) or found["date"] != date or found["run"] != RUN:
        return None
    return found


def report_alerts(given: ReportInput, workspace: Path) -> ReportOutput:
    """Merge this call's judgement into weather-alert/<date>.json: every examined city is restated by the
    call (its earlier entries go, this call's entries come in; no entry means no alert), every other
    city's entries are kept. The merge is atomic per call; the counts returned are this call's own."""
    date = checked_date(given.date)
    data = snapshot()
    examined = given.examined
    if not 1 <= len(examined) <= MAX_REPORT_ENTRIES or len(set(examined)) != len(examined):
        raise ValueError(f"examined must name 1 to {MAX_REPORT_ENTRIES} distinct cities")
    unknown = [name for name in examined if not any(city.name == name for city in data.cities)]
    if unknown:
        known = ", ".join(city.name for city in data.cities)
        raise ValueError(f"unknown city {', '.join(unknown)}; the cities are {known}")
    named = [alert.city for alert in given.alerts] + [entry.city for entry in given.unavailable]
    if len(set(named)) != len(named):
        raise ValueError("a city may appear once, as an alert or as unavailable")
    outside = [name for name in named if name not in set(examined)]
    if outside:
        raise ValueError(f"{', '.join(outside)} must be listed in examined to be reported")
    path = workspace.resolve() / REPORT_DIR / f"{date}.json"
    covered = set(examined)
    given_plain = plain_output(given)
    with LOCK:
        if not marker_is_current(workspace):
            write_run_marker(workspace)
        earlier = mergeable_report(path, date) or {"date": date, "run": RUN, "examined": [], "alerts": [], "unavailable": []}
        merged = {
            "date": date,
            "run": RUN,
            "examined": [city.name for city in data.cities if city.name in covered | set(earlier["examined"])],
            "alerts": [entry for entry in earlier["alerts"] if entry["city"] not in covered] + given_plain["alerts"],
            "unavailable": [entry for entry in earlier["unavailable"] if entry["city"] not in covered] + given_plain["unavailable"],
        }
        write_atomic(path, json.dumps(merged, indent=2) + "\n")
    return ReportOutput(path=report_path(date), alerts=len(given.alerts), unavailable=len(given.unavailable), examined=len(examined))

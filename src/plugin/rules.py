"""The alert rules, shared byte for byte by the server (src/plugin/rules.py) and the verifier
(verifier/rules.py): the condition grammar, the exact unit conversions, half-up rounding, a local
day's metric aggregates, and the evaluation that turns aggregates into alerts.

Everything here is exact arithmetic on Decimal and Fraction: a comparison never sees a binary
float. It imports only standard-library modules a verifier may use, and nothing of the plugin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction

SNAPSHOT_START = "2026-07-01"
SNAPSHOT_END = "2026-09-30"
VARIABLES = ("max_temp", "min_temp", "rain", "wind", "humidity")
OPS = (">=", ">", "<=", "<")
# Each rule variable's source variable in the snapshot, by Open-Meteo's hourly name.
SOURCE_OF = {
    "max_temp": "temperature_2m",
    "min_temp": "temperature_2m",
    "rain": "precipitation",
    "wind": "wind_speed_10m",
    "humidity": "relative_humidity_2m",
}
SOURCE_VARIABLES = ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m")
MATCHES = ("all", "any")
TEMPERATURE_UNITS = ("C", "F")
WIND_UNITS = ("kmh", "mph", "ms")
RAIN_UNITS = ("mm", "in")
MIN_CONDITIONS = 1
MAX_CONDITIONS = 6
CITY_COUNT = 30  # the snapshot's cities
MAX_CITIES_PER_CALL = CITY_COUNT
MAX_REPORT_ENTRIES = CITY_COUNT  # a report names each city at most once
CHUNK = 10  # cities per report_alerts call on the prescribed path
PLACES = 1  # reported values carry one decimal
DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
CONDITION = re.compile(r"^\s*(max_temp|min_temp|rain|wind|humidity)\s*(>=|<=|>|<)\s*([+-]?(?:\d+(?:\.\d{1,2})?|\.\d{1,2}))\s*$")
GRAMMAR = (
    'a condition is "<variable> <op> <number>": variable max_temp, min_temp, rain, wind or humidity; '
    "op >=, >, <= or <; a number with at most 2 decimals"
)
# Exact conversion factors: °F = °C × 9/5 + 32, mph = km/h ÷ 1.609344, m/s = km/h ÷ 3.6, in = mm ÷ 25.4.
KMH_PER_MPH = Fraction("1.609344")
KMH_PER_MS = Fraction("3.6")
MM_PER_IN = Fraction("25.4")


@dataclass(frozen=True)
class Condition:
    variable: str
    op: str
    threshold: Decimal
    text: str  # the canonical form, "<variable> <op> <number>"


@dataclass(frozen=True)
class Units:
    temperature: str  # C or F
    wind: str  # kmh, mph or ms
    rain: str  # mm or in


@dataclass(frozen=True)
class DayMetrics:
    """One city's local day in metric units, as the pin records it: the exact ingredients of every value."""

    city: str
    hours: int
    max_temp_c: Decimal | None
    min_temp_c: Decimal | None
    rain_mm: Decimal | None
    max_wind_kmh: Decimal | None
    humidity_sum_pct: Decimal | None
    humidity_hours: int
    missing: tuple[str, ...]  # source variables with at least one missing hour


@dataclass(frozen=True)
class Alert:
    city: str
    values: dict[str, Decimal]  # each needed variable, in the requested units, rounded half up to 1 decimal
    matched: tuple[str, ...]  # the conditions that hold, canonical, in the order given


@dataclass(frozen=True)
class Expected:
    alerts: tuple[Alert, ...]  # in the order of the metric rows
    unavailable: dict[str, tuple[str, ...]]  # city -> the needed variables it has no complete day for
    examined: tuple[str, ...]  # every city judged, in the order of the metric rows


# ----- Dates -----


def date_problem(text: str) -> str | None:
    """What is wrong with a snapshot date: not YYYY-MM-DD, not a calendar day, or outside the snapshot."""
    found = DATE.fullmatch(text) if isinstance(text, str) else None
    if not found:
        return "date must be YYYY-MM-DD"
    year, month, day = (int(part) for part in found.groups())
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    lengths = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if not 1 <= month <= 12 or not 1 <= day <= lengths[month - 1]:
        return f"{text} is not a calendar day"
    if not SNAPSHOT_START <= text <= SNAPSHOT_END:
        return f"{text} is outside the snapshot, {SNAPSHOT_START} to {SNAPSHOT_END}"
    return None


# ----- Conditions -----


def canonical_number(value: Decimal) -> str:
    """A threshold as its shortest plain text: 95.00 -> 95, 0.50 -> 0.5, -0 -> 0."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def parse_condition(text: str) -> Condition:
    """The condition ``text`` means, or ValueError with a plain reason."""
    found = CONDITION.fullmatch(text) if isinstance(text, str) else None
    if not found:
        raise ValueError(f"{text!r} is not a condition: {GRAMMAR}")
    variable, op, number = found.groups()
    threshold = Decimal(number)
    return Condition(variable, op, threshold, f"{variable} {op} {canonical_number(threshold)}")


def parse_conditions(texts: list[str]) -> list[Condition]:
    """Every condition, in order; ValueError when there are too few or too many, one is malformed, or
    two mean the same."""
    if not isinstance(texts, list) or not MIN_CONDITIONS <= len(texts) <= MAX_CONDITIONS:
        raise ValueError(f"conditions must hold {MIN_CONDITIONS} to {MAX_CONDITIONS} rules")
    conditions = [parse_condition(text) for text in texts]
    seen: set[str] = set()
    for condition in conditions:
        if condition.text in seen:
            raise ValueError(f"condition {condition.text!r} is given twice")
        seen.add(condition.text)
    return conditions


def needed_variables(conditions: list[Condition]) -> tuple[str, ...]:
    """The variables the conditions use, in the canonical order."""
    used = {condition.variable for condition in conditions}
    return tuple(variable for variable in VARIABLES if variable in used)


# ----- Arithmetic -----


def convert(variable: str, metric: Fraction, units: Units) -> Fraction:
    """A metric value in the requested unit, exactly."""
    if variable in ("max_temp", "min_temp"):
        return metric * 9 / 5 + 32 if units.temperature == "F" else metric
    if variable == "wind":
        if units.wind == "mph":
            return metric / KMH_PER_MPH
        if units.wind == "ms":
            return metric / KMH_PER_MS
        return metric
    if variable == "rain":
        return metric / MM_PER_IN if units.rain == "in" else metric
    return metric  # humidity is always a percentage


def round_half_up(value: Fraction, places: int = PLACES) -> Decimal:
    """``value`` to ``places`` decimals, ties away from zero (Decimal's ROUND_HALF_UP), exactly."""
    scaled = abs(value) * 10**places
    whole = scaled.numerator // scaled.denominator
    if (scaled - whole) * 2 >= 1:
        whole += 1
    if value < 0 and whole:
        whole = -whole
    return Decimal(whole).scaleb(-places).quantize(Decimal(1).scaleb(-places))


def holds(value: Fraction, condition: Condition) -> bool:
    """Whether the exact ``value`` satisfies the condition."""
    threshold = Fraction(condition.threshold)
    if condition.op == ">=":
        return value >= threshold
    if condition.op == ">":
        return value > threshold
    if condition.op == "<=":
        return value <= threshold
    return value < threshold


# ----- A day's aggregates -----


def aggregate(city: str, rows: list[dict[str, Decimal | None]]) -> DayMetrics:
    """The local day's metric aggregates from its hourly rows (each a mapping of source variable to a
    Decimal, or None for a missing value). A variable with a missing hour is listed in ``missing``;
    its aggregate is over the hours that have it, or None when none has."""
    hours = len(rows)
    present = {variable: [row[variable] for row in rows if row[variable] is not None] for variable in SOURCE_VARIABLES}
    missing = tuple(variable for variable in SOURCE_VARIABLES if len(present[variable]) < hours or not hours)
    temps, humidity = present["temperature_2m"], present["relative_humidity_2m"]
    rain, wind = present["precipitation"], present["wind_speed_10m"]
    return DayMetrics(
        city=city,
        hours=hours,
        max_temp_c=max(temps) if temps else None,
        min_temp_c=min(temps) if temps else None,
        rain_mm=sum(rain, Decimal(0)) if rain else None,
        max_wind_kmh=max(wind) if wind else None,
        humidity_sum_pct=sum(humidity, Decimal(0)) if humidity else None,
        humidity_hours=len(humidity),
        missing=missing,
    )


def metrics_from_plain(
    city: str,
    hours: int,
    max_temp_c: float | None,
    min_temp_c: float | None,
    rain_mm: float | None,
    max_wind_kmh: float | None,
    humidity_sum_pct: float | None,
    humidity_hours: int,
    missing: list[str],
) -> DayMetrics:
    """A pinned row (JSON numbers) as exact metrics: a float's shortest text is its intended decimal."""

    def exact(value: float | None) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    return DayMetrics(
        city=city,
        hours=hours,
        max_temp_c=exact(max_temp_c),
        min_temp_c=exact(min_temp_c),
        rain_mm=exact(rain_mm),
        max_wind_kmh=exact(max_wind_kmh),
        humidity_sum_pct=exact(humidity_sum_pct),
        humidity_hours=humidity_hours,
        missing=tuple(missing),
    )


def exact_metric(metrics: DayMetrics, variable: str) -> Fraction | None:
    """A rule variable's exact metric value for the day, or None when the day has no value for it."""
    if variable == "max_temp":
        value = metrics.max_temp_c
    elif variable == "min_temp":
        value = metrics.min_temp_c
    elif variable == "rain":
        value = metrics.rain_mm
    elif variable == "wind":
        value = metrics.max_wind_kmh
    else:
        if metrics.humidity_sum_pct is None or not metrics.humidity_hours:
            return None
        return Fraction(metrics.humidity_sum_pct) / metrics.humidity_hours
    return None if value is None else Fraction(value)


def available(metrics: DayMetrics, variable: str) -> bool:
    """Whether the day has every hour of the variable, so a rule may use it."""
    return SOURCE_OF[variable] not in metrics.missing and exact_metric(metrics, variable) is not None


# ----- Evaluation -----


def evaluate(metrics: list[DayMetrics], conditions: list[Condition], match: str, units: Units) -> Expected:
    """The alerts and the unavailable cities the rules give for these days.

    A city with a missing hour for any variable the conditions use is unavailable, with those
    variables, and never an alert. Otherwise each condition is tested against the exact converted
    value; the city is an alert when every condition holds (match "all") or at least one does
    ("any"), with each needed variable's value rounded half up to one decimal and the conditions
    that hold.
    """
    needed = needed_variables(conditions)
    alerts: list[Alert] = []
    unavailable: dict[str, tuple[str, ...]] = {}
    for day in metrics:
        lacking = tuple(variable for variable in needed if not available(day, variable))
        if lacking:
            unavailable[day.city] = lacking
            continue
        exact = {variable: convert(variable, exact_metric(day, variable), units) for variable in needed}
        matched = tuple(condition.text for condition in conditions if holds(exact[condition.variable], condition))
        if (match == "all" and len(matched) == len(conditions)) or (match == "any" and matched):
            alerts.append(Alert(day.city, {variable: round_half_up(exact[variable]) for variable in needed}, matched))
    return Expected(tuple(alerts), unavailable, tuple(day.city for day in metrics))


# ----- The task, the report file and the submitted report -----

TASK_KEYS = ("date", "match", "temperature_unit", "wind_unit", "rain_unit", "conditions")
REPORT_KEYS = ("date", "run", "task", "examined", "alerts", "unavailable")


def plain_task(task) -> dict:
    """A task input (a contract AlertInput, whose choices are Enums) as the plain JSON the report
    file and the submitted report carry."""

    def plain(value):
        return value.value if isinstance(value, Enum) else value

    return {
        "date": task.date,
        "match": plain(task.match),
        "temperature_unit": plain(task.temperature_unit),
        "wind_unit": plain(task.wind_unit),
        "rain_unit": plain(task.rain_unit),
        "conditions": list(task.conditions),
    }


def task_problem(task: object) -> str | None:
    """What is wrong with a plain task (as JSON), or None: its keys, a snapshot date, the choices,
    and 1 to 6 well-formed, distinct conditions."""
    if not isinstance(task, dict) or set(task) != set(TASK_KEYS):
        return "task must hold date, match, temperature_unit, wind_unit, rain_unit and conditions"
    problem = date_problem(task["date"])
    if problem:
        return problem
    if task["match"] not in MATCHES:
        return "match must be all or any"
    if task["temperature_unit"] not in TEMPERATURE_UNITS:
        return "temperature_unit must be C or F"
    if task["wind_unit"] not in WIND_UNITS:
        return "wind_unit must be kmh, mph or ms"
    if task["rain_unit"] not in RAIN_UNITS:
        return "rain_unit must be mm or in"
    try:
        parse_conditions(task["conditions"])
    except ValueError as error:
        return str(error)
    return None


def canonical_task(task: dict) -> dict:
    """A plain task that passes task_problem with its conditions in canonical form: two tasks that
    mean the same compare equal."""
    return {**task, "conditions": [condition.text for condition in parse_conditions(task["conditions"])]}


def canonical_conditions(texts: list[str]) -> list[str]:
    """The conditions' canonical forms, or ValueError when one is malformed or two mean the same."""
    found = [parse_condition(text).text for text in texts]
    if len(set(found)) != len(found):
        raise ValueError("a condition is listed twice")
    return found


def is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def names_ok(names: object) -> bool:
    return isinstance(names, list) and all(isinstance(name, str) and name.strip() for name in names)


def judgement_problem(task: dict, examined: object, alerts: object, unavailable: object) -> str | None:
    """What is wrong with a judgement's lists under ``task`` (a plain task that passes task_problem),
    or None. ``examined`` is the cities the judgement covers (every reported city must be among
    them), or None when the judgement's cities are not held to a list."""
    if examined is not None and (not names_ok(examined) or len(set(examined)) != len(examined)):
        return "examined must be a list of distinct city names"
    if not isinstance(alerts, list) or not isinstance(unavailable, list):
        return "alerts and unavailable must be lists"
    conditions = parse_conditions(task["conditions"])
    given = [condition.text for condition in conditions]
    needed = needed_variables(conditions)
    cities = []
    for alert in alerts:
        if not isinstance(alert, dict) or set(alert) != {"city", "values", "matched"}:
            return "each alert must hold city, values and matched"
        if not isinstance(alert["city"], str) or not alert["city"].strip():
            return "an alert's city must be a city name"
        city = alert["city"]
        values = alert["values"]
        if not isinstance(values, dict) or not all(key in VARIABLES and is_number(value) for key, value in values.items()):
            return f"{city}: values must map condition variables to numbers"
        if set(values) != set(needed):
            return f"{city}: values must hold exactly the variables the conditions use: {', '.join(needed)}"
        if not isinstance(alert["matched"], list) or not all(isinstance(text, str) for text in alert["matched"]):
            return f"{city}: matched must be a list of conditions"
        try:
            matched = canonical_conditions(alert["matched"])
        except ValueError as error:
            return f"{city}: matched: {error}"
        unknown = [text for text in matched if text not in given]
        if unknown:
            return f"{city}: matched names a condition the task does not have: {unknown[0]}"
        if task["match"] == "all" and set(matched) != set(given):
            return f"{city}: with match all, an alert city matches every condition"
        if task["match"] == "any" and not matched:
            return f"{city}: with match any, an alert city matches at least one condition"
        cities.append(city)
    for entry in unavailable:
        if not isinstance(entry, dict) or set(entry) != {"city", "variables"}:
            return "each unavailable entry must hold city and variables"
        if not isinstance(entry["city"], str) or not entry["city"].strip():
            return "an unavailable entry's city must be a city name"
        variables = entry["variables"]
        if not isinstance(variables, list) or not variables or not all(name in needed for name in variables):
            return f"{entry['city']}: variables must name the condition variables the city lacks: {', '.join(needed)}"
        if len(set(variables)) != len(variables):
            return f"{entry['city']}: variables must be distinct"
        cities.append(entry["city"])
    if len(set(cities)) != len(cities):
        return "a city may appear once, as an alert or as unavailable"
    if examined is not None:
        outside = [city for city in cities if city not in set(examined)]
        if outside:
            return f"{', '.join(outside)} must be listed in examined to be reported"
    return None


def report_problem(report: object) -> str | None:
    """What is wrong with a report file's shape (as parsed JSON), or None: the keys, a date, the run
    marker of the process that wrote it, the task, and well-formed lists under that task."""
    if not isinstance(report, dict) or set(report) != set(REPORT_KEYS):
        return "the report must hold date, run, task, examined, alerts and unavailable"
    if not isinstance(report["date"], str):
        return "the report's date must be a string"
    if not isinstance(report["run"], str) or not report["run"].strip():
        return "the report's run must be the marker of the run that wrote it"
    problem = task_problem(report["task"])
    if problem:
        return f"task: {problem}"
    if report["date"] != report["task"]["date"]:
        return "the report's date is not its task's"
    return judgement_problem(report["task"], report["examined"], report["alerts"], report["unavailable"])


def plain_entries(expected: Expected) -> dict:
    """``expected``'s alerts and unavailable cities as JSON."""
    return {
        "alerts": [
            {"city": alert.city, "values": {k: float(v) for k, v in alert.values.items()}, "matched": list(alert.matched)}
            for alert in expected.alerts
        ],
        "unavailable": [{"city": city, "variables": list(variables)} for city, variables in expected.unavailable.items()],
    }


def plain_report(task: dict, expected: Expected) -> dict:
    """``expected`` as the report file's JSON (without the run marker report_alerts adds): what a
    perfect run's file says."""
    return {"date": task["date"], "task": dict(task), "examined": list(expected.examined), **plain_entries(expected)}


def expected_output(task: dict, expected: Expected) -> dict:
    """``expected`` as the task's output (an AlertReportOutput as JSON): what a perfect run submits."""
    return {"task": dict(task), **plain_entries(expected)}

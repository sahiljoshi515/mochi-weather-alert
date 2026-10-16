"""The alert rules, shared byte for byte by the server (src/plugin/rules.py) and the verifier
(verifier/rules.py): the 30 cities, the condition grammar, the calendar, which Open-Meteo data a
date is judged on, a local day's aggregates, the canonical form of the data both sides hash, the
exact unit conversions and rounding (ties away from zero), and the evaluation that turns a day into an alert.

Everything here is exact arithmetic on integers, Decimal and Fraction: a comparison never sees a
binary float, and dates are integer day and minute counts (no datetime: a verifier may not import
it). It imports only standard-library modules a verifier may use, and nothing of the plugin.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction

# ----- The cities: name, country, latitude, longitude, IANA time zone (alphabetical) -----

CITIES = (
    ("Athens", "Greece", "37.9838", "23.7275", "Europe/Athens"),
    ("Auckland", "New Zealand", "-36.8485", "174.7633", "Pacific/Auckland"),
    ("Bangkok", "Thailand", "13.7563", "100.5018", "Asia/Bangkok"),
    ("Buenos Aires", "Argentina", "-34.6037", "-58.3816", "America/Argentina/Buenos_Aires"),
    ("Cairo", "Egypt", "30.0444", "31.2357", "Africa/Cairo"),
    ("Cape Town", "South Africa", "-33.9249", "18.4241", "Africa/Johannesburg"),
    ("Denver", "United States", "39.7392", "-104.9903", "America/Denver"),
    ("Dubai", "United Arab Emirates", "25.2048", "55.2708", "Asia/Dubai"),
    ("Honolulu", "United States", "21.3069", "-157.8583", "Pacific/Honolulu"),
    ("Houston", "United States", "29.7604", "-95.3698", "America/Chicago"),
    ("Jakarta", "Indonesia", "-6.2088", "106.8456", "Asia/Jakarta"),
    ("Lagos", "Nigeria", "6.5244", "3.3792", "Africa/Lagos"),
    ("Lima", "Peru", "-12.0464", "-77.0428", "America/Lima"),
    ("London", "United Kingdom", "51.5074", "-0.1278", "Europe/London"),
    ("Madrid", "Spain", "40.4168", "-3.7038", "Europe/Madrid"),
    ("Mexico City", "Mexico", "19.4326", "-99.1332", "America/Mexico_City"),
    ("Moscow", "Russia", "55.7558", "37.6173", "Europe/Moscow"),
    ("Mumbai", "India", "19.076", "72.8777", "Asia/Kolkata"),
    ("Nairobi", "Kenya", "-1.2921", "36.8219", "Africa/Nairobi"),
    ("Oslo", "Norway", "59.9139", "10.7522", "Europe/Oslo"),
    ("Perth", "Australia", "-31.9505", "115.8605", "Australia/Perth"),
    ("Phoenix", "United States", "33.4484", "-112.074", "America/Phoenix"),
    ("Reykjavik", "Iceland", "64.1466", "-21.9426", "Atlantic/Reykjavik"),
    ("Santiago", "Chile", "-33.4489", "-70.6693", "America/Santiago"),
    ("Seoul", "South Korea", "37.5665", "126.978", "Asia/Seoul"),
    ("Singapore", "Singapore", "1.3521", "103.8198", "Asia/Singapore"),
    ("Sydney", "Australia", "-33.8688", "151.2093", "Australia/Sydney"),
    ("Tokyo", "Japan", "35.6762", "139.6503", "Asia/Tokyo"),
    ("Toronto", "Canada", "43.6532", "-79.3832", "America/Toronto"),
    ("Vancouver", "Canada", "49.2827", "-123.1207", "America/Vancouver"),
)
CITY_NAMES = tuple(city[0] for city in CITIES)
TIMEZONE_OF = {city[0]: city[4] for city in CITIES}
LONGITUDE_OF = {city[0]: Decimal(city[3]) for city in CITIES}
CITY_COUNT = len(CITIES)
MAX_CITIES_PER_CALL = CITY_COUNT
MAX_REPORT_ENTRIES = CITY_COUNT  # a report names each city at most once
# Cities per report_alerts call on the prescribed path: a hosted model's reply (its reasoning included)
# must stay under 2,048 tokens, so the task is judged in two halves.
CHUNK = 15

# ----- Rules and units -----

VARIABLES = ("max_temp", "min_temp", "rain", "wind", "humidity")
OPS = (">=", ">", "<=", "<")
# Each rule variable's source variable, by Open-Meteo's hourly name.
SOURCE_OF = {
    "max_temp": "temperature_2m",
    "min_temp": "temperature_2m",
    "rain": "precipitation",
    "wind": "wind_speed_10m",
    "humidity": "relative_humidity_2m",
}
SOURCE_VARIABLES = ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m")
# Readings at an instant are taken at the stamps inside the local day [start, end); precipitation is the
# total over the step that ends at a stamp, so it is taken at the stamps in (start, end].
ACCUMULATED = ("precipitation",)
MATCHES = ("all", "any")
TEMPERATURE_UNITS = ("C", "F")
WIND_UNITS = ("kmh", "mph", "ms")
RAIN_UNITS = ("mm", "in")
MIN_CONDITIONS = 1
MAX_CONDITIONS = 6
# How many decimals get_daily gives a value in the task's units: truncated, never rounded, so comparing
# it with a threshold of at most 2 decimals and rounding it to 1 decimal (ties away from zero) give the exact
# answers, negatives included (see round_half_up).
GIVEN_PLACES = 9
PLACES = 1  # reported values carry one decimal
# ASCII digits only: "\d" would accept other scripts' digits, and dates go into URLs and file names.
DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
STAMP = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})")
CONDITION = re.compile(r"\s*(max_temp|min_temp|rain|wind|humidity)\s*(>=|<=|>|<)\s*([+-]?(?:[0-9]+(?:\.[0-9]{1,2})?|\.[0-9]{1,2}))\s*")
GRAMMAR = (
    'a condition is "<variable> <op> <number>": variable max_temp, min_temp, rain, wind or humidity; '
    "op >=, >, <= or <; a number with at most 2 decimals"
)
# Exact conversion factors: °F = °C × 9/5 + 32, mph = km/h ÷ 1.609344, m/s = km/h ÷ 3.6, in = mm ÷ 25.4.
KMH_PER_MPH = Fraction("1.609344")
KMH_PER_MS = Fraction("3.6")
MM_PER_IN = Fraction("25.4")
# Plausible ranges of the source readings (metric), for checking fetched data.
SOURCE_RANGES = {
    "temperature_2m": (Decimal(-95), Decimal(65)),
    "relative_humidity_2m": (Decimal(0), Decimal(100)),
    "precipitation": (Decimal(0), Decimal(1000)),
    "wind_speed_10m": (Decimal(0), Decimal(500)),
}

# ----- Which data a date is judged on -----
# Every source is named; Open-Meteo's best_match is never used, because it switches sources silently.
FIRST_DAY = "1940-01-01"
ERA5_LAST = "2017-01-01"  # ERA5 reanalysis up to here; the ECMWF IFS archive starts on 2017-01-01 00Z
ARCHIVE_LAG_DAYS = 2  # the IFS archive is final up to T-2 (T: today in UTC)
FORECAST_FIRST_OFFSET = -1  # yesterday and today come from a forecast run issued before them
FORECAST_LAST_OFFSET = 13  # no published run covers every city's local day further ahead
OUTLOOK_FIRST_OFFSET = 14
PUBLISH_LAG_MINUTES = 9 * 60  # a run counts as published 9 h after its start
RUN_HORIZON_HOURS = {0: 360, 6: 144, 12: 360, 18: 144}  # how far ahead each ECMWF IFS cycle reaches
RUN_CANDIDATES = 3  # the newest covering runs a tool may try, newest first
# Every city's local day D lies inside [(D-1) 10:00Z, (D+1) 11:00Z]: Auckland's starts at (D-1) 11:00Z
# in summer, Honolulu's ends at (D+1) 10:00Z. A run must start before the window's first hour (its
# hour-0 precipitation is empty) and reach past its end.
WINDOW_FIRST_MINUTE = -14 * 60  # (D-1) 10:00Z, from D 00:00Z
WINDOW_LAST_MINUTE = 35 * 60  # (D+1) 11:00Z
OUTLOOK_PUBLISHED_DAY = 7  # SEAS5's monthly run is published by the 7th
# A local day starts at local midnight, or when clocks jump past it: up to two hours later (Java jumped
# 90 minutes at midnight on 1942-03-23), so a day lasts 20 to 28 hours.
MIDNIGHT_JUMP_MINUTES = 120
DELTA_MINUTES = 10  # the agent's tools read the clock this much behind the answer key's latest reading


class DataKind(Enum):
    ARCHIVE = "archive"
    FORECAST = "forecast"
    OUTLOOK = "outlook"


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
class Choice:
    """The data the rules judge a date on, at a clock reading: one named source, or a refusal."""

    date: str
    today: str  # the clock's UTC date
    kind: str  # archive, forecast or outlook ("" when refused)
    model: str
    runs: tuple[str, ...]  # a forecast's candidate runs, newest first; () otherwise
    step_hours: int
    problem: str  # why the date is refused; "" when it is not


@dataclass(frozen=True)
class DayMetrics:
    """One city's local day in metric units: the exact ingredients of every value."""

    city: str
    samples: int  # readings at an instant inside the day
    max_temp_c: Decimal | None
    min_temp_c: Decimal | None
    rain_mm: Decimal | None
    max_wind_kmh: Decimal | None
    humidity_sum_pct: Decimal | None
    humidity_samples: int
    missing: tuple[str, ...]  # source variables with a reading missing that day


@dataclass(frozen=True)
class Alert:
    city: str
    values: dict[str, Decimal]  # each needed variable, in the requested units, to 1 decimal, ties away from zero
    matched: tuple[str, ...]  # the conditions that hold, canonical, in the order given


@dataclass(frozen=True)
class Expected:
    alerts: tuple[Alert, ...]  # in city order
    unavailable: dict[str, tuple[str, ...]]  # city -> the needed variables it has no complete day for
    examined: tuple[str, ...]  # every city judged, in city order


# ----- The calendar, as integer days and minutes since 1970-01-01 (proleptic Gregorian, UTC) -----


def days_from_civil(year: int, month: int, day: int) -> int:
    """The day number of a calendar date (1970-01-01 is 0)."""
    year -= month <= 2
    era = year // 400
    year_of_era = year - era * 400
    day_of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + day_of_era - 719468


def civil_from_days(number: int) -> tuple[int, int, int]:
    """The calendar date (year, month, day) of a day number."""
    number += 719468
    era = number // 146097
    day_of_era = number - era * 146097
    year_of_era = (day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096) // 365
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    month_part = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * month_part + 2) // 5 + 1
    month = month_part + (3 if month_part < 10 else -9)
    return year_of_era + era * 400 + (month <= 2), month, day


def month_length(year: int, month: int) -> int:
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]


def date_problem(text: object) -> str | None:
    """What is wrong with a date as a calendar day the rules can judge, or None: YYYY-MM-DD in ASCII
    digits, a real day, from FIRST_DAY on. How far ahead a date may be depends on the clock, which
    only the tools read."""
    found = DATE.fullmatch(text) if isinstance(text, str) else None
    if not found:
        return f"date must be YYYY-MM-DD; got {text!r}"
    year, month, day = (int(part) for part in found.groups())
    if not 1 <= month <= 12 or not 1 <= day <= month_length(year, month):
        return f"{text} is not a calendar day"
    if text < FIRST_DAY:
        return f"{text} is before {FIRST_DAY}, the first day the archive covers"
    return None


def day_number(text: str) -> int:
    """The day number of a date that passes date_problem."""
    year, month, day = (int(part) for part in DATE.fullmatch(text).groups())
    return days_from_civil(year, month, day)


def date_text(number: int) -> str:
    year, month, day = civil_from_days(number)
    return f"{year:04d}-{month:02d}-{day:02d}"


def stamp_minutes(text: object) -> int | None:
    """Minutes since 1970-01-01T00:00Z of a UTC time "YYYY-MM-DDTHH:MM" (an optional trailing Z), or None."""
    if not isinstance(text, str):
        return None
    found = STAMP.fullmatch(text.removesuffix("Z"))
    if not found:
        return None
    year, month, day, hour, minute = (int(part) for part in found.groups())
    if not 1 <= month <= 12 or not 1 <= day <= month_length(year, month) or hour > 23 or minute > 59:
        return None
    return days_from_civil(year, month, day) * 1440 + hour * 60 + minute


def stamp_text(minutes: int) -> str:
    """A minute count as "YYYY-MM-DDTHH:MM" (UTC)."""
    return f"{date_text(minutes // 1440)}T{minutes % 1440 // 60:02d}:{minutes % 60:02d}"


def weekday(text: str) -> str:
    """The weekday of a date (1970-01-01 was a Thursday)."""
    return ("Thursday", "Friday", "Saturday", "Sunday", "Monday", "Tuesday", "Wednesday")[day_number(text) % 7]


# ----- Which data a date is judged on, at a clock reading -----


def outlook_last_day(today: int) -> int:
    """The last day the ECMWF seasonal outlook covers: the end of the sixth month after today's,
    or of the fifth before the month's new run is out (the 7th)."""
    year, month, day = civil_from_days(today)
    months = 6 if day >= OUTLOOK_PUBLISHED_DAY else 5
    index = year * 12 + (month - 1) + months
    last_year, last_month = index // 12, index % 12 + 1
    return days_from_civil(last_year, last_month, month_length(last_year, last_month))


def forecast_runs(date: int, clock: int) -> tuple[int, ...]:
    """The forecast runs (start minutes) that cover every city's local day on ``date`` and count as
    published at ``clock``, newest first, at most RUN_CANDIDATES."""
    first, last = date * 1440 + WINDOW_FIRST_MINUTE, date * 1440 + WINDOW_LAST_MINUTE
    newest = min(first, clock - PUBLISH_LAG_MINUTES)
    run = newest // 360 * 360
    found: list[int] = []
    while len(found) < RUN_CANDIDATES and run >= first - 21 * 1440:
        if run + RUN_HORIZON_HOURS[run % 1440 // 60] * 60 >= last:
            found.append(run)
        run -= 360
    return tuple(found)


def windows_of(today: int) -> dict[str, str]:
    """Each kind's first and last date at a clock whose UTC date is ``today``."""
    return {
        "archive_first": FIRST_DAY,
        "archive_last": date_text(today - ARCHIVE_LAG_DAYS),
        "forecast_first": date_text(today + FORECAST_FIRST_OFFSET),
        "forecast_last": date_text(today + FORECAST_LAST_OFFSET),
        "outlook_first": date_text(today + OUTLOOK_FIRST_OFFSET),
        "outlook_last": date_text(outlook_last_day(today)),
    }


def ranges_text(today: int) -> str:
    windows = windows_of(today)
    return (
        f"Today is {date_text(today)} (UTC); dates from {FIRST_DAY} to {windows['outlook_last']} work: "
        f"archive to {windows['archive_last']}, forecast run {windows['forecast_first']} to {windows['forecast_last']}, "
        f"seasonal outlook {windows['outlook_first']} to {windows['outlook_last']}."
    )


def choose(text: str, clock: int) -> Choice:
    """The data the rules judge ``text`` (a date that passes date_problem) on, with the clock at
    ``clock`` (UTC minutes)."""
    today = clock // 1440
    date = day_number(text)
    if date <= today - ARCHIVE_LAG_DAYS:
        model = "era5" if text <= ERA5_LAST else "ecmwf_ifs"
        return Choice(text, date_text(today), DataKind.ARCHIVE.value, model, (), 1, "")
    if date <= today + FORECAST_LAST_OFFSET:
        runs = tuple(stamp_text(run) for run in forecast_runs(date, clock))
        if not runs:
            problem = f"no ECMWF IFS forecast run covering {text} counts as published yet. {ranges_text(today)}"
            return Choice(text, date_text(today), "", "", (), 0, problem)
        return Choice(text, date_text(today), DataKind.FORECAST.value, "ecmwf_ifs", runs, 1, "")
    last = outlook_last_day(today)
    if date <= last:
        return Choice(text, date_text(today), DataKind.OUTLOOK.value, "ecmwf_seas5_ensemble_mean", (), 6, "")
    problem = f"{text} is after {date_text(last)}, the last day the ECMWF seasonal outlook covers. {ranges_text(today)}"
    return Choice(text, date_text(today), "", "", (), 0, problem)


def source_key(kind: str, model: str, run: str, step_hours: int) -> tuple[str, str, str, int]:
    return (kind, model, run, step_hours)


ENDPOINTS = {
    DataKind.ARCHIVE.value: "https://archive-api.open-meteo.com/v1/archive",
    DataKind.FORECAST.value: "https://single-runs-api.open-meteo.com/v1/forecast",
    DataKind.OUTLOOK.value: "https://seasonal-api.open-meteo.com/v1/seasonal",
}


def source_request(date: str, key: tuple) -> str:
    """The one Open-Meteo request that fetches source ``key`` (kind, model, run, step) for ``date``: all 30
    cities, in UTC, from the day before to the day after (a forecast run up to the hour that covers every
    city's day). The answer key, the agent's tools and the verifier all derive it from here."""
    kind, model, run, _step = key
    day = day_number(date)
    query = [
        ("latitude", ",".join(city[2] for city in CITIES)),
        ("longitude", ",".join(city[3] for city in CITIES)),
        ("hourly", ",".join(SOURCE_VARIABLES)),
        ("models", model),
        ("timezone", "GMT"),
    ]
    if kind == DataKind.FORECAST.value:
        hours = (day * 1440 + WINDOW_LAST_MINUTE - stamp_minutes(run)) // 60 + 1
        query += [("run", run), ("forecast_hours", str(hours))]
    else:
        query += [("start_date", max(date_text(day - 1), FIRST_DAY)), ("end_date", date_text(day + 1))]
    return ENDPOINTS[kind] + "?" + "&".join(f"{name}={value}" for name, value in query)


def source_label(kind: str, model: str, run: str, step_hours: int) -> str:
    """The data in plain words, for the result card."""
    if kind == DataKind.ARCHIVE.value:
        return "ERA5 reanalysis (ECMWF), hourly" if model == "era5" else "ECMWF IFS archive (analysis), hourly"
    if kind == DataKind.FORECAST.value:
        return f"ECMWF IFS forecast issued {run[:10]} {run[11:16]} UTC, hourly"
    return "ECMWF SEAS5 seasonal outlook (ensemble mean), 6-hourly"


def choice_problem(date: str, clock: int, key: tuple, skipped: list[str]) -> str | None:
    """Whether a fetched source ``key`` (kind, model, run, step) follows the date rules at ``clock``,
    given the runs the fetch skipped (newest first): None when it does, else why not."""
    choice = choose(date, clock)
    if choice.problem:
        return f"at {stamp_text(clock)}Z the rules refuse {date}"
    kind, model, run, step = key
    if (kind, model, step) != (choice.kind, choice.model, choice.step_hours):
        wanted = source_label(choice.kind, choice.model, choice.runs[0] if choice.runs else "", choice.step_hours)
        return f"at {stamp_text(clock)}Z the rules choose {wanted}, not {source_label(kind, model, run, step)}"
    if kind != DataKind.FORECAST.value:
        return None if not run and not skipped else "only a forecast names a run"
    if run not in choice.runs:
        return f"at {stamp_text(clock)}Z the rules choose among runs {', '.join(choice.runs)}, not {run}"
    position = choice.runs.index(run)
    if list(choice.runs[:position]) != list(skipped):
        return f"run {run} is used but the newer runs were not all tried first (skipped {skipped}, rules {list(choice.runs[:position])})"
    return None


# ----- A city's local day from its series -----


def stamps_inside(start: int, end: int, step_minutes: int, *, accumulated: bool) -> list[int]:
    """The grid stamps an aggregate reads: [start, end) for readings at an instant, (start, end] for
    totals over the step ending at the stamp."""
    if accumulated:
        first = start // step_minutes * step_minutes + step_minutes
        return list(range(first, end + 1, step_minutes))
    first = -(-start // step_minutes) * step_minutes
    return list(range(first, end, step_minutes))


def exact_reading(value: object) -> Decimal | None:
    """A reading from JSON (a number or null) as the decimal its shortest text means."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{value!r} is not a reading")
    return Decimal(repr(float(value)))


def day_metrics(series: dict, step_hours: int) -> DayMetrics:
    """One city's local day from its series (a plain dict as the pin and the receipt hold it): the
    readings at the stamps inside the day; a stamp that is absent, or a null reading, makes its
    variable missing for the day."""
    step = step_hours * 60
    start, end = stamp_minutes(series["start"]), stamp_minutes(series["end"])
    index = {stamp_minutes(time): position for position, time in enumerate(series["times"])}
    instant = stamps_inside(start, end, step, accumulated=False)
    totals = stamps_inside(start, end, step, accumulated=True)
    present: dict[str, list[Decimal]] = {}
    missing = []
    for variable in SOURCE_VARIABLES:
        wanted = totals if variable in ACCUMULATED else instant
        values = [exact_reading(series[variable][index[stamp]]) if stamp in index else None for stamp in wanted]
        present[variable] = [value for value in values if value is not None]
        if not wanted or len(present[variable]) < len(wanted):
            missing.append(variable)
    temps, humidity = present["temperature_2m"], present["relative_humidity_2m"]
    rain, wind = present["precipitation"], present["wind_speed_10m"]
    return DayMetrics(
        city=series["city"],
        samples=len(instant),
        max_temp_c=max(temps) if temps else None,
        min_temp_c=min(temps) if temps else None,
        rain_mm=sum(rain, Decimal(0)) if rain else None,
        max_wind_kmh=max(wind) if wind else None,
        humidity_sum_pct=sum(humidity, Decimal(0)) if humidity else None,
        humidity_samples=len(humidity),
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
        if metrics.humidity_sum_pct is None or not metrics.humidity_samples:
            return None
        return Fraction(metrics.humidity_sum_pct) / metrics.humidity_samples
    return None if value is None else Fraction(value)


def available(metrics: DayMetrics, variable: str) -> bool:
    """Whether the day has every reading of the variable, so a rule may use it."""
    return SOURCE_OF[variable] not in metrics.missing and exact_metric(metrics, variable) is not None


# ----- The canonical form both sides hash -----


def canonical_number(value: Decimal) -> str:
    """A number as its shortest plain text: 95.00 -> 95, 0.50 -> 0.5, -0 -> 0."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def canonical_reading(value: object) -> str | None:
    found = exact_reading(value)
    return None if found is None else canonical_number(found)


def canonical_series(date: str, key: tuple, cities: list[dict]) -> str:
    """The text both sides hash: the date, the source, and every city's window, grid point, stamps and
    readings, numbers as their shortest decimal text, so 85 and 85.0 are the same reading."""
    kind, model, run, step = key
    rows = []
    for series in cities:
        rows.append(
            [
                series["city"],
                series["timezone"],
                series["start"],
                series["end"],
                series["start_offset"],
                series["end_offset"],
                [canonical_reading(value) for value in series["grid"]],
                list(series["times"]),
                *[[canonical_reading(value) for value in series[variable]] for variable in SOURCE_VARIABLES],
            ]
        )
    return json.dumps({"date": date, "source": [kind, model, run, step], "cities": rows}, separators=(",", ":"), ensure_ascii=True)


def series_sha256(date: str, key: tuple, cities: list[dict]) -> str:
    return hashlib.sha256(canonical_series(date, key, cities).encode("ascii")).hexdigest()


def series_problem(date: str, step_hours: int, series: object) -> str | None:
    """What is wrong with one city's series for ``date`` (a plain dict), or None. Checked by hand: the
    window is the city's local day (its start and end fall on local midnights, 20 to 28 hours apart,
    with offsets that suit its longitude), inside the span every source covers, and the stamps are
    sorted, distinct and on the source's grid, with plausible readings."""
    keys = {"city", "timezone", "start", "end", "start_offset", "end_offset", "grid", "times", *SOURCE_VARIABLES}
    if not isinstance(series, dict) or set(series) != keys:
        return "a city's series must hold city, timezone, start, end, offsets, grid, times and the four variables"
    city = series["city"]
    if city not in TIMEZONE_OF:
        return f"{city!r} is not one of the cities"
    if series["timezone"] != TIMEZONE_OF[city]:
        return f"{city}: time zone {series['timezone']!r} is not {TIMEZONE_OF[city]}"
    start, end = stamp_minutes(series["start"]), stamp_minutes(series["end"])
    offsets = (series["start_offset"], series["end_offset"])
    if start is None or end is None:
        return f"{city}: start and end must be UTC times YYYY-MM-DDTHH:MM"
    if not all(isinstance(offset, int) and not isinstance(offset, bool) and -12 * 60 <= offset <= 14 * 60 for offset in offsets):
        return f"{city}: offsets must be whole minutes from -12:00 to +14:00"
    solar = LONGITUDE_OF[city] * 4
    if any(abs(Decimal(offset) - solar) > 240 for offset in offsets):
        return f"{city}: an offset of {offsets} minutes is more than 4 hours from the city's solar time"
    day = day_number(date)
    for edge, offset, wanted in ((start, offsets[0], day), (end, offsets[1], day + 1)):
        local = edge + offset
        if local // 1440 != wanted or local % 1440 > MIDNIGHT_JUMP_MINUTES:
            sign = "+" if offset >= 0 else "-"
            return f"{city}: {stamp_text(edge)}Z at {sign}{abs(offset) // 60:02d}:{abs(offset) % 60:02d} is not local midnight of {date_text(wanted)}"
    if not 20 * 60 <= end - start <= 28 * 60:
        return f"{city}: a local day lasts 20 to 28 hours, not {end - start} minutes"
    if start < day * 1440 + WINDOW_FIRST_MINUTE or end > day * 1440 + WINDOW_LAST_MINUTE:
        return f"{city}: the day falls outside {stamp_text(day * 1440 + WINDOW_FIRST_MINUTE)}Z to {stamp_text(day * 1440 + WINDOW_LAST_MINUTE)}Z"
    grid = series["grid"]
    if not isinstance(grid, list) or len(grid) != 3 or any(isinstance(value, bool) or not isinstance(value, int | float) for value in grid):
        return f"{city}: grid must be the grid point's latitude, longitude and elevation"
    times = series["times"]
    if not isinstance(times, list):
        return f"{city}: times must be a list"
    minutes = [stamp_minutes(time) for time in times]
    if any(minute is None for minute in minutes):
        return f"{city}: every time must be a UTC time YYYY-MM-DDTHH:MM"
    if minutes != sorted(set(minutes)):
        return f"{city}: times must be sorted and distinct"
    if any(minute % (step_hours * 60) for minute in minutes):
        return f"{city}: a time is off the {step_hours}-hourly grid"
    if minutes and (minutes[0] < start or minutes[-1] > end):
        return f"{city}: times stray outside the day, {stamp_text(start)}Z to {stamp_text(end)}Z"
    for variable in SOURCE_VARIABLES:
        values = series[variable]
        if not isinstance(values, list) or len(values) != len(times):
            return f"{city}: {variable} must have one reading per time"
        low, high = SOURCE_RANGES[variable]
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int | float):
                return f"{city}: {variable} holds a non-number"
            if not low <= exact_reading(value) <= high:
                return f"{city}: {variable} reading {value} is outside {low} to {high}"
    return None


def cities_problem(date: str, step_hours: int, cities: object) -> str | None:
    """What is wrong with every city's series for ``date``, or None: each of the 30 cities once, in order."""
    if not isinstance(cities, list) or [series.get("city") if isinstance(series, dict) else None for series in cities] != list(CITY_NAMES):
        return f"the data must hold the {CITY_COUNT} cities, once each, in order"
    for series in cities:
        problem = series_problem(date, step_hours, series)
        if problem:
            return problem
    return None


# ----- Conditions -----


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


def truncate(value: Fraction, places: int = GIVEN_PLACES) -> Decimal:
    """``value`` cut to ``places`` decimals toward zero, exactly: every digit kept is the exact value's,
    and every one of the ``places`` decimals is written, 0.45 as 0.450000000."""
    scaled = abs(value) * 10**places
    whole = scaled.numerator // scaled.denominator
    sign = "-" if value < 0 and whole else ""
    return Decimal(f"{sign}{whole // 10**places}.{whole % 10**places:0{places}d}")


def given_values(metrics: DayMetrics, conditions: list[Condition], units: Units) -> dict[str, Decimal]:
    """What get_daily gives a city for the conditions: each needed variable the day has every reading
    of, in the requested units, truncated to GIVEN_PLACES decimals."""
    return {
        variable: truncate(convert(variable, exact_metric(metrics, variable), units))
        for variable in needed_variables(conditions)
        if available(metrics, variable)
    }


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
    """``value`` to ``places`` decimals, ties away from zero (Decimal's ROUND_HALF_UP), exactly: 2.25 ->
    2.3 and -2.25 -> -2.3. Being symmetric, it gives the same result on a value truncated toward zero
    (as get_daily gives it) as on the exact value, negatives included."""
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


# ----- Evaluation -----


def evaluate(metrics: list[DayMetrics], conditions: list[Condition], match: str, units: Units) -> Expected:
    """The alerts and the unavailable cities the rules give for these days.

    A city with a missing reading for any variable the conditions use is unavailable, with those
    variables, and never an alert. Otherwise each condition is tested against the exact converted
    value; the city is an alert when every condition holds (match "all") or at least one does
    ("any"), with each needed variable's value rounded to one decimal, ties away from zero, and the
    conditions that hold.
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


def expected_from_series(task: dict, step_hours: int, cities: list[dict]) -> Expected:
    """What the rules give for a plain task over every city's series."""
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    return evaluate([day_metrics(series, step_hours) for series in cities], conditions, task["match"], units)


# ----- The task, the report file and the submitted report -----

# Every run (one plugin process: a task call's agent) keeps its files in its own folder, so two task calls
# at once in one workspace never touch each other's: reports/<run>/<date>.source.json (the receipt of the
# data read) and reports/<run>/<date>.json (the report). The run is 16 hex digits, drawn at process start.
REPORT_DIR = "reports"
RUN_TOKEN = re.compile(r"[0-9a-f]{16}")


def run_folder(run: str) -> str:
    return f"{REPORT_DIR}/{run}"


def receipt_path(date: str, run: str) -> str:
    return f"{REPORT_DIR}/{run}/{date}.source.json"


def report_path(date: str, run: str) -> str:
    return f"{REPORT_DIR}/{run}/{date}.json"


TASK_KEYS = ("date", "match", "temperature_unit", "wind_unit", "rain_unit", "conditions")
SOURCE_KEYS = ("kind", "model", "run", "step_hours", "today", "label")
REPORT_KEYS = ("date", "run", "task", "source", "sha256", "examined", "alerts", "unavailable")
SHA256 = re.compile(r"[0-9a-f]{64}")


def plain(value):
    return value.value if isinstance(value, Enum) else value


def plain_task(task) -> dict:
    """A task input (a contract AlertInput, whose choices are Enums) as the plain JSON the report
    file and the submitted report carry."""
    return {
        "date": task.date,
        "match": plain(task.match),
        "temperature_unit": plain(task.temperature_unit),
        "wind_unit": plain(task.wind_unit),
        "rain_unit": plain(task.rain_unit),
        "conditions": list(task.conditions),
    }


def plain_source(source) -> dict:
    """A contract DataSource as plain JSON."""
    return {
        "kind": plain(source.kind),
        "model": source.model,
        "run": source.run,
        "step_hours": source.step_hours,
        "today": source.today,
        "label": source.label,
    }


def source_problem(source: object) -> str | None:
    """What is wrong with a plain data source, or None."""
    if not isinstance(source, dict) or set(source) != set(SOURCE_KEYS):
        return "the data source must hold kind, model, run, step_hours, today and label"
    if source["kind"] not in [kind.value for kind in DataKind]:
        return "the data source's kind must be archive, forecast or outlook"
    if date_problem(source["today"]):
        return "the data source's today must be a date"
    expected = source_label(source["kind"], source["model"], source["run"], source["step_hours"])
    if source["label"] != expected:
        return f"the data source's label must be {expected!r}"
    return None


def key_of(source: dict) -> tuple[str, str, str, int]:
    return source_key(source["kind"], source["model"], source["run"], source["step_hours"])


def task_problem(task: object) -> str | None:
    """What is wrong with a plain task (as JSON), or None: its keys, a date the rules can judge, the
    choices, and 1 to 6 well-formed, distinct conditions."""
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
        if city not in CITY_NAMES:
            return f"{city!r} is not one of the 30 cities (names are spelled as the task lists them)"
        values = alert["values"]
        if not isinstance(values, dict) or not all(key in VARIABLES and is_number(value) and math.isfinite(value) for key, value in values.items()):
            return f"{city}: values must map condition variables to finite numbers"
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
        if entry["city"] not in CITY_NAMES:
            return f"{entry['city']!r} is not one of the 30 cities (names are spelled as the task lists them)"
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


def canonical_judgement(judgement: dict) -> tuple[dict, dict]:
    """A well-formed judgement's entries by city, as sets where order means nothing: each alert's values
    as exact decimals and its matched conditions as a sorted set of canonical forms, each unavailable
    city's variables as a sorted set."""
    alerts = {
        entry["city"]: (
            {variable: Decimal(repr(float(value))) for variable, value in entry["values"].items()},
            tuple(sorted(canonical_conditions(entry["matched"]))),
        )
        for entry in judgement["alerts"]
    }
    unavailable = {entry["city"]: tuple(sorted(entry["variables"])) for entry in judgement["unavailable"]}
    return alerts, unavailable


def report_problem(report: object) -> str | None:
    """What is wrong with a report file's shape (as parsed JSON), or None: the keys, a date, the run
    marker of the process that wrote it, the task, the data it judges, and well-formed lists."""
    if not isinstance(report, dict) or set(report) != set(REPORT_KEYS):
        return "the report must hold date, run, task, source, sha256, examined, alerts and unavailable"
    if not isinstance(report["date"], str):
        return "the report's date must be a string"
    if not isinstance(report["run"], str) or not RUN_TOKEN.fullmatch(report["run"]):
        return "the report's run must be the token of the run that wrote it"
    problem = task_problem(report["task"])
    if problem:
        return f"task: {problem}"
    if report["date"] != report["task"]["date"]:
        return "the report's date is not its task's"
    problem = source_problem(report["source"])
    if problem:
        return problem
    if not isinstance(report["sha256"], str) or not SHA256.fullmatch(report["sha256"]):
        return "the report's sha256 must name the data it judges"
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


def expected_output(task: dict, source: dict, expected: Expected) -> dict:
    """``expected`` as the task's output (an AlertReportOutput as JSON): what a perfect run submits."""
    return {"task": dict(task), "data": dict(source), **plain_entries(expected)}

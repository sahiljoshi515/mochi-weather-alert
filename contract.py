"""Each tool's and task's typed input and output (see nanoagent.plugin.contract): declarations only."""

from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from slimconfig import Output


class Tool(Enum):
    PIN_ALERTS = "pin_alerts"
    GET_DAILY = "get_daily"
    REPORT_ALERTS = "report_alerts"
    CHECK_DATE = "check_date"


class Task(Enum):
    DAILY_ALERT = "daily-alert"
    CHECK_DATE = "check-date"


class Match(Enum):
    ALL = "all"
    ANY = "any"


class TempUnit(Enum):
    C = "C"
    F = "F"


class WindUnit(Enum):
    KMH = "kmh"
    MPH = "mph"
    MS = "ms"


class RainUnit(Enum):
    MM = "mm"
    IN = "in"


class DataKind(Enum):
    ARCHIVE = "archive"
    FORECAST = "forecast"
    OUTLOOK = "outlook"


DATE = (
    "A calendar day YYYY-MM-DD from 1940-01-01 to about six months ahead: the past from the archive, the next two "
    "weeks from a forecast run, later days from the seasonal outlook. Out-of-range dates are refused with today's "
    "UTC date and the allowed ranges. You are not told today's date: for a day relative to today, call check-date first"
)
CITY = "A city's name as the task names it, e.g. Santiago"
CONDITION = (
    'A rule "<variable> <op> <number>": variable max_temp, min_temp, rain, wind or humidity; op >=, >, <= or <; '
    'the number in the requested units (humidity in %), with at most 2 decimals; e.g. "max_temp >= 95"'
)
SAMPLES = (
    "Readings at an instant inside the local day: 23 to 25 hourly ones (23 when clocks go forward, 25 when they go "
    "back), or 3 to 5 six-hourly ones for the seasonal outlook"
)
MISSING = (
    "Source variables with a reading missing that day: temperature_2m, relative_humidity_2m, precipitation or "
    "wind_speed_10m"
)
TASK = "The task input, exactly as given"


@dataclass
class AlertInput(Output):
    date: Annotated[str, DATE]
    match: Annotated[Match, "all: a city is an alert only when every condition holds for it; any: when at least one does"]
    temperature_unit: Annotated[TempUnit, "C or F: the unit of max_temp and min_temp thresholds and of the reported temperatures"]
    wind_unit: Annotated[WindUnit, "kmh, mph or ms: the unit of wind thresholds and of the reported wind speeds"]
    rain_unit: Annotated[RainUnit, "mm or in: the unit of rain thresholds and of the reported precipitation"]
    conditions: Annotated[list[Annotated[str, CONDITION]], "1 to 6 distinct conditions, e.g. [\"max_temp >= 95\", \"rain > 0.5\"]"]


@dataclass
class DataSource(Output):
    kind: Annotated[DataKind, "archive (the past), forecast (yesterday to two weeks ahead) or outlook (later)"]
    model: Annotated[str, "The Open-Meteo model: era5, ecmwf_ifs or ecmwf_seas5_ensemble_mean"]
    run: Annotated[str, "A forecast's run, YYYY-MM-DDTHH:MM UTC; empty for the archive and the outlook"]
    step_hours: Annotated[int, "Hours between readings: 1, or 6 for the outlook"]
    today: Annotated[str, "Today's UTC date when the data was chosen"]
    label: Annotated[str, "The data in plain words, e.g. ECMWF IFS forecast issued 2026-10-16 00:00 UTC, hourly"]


@dataclass
class PinnedCity(Output):
    city: str
    timezone: Annotated[str, "The city's IANA time zone"]
    start: Annotated[str, "The local day's first instant, a UTC time YYYY-MM-DDTHH:MM"]
    end: Annotated[str, "The next local day's first instant, a UTC time YYYY-MM-DDTHH:MM"]
    start_offset: Annotated[int, "Minutes east of UTC at start"]
    end_offset: Annotated[int, "Minutes east of UTC at end"]
    grid: Annotated[list[float], "Open-Meteo's grid point: latitude, longitude and elevation"]
    times: Annotated[list[str], "The source's stamps from start to end, UTC"]
    temperature_2m: Annotated[list[float | None], "°C at each stamp; null where missing"]
    relative_humidity_2m: Annotated[list[float | None], "% at each stamp; null where missing"]
    precipitation: Annotated[list[float | None], "mm over the step ending at each stamp; null where missing"]
    wind_speed_10m: Annotated[list[float | None], "km/h at each stamp; null where missing"]


@dataclass
class Alert(Output):
    city: Annotated[str, CITY]
    values: Annotated[
        dict[str, float],
        'Each variable the conditions use, in the requested units, rounded half up to 1 decimal, e.g. {"max_temp": 96.3}',
    ]
    matched: Annotated[list[str], "The conditions that hold for the city, as given in the task"]


@dataclass
class Unavailable(Output):
    city: Annotated[str, CITY]
    variables: Annotated[list[str], "The condition variables the city has a missing reading for"]


@dataclass
class PinnedSource(Output):
    source: DataSource
    clock: Annotated[str, "The clock reading the date rules were applied at, a UTC time YYYY-MM-DDTHH:MM"]
    fetched_at: Annotated[str, "When Open-Meteo answered, UTC"]
    request: Annotated[str, "The request that fetched the data"]
    skipped: Annotated[list[str], "Newer forecast runs not (completely) published when fetched, newest first"]
    sha256: Annotated[str, "sha256 of the data's canonical form"]
    cities: Annotated[list[PinnedCity], "Every city's local day"]
    alerts: Annotated[list[Alert], "The cities whose day satisfies the rules on this data"]
    unavailable: Annotated[list[Unavailable], "The cities with a missing reading for a variable the conditions use"]


@dataclass
class PinOutput(Output):
    date: str
    clock: Annotated[str, "The answer key's clock reading, a UTC time YYYY-MM-DDTHH:MM"]
    candidates: Annotated[list[PinnedSource], "The data the rules choose at the clock and 10 minutes before it: one source, or two"]


@dataclass
class AlertReportOutput(Output):
    task: Annotated[AlertInput, TASK]
    data: Annotated[DataSource, "The data the report judges, as report_alerts returned it"]
    alerts: Annotated[list[Alert], "Every city whose day satisfies the rules, with its values and matched conditions; [] for none"]
    unavailable: Annotated[list[Unavailable], "Every city with a missing reading for a variable the conditions use; [] for none"]


@dataclass
class DailyInput(Output):
    task: Annotated[AlertInput, TASK]
    cities: Annotated[list[Annotated[str, CITY]], "1 to 30 distinct cities: every city the task names, in one call"]


@dataclass
class DailyCity(Output):
    city: str
    samples: Annotated[int, SAMPLES]
    missing: Annotated[list[str], MISSING]
    values: Annotated[
        dict[str, float],
        "Each variable the task's conditions use that the day has every reading of, in the task's units, exact to 9 "
        "decimals (truncated, never rounded): compare the conditions against these as they are, and report each "
        'one rounded half up to 1 decimal, e.g. {"max_temp": 96.26, "wind": 24.979121927}',
    ]


@dataclass
class DailyOutput(Output):
    date: str
    source: Annotated[DataSource, "The data the values come from"]
    cities: list[DailyCity]


@dataclass
class ReportInput(Output):
    task: Annotated[AlertInput, TASK]
    examined: Annotated[
        list[Annotated[str, CITY]],
        "The cities this call judges, 1 to 30 distinct: their earlier entries are replaced by this call's, and a "
        "listed city with no entry has no alert. Every city in alerts or unavailable must be listed here",
    ]
    alerts: Annotated[list[Alert], "Every examined city whose day satisfies the rules; [] for none"]
    unavailable: Annotated[
        list[Unavailable], "Every examined city with a missing reading for a variable the conditions use; [] for none"
    ]


@dataclass
class ReportOutput(Output):
    path: Annotated[str, "The report file, relative to the workspace: reports/<date>.json"]
    examined: Annotated[int, "How many of the 30 cities the merged report has examined so far"]
    complete: Annotated[bool, "Whether every city is examined: then submit the report"]
    report: Annotated[AlertReportOutput, "The merged report so far: the task, its data, every alert and every unavailable city"]


@dataclass
class DateInput(Output):
    date: Annotated[str, "A date YYYY-MM-DD, or today"]


@dataclass
class DateOutput(Output):
    today: Annotated[str, "Today's date in UTC"]
    clock: Annotated[str, "The clock reading, a UTC time YYYY-MM-DDTHH:MM"]
    date: Annotated[str, "The date asked about (today's when the input is today)"]
    weekday: Annotated[str, "The date's weekday"]
    kind: Annotated[DataKind | None, "The data a daily-alert task on the date would use; null when the date is refused"]
    source: Annotated[str, "That data in plain words, or why the date is refused"]
    archive_first: Annotated[str, "The first date the archive covers"]
    archive_last: Annotated[str, "The last date the archive covers"]
    forecast_first: Annotated[str, "The first date a forecast run covers"]
    forecast_last: Annotated[str, "The last date a forecast run covers"]
    outlook_first: Annotated[str, "The first date the seasonal outlook covers"]
    outlook_last: Annotated[str, "The last date the seasonal outlook covers"]
    message: Annotated[str, "The answer in one sentence"]


TOOLS = {
    Tool.PIN_ALERTS: (AlertInput, PinOutput),
    Tool.GET_DAILY: (DailyInput, DailyOutput),
    Tool.REPORT_ALERTS: (ReportInput, ReportOutput),
    Tool.CHECK_DATE: (DateInput, DateOutput),
}
TASKS = {
    Task.DAILY_ALERT: (AlertInput, AlertReportOutput),
    Task.CHECK_DATE: (DateInput, DateOutput),
}

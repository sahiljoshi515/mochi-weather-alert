"""Each tool's and task's typed input and output (see nanoagent.plugin.contract): declarations only."""

from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from slimconfig import Output


class Tool(Enum):
    PIN_ALERTS = "pin_alerts"
    LIST_CITIES = "list_cities"
    GET_DAILY = "get_daily"
    GET_HOURLY = "get_hourly"
    REPORT_ALERTS = "report_alerts"


class Task(Enum):
    DAILY_ALERT = "daily-alert"


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


DATE = "A local calendar day in the snapshot, YYYY-MM-DD, from 2026-07-01 to 2026-09-30"
CITY = "A city's name as list_cities gives it, e.g. Santiago"
CONDITION = (
    'A rule "<variable> <op> <number>": variable max_temp, min_temp, rain, wind or humidity; op >=, >, <= or <; '
    'the number in the requested units (humidity in %), with at most 2 decimals; e.g. "max_temp >= 95"'
)
HOURS = "Hourly rows in the local calendar day: 23 on a day clocks go forward, 25 when they go back, else 24"
MISSING = (
    "Source variables with at least one missing hour that day: temperature_2m, relative_humidity_2m, "
    "precipitation or wind_speed_10m"
)


@dataclass
class AlertInput(Output):
    date: Annotated[str, DATE]
    match: Annotated[Match, "all: every condition must hold for a city; any: at least one must"]
    temperature_unit: Annotated[TempUnit, "C or F: the unit of max_temp and min_temp thresholds and reported values"]
    wind_unit: Annotated[WindUnit, "kmh, mph or ms: the unit of wind thresholds and reported values"]
    rain_unit: Annotated[RainUnit, "mm or in: the unit of rain thresholds and reported values"]
    conditions: Annotated[list[Annotated[str, CONDITION]], "1 to 6 distinct conditions"]


@dataclass
class PinnedCity(Output):
    city: str
    country: str
    hours: Annotated[int, HOURS]
    max_temp_c: Annotated[float | None, "The day's highest hourly temperature, °C; null when no hour has one"]
    min_temp_c: Annotated[float | None, "The day's lowest hourly temperature, °C; null when no hour has one"]
    rain_mm: Annotated[float | None, "The precipitation total over the hours that have one, mm; null when none has"]
    max_wind_kmh: Annotated[float | None, "The day's highest hourly wind speed, km/h; null when no hour has one"]
    humidity_sum_pct: Annotated[float | None, "The sum of the hourly relative humidities present, %; null when none is"]
    humidity_hours: Annotated[int, "How many hours have a relative humidity"]
    missing: Annotated[list[str], MISSING]


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
    variables: Annotated[list[str], "The condition variables the city has a missing hour for"]


@dataclass
class PinOutput(Output):
    date: str
    metric: Annotated[list[PinnedCity], "Every city's local day, in metric units"]
    alerts: Annotated[list[Alert], "The cities whose day satisfies the rules"]
    unavailable: Annotated[list[Unavailable], "The cities with a missing hour for a variable the conditions use"]


@dataclass
class CitiesInput(Output):
    date: Annotated[str, DATE]


@dataclass
class CityDay(Output):
    city: str
    country: str
    timezone: Annotated[str, "The city's IANA time zone; its day is the calendar day there"]
    hours_available: Annotated[int, HOURS]


@dataclass
class CitiesOutput(Output):
    date: str
    cities: list[CityDay]


@dataclass
class DailyInput(Output):
    cities: Annotated[list[Annotated[str, CITY]], "1 to 30 distinct cities: every city list_cities gives, in one call"]
    date: Annotated[str, DATE]


@dataclass
class DailyCity(Output):
    city: str
    country: str
    hours: Annotated[int, HOURS]
    max_temp_c: Annotated[float | None, "The day's highest hourly temperature, °C; null when no hour has one"]
    min_temp_c: Annotated[float | None, "The day's lowest hourly temperature, °C; null when no hour has one"]
    rain_mm: Annotated[float | None, "The precipitation total over the hours that have one, mm; null when none has"]
    max_wind_kmh: Annotated[float | None, "The day's highest hourly wind speed, km/h; null when no hour has one"]
    mean_humidity_pct: Annotated[
        float | None, "The mean of the hourly relative humidities present, %, to 4 decimals; null when none is"
    ]
    missing: Annotated[list[str], MISSING]


@dataclass
class DailyOutput(Output):
    date: str
    cities: list[DailyCity]


@dataclass
class HourlyInput(Output):
    city: Annotated[str, CITY]
    date: Annotated[str, DATE]


@dataclass
class HourlyRow(Output):
    time: Annotated[str, "Local time with its UTC offset, e.g. 2026-09-06T01:00-03:00"]
    temp_c: Annotated[float | None, "Temperature, °C"]
    humidity_pct: Annotated[float | None, "Relative humidity, %"]
    rain_mm: Annotated[float | None, "Precipitation in the hour, mm"]
    wind_kmh: Annotated[float | None, "Wind speed, km/h"]


@dataclass
class HourlyOutput(Output):
    city: str
    date: str
    timezone: str
    rows: list[HourlyRow]


@dataclass
class ReportInput(Output):
    date: Annotated[str, DATE]
    examined: Annotated[
        list[Annotated[str, CITY]],
        "The cities this call judges, 1 to 30 distinct: their earlier entries are replaced by this call's, and a "
        "listed city with no entry has no alert. Every city in alerts or unavailable must be listed here",
    ]
    alerts: Annotated[list[Alert], "Every examined city whose day satisfies the rules; [] for none"]
    unavailable: Annotated[
        list[Unavailable], "Every examined city with a missing hour for a variable the conditions use; [] for none"
    ]


@dataclass
class ReportOutput(Output):
    path: Annotated[str, "The report file, relative to the workspace: weather-alert/<date>.json"]
    alerts: Annotated[int, "Alert cities in the file after this call, over every call for the date"]
    unavailable: Annotated[int, "Unavailable cities in the file after this call"]
    examined: Annotated[int, "Cities examined so far, over every call for the date"]


TOOLS = {
    Tool.PIN_ALERTS: (AlertInput, PinOutput),
    Tool.LIST_CITIES: (CitiesInput, CitiesOutput),
    Tool.GET_DAILY: (DailyInput, DailyOutput),
    Tool.GET_HOURLY: (HourlyInput, HourlyOutput),
    Tool.REPORT_ALERTS: (ReportInput, ReportOutput),
}
TASKS = {Task.DAILY_ALERT: AlertInput}

"""The tools: the pinned answer key, the city list, a day's aggregates, a day's hours, and the report."""

from __future__ import annotations

import json
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
from plugin.rules import MAX_CITIES_PER_CALL, DayMetrics, Units, aggregate, date_problem, evaluate, parse_conditions

REPORT_DIR = "weather-alert"
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
            CityDay(city=city.name, country=city.country, timezone=city.timezone, hours=len(data.day(city.name, date)))
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


def report_alerts(given: ReportInput, workspace: Path) -> ReportOutput:
    """Write the report as weather-alert/<date>.json, replacing an earlier one."""
    date = checked_date(given.date)
    cities = [alert.city for alert in given.alerts] + [entry.city for entry in given.unavailable]
    if len(set(cities)) != len(cities):
        raise ValueError("a city may appear once, as an alert or as unavailable")
    path = workspace.resolve() / REPORT_DIR / f"{date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plain_output(given), indent=2) + "\n", encoding="utf-8")
    return ReportOutput(path=report_path(date), alerts=len(given.alerts), unavailable=len(given.unavailable))

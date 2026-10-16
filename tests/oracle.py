"""An independent oracle for the daily-alert answer, written from the README's rules and Open-Meteo's
documentation, sharing no code with plugin.rules or the verifier.

Where the plugin computes each city's local day as a window [start, end) of UTC minutes and checks it by
integer arithmetic, the oracle tests every stamp's membership directly: a reading at an instant belongs
to the day when its UTC time, converted with the zone read straight from the tzdata wheel
(ZoneInfo.from_file), falls on the date; a total belongs when the minute before its stamp does. Sums
are Fractions, rounding is Decimal ROUND_HALF_UP at 60 digits, conversions are exact.
"""

from __future__ import annotations

import importlib.resources
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from functools import cache
from zoneinfo import ZoneInfo

ZONES = {
    "Athens": "Europe/Athens", "Auckland": "Pacific/Auckland", "Bangkok": "Asia/Bangkok",
    "Buenos Aires": "America/Argentina/Buenos_Aires", "Cairo": "Africa/Cairo", "Cape Town": "Africa/Johannesburg",
    "Denver": "America/Denver", "Dubai": "Asia/Dubai", "Honolulu": "Pacific/Honolulu", "Houston": "America/Chicago",
    "Jakarta": "Asia/Jakarta", "Lagos": "Africa/Lagos", "Lima": "America/Lima", "London": "Europe/London",
    "Madrid": "Europe/Madrid", "Mexico City": "America/Mexico_City", "Moscow": "Europe/Moscow", "Mumbai": "Asia/Kolkata",
    "Nairobi": "Africa/Nairobi", "Oslo": "Europe/Oslo", "Perth": "Australia/Perth", "Phoenix": "America/Phoenix",
    "Reykjavik": "Atlantic/Reykjavik", "Santiago": "America/Santiago", "Seoul": "Asia/Seoul", "Singapore": "Asia/Singapore",
    "Sydney": "Australia/Sydney", "Tokyo": "Asia/Tokyo", "Toronto": "America/Toronto", "Vancouver": "America/Vancouver",
}  # fmt: skip
CITIES = list(ZONES)  # alphabetical, the order Open-Meteo's answer lists the locations in
SOURCE = {"max_temp": "temperature_2m", "min_temp": "temperature_2m", "rain": "precipitation", "wind": "wind_speed_10m", "humidity": "relative_humidity_2m"}
ORDER = ("max_temp", "min_temp", "rain", "wind", "humidity")


@cache
def zone(name: str) -> ZoneInfo:
    """The zone straight from the tzdata wheel's file, not through zoneinfo's search path."""
    parts = name.split("/")
    resource = importlib.resources.files("tzdata.zoneinfo").joinpath(*parts)
    with resource.open("rb") as handle:
        return ZoneInfo.from_file(handle, key=name)


def stamp(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def local_date(instant: datetime, name: str) -> date:
    return instant.astimezone(zone(name)).date()


def expected_stamps(day: date, name: str, step_hours: int, total: bool) -> list[datetime]:
    """Every grid stamp (multiples of the step from midnight UTC) belonging to the local day."""
    found = []
    instant = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(days=2)
    stop = instant + timedelta(days=5)
    while instant < stop:
        probe = instant - timedelta(minutes=1) if total else instant
        if local_date(probe, name) == day:
            found.append(instant)
        instant += timedelta(hours=step_hours)
    return found


def exact(value) -> Fraction:
    return Fraction(Decimal(repr(float(value))))


def readings(location: dict, variable: str, stamps: list[datetime]) -> list[Fraction] | None:
    """The readings at ``stamps``, or None when one is absent or null."""
    hourly = location["hourly"]
    position = {stamp(text): index for index, text in enumerate(hourly["time"])}
    found = []
    for instant in stamps:
        index = position.get(instant)
        if index is None or hourly[variable][index] is None:
            return None
        found.append(exact(hourly[variable][index]))
    return found if stamps else None


def metric(location: dict, name: str, day: date, step_hours: int, variable: str) -> Fraction | None:
    """A rule variable's exact metric value for the city's local day, or None when a reading is missing."""
    source = SOURCE[variable]
    values = readings(location, source, expected_stamps(day, name, step_hours, total=source == "precipitation"))
    if values is None:
        return None
    if variable == "max_temp" or variable == "wind":
        return max(values)
    if variable == "min_temp":
        return min(values)
    if variable == "rain":
        return sum(values, Fraction(0))
    return sum(values, Fraction(0)) / len(values)


def convert(variable: str, value: Fraction, task: dict) -> Fraction:
    if variable in ("max_temp", "min_temp") and task["temperature_unit"] == "F":
        return value * Fraction(9, 5) + 32
    if variable == "wind" and task["wind_unit"] == "mph":
        return value / Fraction("1.609344")
    if variable == "wind" and task["wind_unit"] == "ms":
        return value / Fraction("3.6")
    if variable == "rain" and task["rain_unit"] == "in":
        return value / Fraction("25.4")
    return value


def half_up(value: Fraction) -> float:
    with localcontext() as context:
        context.prec = 60
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return float(decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def condition(text: str):
    variable, op, number = text.split()
    return text, variable, op, Fraction(number)


def holds(value: Fraction, op: str, threshold: Fraction) -> bool:
    return {">=": value >= threshold, ">": value > threshold, "<=": value <= threshold, "<": value < threshold}[op]


def answer(task: dict, locations: list[dict], step_hours: int) -> dict:
    """The alerts and unavailable cities for a task over Open-Meteo's answer (one location per city)."""
    day = date.fromisoformat(task["date"])
    conditions = [condition(" ".join(text.split())) for text in task["conditions"]]
    needed = [variable for variable in ORDER if any(entry[1] == variable for entry in conditions)]
    alerts, unavailable = [], []
    for name, location in zip(CITIES, locations, strict=True):
        values = {variable: metric(location, ZONES[name], day, step_hours, variable) for variable in needed}
        lacking = [variable for variable in needed if values[variable] is None]
        if lacking:
            unavailable.append({"city": name, "variables": lacking})
            continue
        converted = {variable: convert(variable, values[variable], task) for variable in needed}
        matched = [text for text, variable, op, threshold in conditions if holds(converted[variable], op, threshold)]
        if (task["match"] == "all" and len(matched) == len(conditions)) or (task["match"] == "any" and matched):
            alerts.append({"city": name, "values": {variable: half_up(converted[variable]) for variable in needed}, "matched": matched})
    return {"alerts": alerts, "unavailable": unavailable}

"""Checks shared by the tool verifiers. Pure functions of their arguments and the files on disk."""

from .rules import SOURCE_VARIABLES, date_problem


def result(valid, message, *, score=None, **details):
    return {
        "valid": valid,
        "score": float(valid) if score is None else score,
        "message": message,
        "details": details,
    }


def date_check(date):
    """A date's problem as a result, or None when it is a snapshot day."""
    problem = date_problem(date)
    return result(False, problem) if problem else None


def names_problem(names, *, what, most=None):
    """What is wrong with a list of city names, or None: non-empty strings, distinct, at most ``most``."""
    if not isinstance(names, list) or not all(isinstance(name, str) and name.strip() for name in names):
        return f"{what} must be a list of city names"
    if len(set(names)) != len(names):
        return f"{what} must be distinct"
    if most is not None and not 1 <= len(names) <= most:
        return f"{what} must hold 1 to {most} names"
    return None


def day_problem(row, *, humidity):
    """What is wrong with one city's day aggregates (a PinnedCity or DailyCity), or None.
    ``humidity`` is the row's humidity figure to range-check (a mean or a sum), or None."""
    if not 1 <= row.hours <= 25:
        return f"{row.city}: a local day has 1 to 25 hours, not {row.hours}"
    if row.max_temp_c is not None and row.min_temp_c is not None and row.max_temp_c < row.min_temp_c:
        return f"{row.city}: the highest temperature is below the lowest"
    if (row.max_temp_c is None) != (row.min_temp_c is None):
        return f"{row.city}: the highest and lowest temperature must both be present or both missing"
    if row.rain_mm is not None and row.rain_mm < 0:
        return f"{row.city}: precipitation is negative"
    if row.max_wind_kmh is not None and row.max_wind_kmh < 0:
        return f"{row.city}: wind speed is negative"
    if humidity is not None and humidity < 0:
        return f"{row.city}: humidity is negative"
    unknown = sorted(set(row.missing) - set(SOURCE_VARIABLES))
    if unknown or len(set(row.missing)) != len(row.missing):
        return f"{row.city}: missing must name distinct source variables"
    for variable, value in (("temperature_2m", row.max_temp_c), ("precipitation", row.rain_mm), ("wind_speed_10m", row.max_wind_kmh)):
        if value is None and variable not in row.missing:
            return f"{row.city}: {variable} has no value but is not listed as missing"
    if humidity is None and "relative_humidity_2m" not in row.missing:
        return f"{row.city}: relative_humidity_2m has no value but is not listed as missing"
    return None

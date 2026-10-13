"""get_hourly: one city on a snapshot date; the rows of that local day, in order."""

from .checks import date_check, result

TIME_PREFIX = 10  # "YYYY-MM-DD"


def verify_input(value):
    if not isinstance(value.city, str) or not value.city.strip():
        return result(False, "city must be a city name")
    return date_check(value.date) or result(True, "input is valid")


def verify_output(input_value, output_value, workspace):
    if output_value.date != input_value.date or output_value.city != input_value.city:
        return result(False, "the hours are for another city or date")
    times = [row.time for row in output_value.rows]
    if not 23 <= len(times) <= 25:
        return result(False, "a local calendar day has 23, 24 or 25 hours")
    if times != sorted(times) or len(set(times)) != len(times):
        return result(False, "the rows are not in time order")
    if not all(time[:TIME_PREFIX] == input_value.date for time in times):
        return result(False, "a row lies outside the requested local day")
    for row in output_value.rows:
        if row.humidity_pct is not None and not 0 <= row.humidity_pct <= 100:
            return result(False, f"{row.time}: humidity is outside 0 to 100%")
        if (row.rain_mm is not None and row.rain_mm < 0) or (row.wind_kmh is not None and row.wind_kmh < 0):
            return result(False, f"{row.time}: a precipitation or wind value is negative")
    return result(True, "hours are well formed", hours=len(times))

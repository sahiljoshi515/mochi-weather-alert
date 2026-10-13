"""list_cities: a date in the snapshot; distinct cities with plausible local-day lengths."""

from .checks import date_check, names_problem, result


def verify_input(value):
    return date_check(value.date) or result(True, "input is valid")


def verify_output(input_value, output_value, workspace):
    if output_value.date != input_value.date:
        return result(False, "the cities are listed for another date")
    names = [row.city for row in output_value.cities]
    problem = names_problem(names, what="cities")
    if problem or not names:
        return result(False, problem or "no cities are listed")
    if not all(23 <= row.hours_available <= 25 for row in output_value.cities):
        return result(False, "a local calendar day has 23, 24 or 25 hours")
    if not all(row.timezone.strip() and row.country.strip() for row in output_value.cities):
        return result(False, "every city has a country and a time zone")
    return result(True, "cities are listed", cities=len(names))

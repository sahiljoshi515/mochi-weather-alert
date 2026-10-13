"""get_daily: 1 to 10 distinct cities on a snapshot date; aggregates for exactly those cities, in order."""

from .checks import date_check, day_problem, names_problem, result
from .rules import MAX_CITIES_PER_CALL


def verify_input(value):
    problem = names_problem(value.cities, what="cities", most=MAX_CITIES_PER_CALL)
    if problem:
        return result(False, problem)
    return date_check(value.date) or result(True, "input is valid")


def verify_output(input_value, output_value, workspace):
    if output_value.date != input_value.date:
        return result(False, "the aggregates are for another date")
    if [row.city for row in output_value.cities] != input_value.cities:
        return result(False, "the aggregates do not cover exactly the requested cities, in order")
    for row in output_value.cities:
        problem = day_problem(row, humidity=row.mean_humidity_pct)
        if problem:
            return result(False, problem)
        if row.mean_humidity_pct is not None and row.mean_humidity_pct > 100:
            return result(False, f"{row.city}: mean humidity is over 100%")
    return result(True, "aggregates are well formed", cities=len(output_value.cities))

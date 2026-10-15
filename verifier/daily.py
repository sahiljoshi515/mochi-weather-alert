"""get_daily: the task and 1 to 30 distinct cities; one row per requested city, in order, giving exactly
the needed variables the day has every hour of, in the task's units, as 9-decimal numbers."""

from decimal import Decimal

from .checks import names_problem, result
from .rules import (
    GIVEN_PLACES,
    MAX_CITIES_PER_CALL,
    SOURCE_OF,
    SOURCE_VARIABLES,
    needed_variables,
    parse_conditions,
    plain_task,
    task_problem,
)

RANGES = {"max_temp": (-130, 160), "min_temp": (-130, 160), "rain": (0, 10000), "wind": (0, 1000), "humidity": (0, 100)}


def verify_input(value):
    problem = task_problem(plain_task(value.task))
    if problem:
        return result(False, problem)
    problem = names_problem(value.cities, what="cities", most=MAX_CITIES_PER_CALL)
    if problem:
        return result(False, problem)
    return result(True, "input is valid")


def verify_output(input_value, output_value, workspace):
    task = plain_task(input_value.task)
    if output_value.date != task["date"]:
        return result(False, "the values are for another date")
    if [row.city for row in output_value.cities] != input_value.cities:
        return result(False, "the rows do not cover exactly the requested cities, in order")
    needed = needed_variables(parse_conditions(task["conditions"]))
    for row in output_value.cities:
        if not 1 <= row.hours <= 25:
            return result(False, f"{row.city}: a local day has 1 to 25 hours, not {row.hours}")
        unknown = sorted(set(row.missing) - set(SOURCE_VARIABLES))
        if unknown or len(set(row.missing)) != len(row.missing):
            return result(False, f"{row.city}: missing must name distinct source variables")
        expected = {variable for variable in needed if SOURCE_OF[variable] not in row.missing}
        if set(row.values) != expected:
            return result(False, f"{row.city}: values must hold exactly the needed variables the day has every hour of: {', '.join(sorted(expected)) or 'none'}")
        for variable, value in row.values.items():
            low, high = RANGES[variable]
            exact = Decimal(str(value))
            if not low <= exact <= high or -exact.as_tuple().exponent > GIVEN_PLACES:
                return result(False, f"{row.city}: {variable} is not a plausible value with at most {GIVEN_PLACES} decimals")
    return result(True, "values are well formed", cities=len(output_value.cities))

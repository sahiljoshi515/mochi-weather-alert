"""get_daily: the task and 1 to 30 distinct known cities in; out, one row per requested city, in order,
equal to the rules applied to the data this run read (its receipt): the readings count, the missing
source variables, and each needed variable the day has every reading of, in the task's units."""

from decimal import Decimal

from .checks import result
from .receipt import receipt_of
from .rules import (
    CITY_NAMES,
    MAX_CITIES_PER_CALL,
    Units,
    day_metrics,
    given_values,
    names_ok,
    parse_conditions,
    plain_source,
    plain_task,
    task_problem,
)


def cities_problem(names, most):
    if not names_ok(names) or len(set(names)) != len(names) or not 1 <= len(names) <= most:
        return f"cities must be 1 to {most} distinct city names"
    unknown = [name for name in names if name not in CITY_NAMES]
    if unknown:
        return f"unknown city {', '.join(unknown)}; the cities are {', '.join(CITY_NAMES)}"
    return None


def verify_input(value):
    problem = task_problem(plain_task(value.task)) or cities_problem(list(value.cities), MAX_CITIES_PER_CALL)
    return result(False, problem) if problem else result(True, "input is valid")


def verify_output(input_value, output_value, workspace):
    task = plain_task(input_value.task)
    date = task["date"]
    if output_value.date != date:
        return result(False, "the values are for another date")
    receipt, problem = receipt_of(workspace, date)
    if problem:
        return result(False, problem)
    if plain_source(output_value.source) != receipt["source"]:
        return result(False, "the values name another source than the data get_daily read")
    if [row.city for row in output_value.cities] != list(input_value.cities):
        return result(False, "the rows do not cover exactly the requested cities, in order")
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    series = {entry["city"]: entry for entry in receipt["cities"]}
    for row in output_value.cities:
        day = day_metrics(series[row.city], receipt["source"]["step_hours"])
        if row.samples != day.samples or list(row.missing) != list(day.missing):
            return result(False, f"{row.city}: samples and missing are not the day's")
        expected = given_values(day, conditions, units)
        if set(row.values) != set(expected) or any(Decimal(str(row.values[name])) != value for name, value in expected.items()):
            return result(False, f"{row.city}: the values are not the rules' values for the data read")
    return result(True, "the values are the data's", cities=len(output_value.cities), source=receipt["source"]["label"])

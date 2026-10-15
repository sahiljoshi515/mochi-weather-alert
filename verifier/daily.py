"""get_daily: the task and 1 to 30 distinct cities; the aggregates of exactly those cities, in order,
each with the needed variables in the task's units, exactly as the metric ingredients give them."""

from decimal import Decimal

from .checks import day_problem, names_problem, result
from .rules import (
    MAX_CITIES_PER_CALL,
    Units,
    given_values,
    metrics_from_plain,
    parse_conditions,
    plain_task,
    task_problem,
)


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
        return result(False, "the aggregates are for another date")
    if [row.city for row in output_value.cities] != input_value.cities:
        return result(False, "the aggregates do not cover exactly the requested cities, in order")
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    for row in output_value.cities:
        problem = day_problem(row, humidity=row.humidity_sum_pct)
        if problem:
            return result(False, problem)
        if not 0 <= row.humidity_hours <= row.hours or (row.humidity_sum_pct is None) != (row.humidity_hours == 0):
            return result(False, f"{row.city}: the humidity sum and its hour count disagree")
        if row.humidity_sum_pct is not None and row.humidity_sum_pct > 100 * row.humidity_hours:
            return result(False, f"{row.city}: mean humidity is over 100%")
        metrics = metrics_from_plain(
            row.city,
            row.hours,
            row.max_temp_c,
            row.min_temp_c,
            row.rain_mm,
            row.max_wind_kmh,
            row.humidity_sum_pct,
            row.humidity_hours,
            row.missing,
        )
        expected = given_values(metrics, conditions, units)
        given = {variable: Decimal(str(value)) for variable, value in row.values.items()}
        if given != expected:
            return result(False, f"{row.city}: values are not the needed variables in the task's units, exactly as the metric aggregates give them")
    return result(True, "aggregates are well formed", cities=len(output_value.cities))

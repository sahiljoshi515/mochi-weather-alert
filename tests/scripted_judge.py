"""A scripted agent's judgement, written from the task's instructions alone (it imports nothing of the
plugin): given get_daily's answer and the task input, the report_alerts calls of the prescribed path,
or the same calls broken in one way a weak agent breaks them.

The scripted cases inline this file into a python step (tools_dev/build_cases.py), so a case judges
whatever data the plugin fetched on the day it runs; the offline replay test imports it.
"""

import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

CITIES = [
    "Athens", "Auckland", "Bangkok", "Buenos Aires", "Cairo", "Cape Town", "Denver", "Dubai", "Honolulu", "Houston",
    "Jakarta", "Lagos", "Lima", "London", "Madrid", "Mexico City", "Moscow", "Mumbai", "Nairobi", "Oslo",
    "Perth", "Phoenix", "Reykjavik", "Santiago", "Seoul", "Singapore", "Sydney", "Tokyo", "Toronto", "Vancouver",
]  # fmt: skip
SOURCE = {"max_temp": "temperature_2m", "min_temp": "temperature_2m", "rain": "precipitation", "wind": "wind_speed_10m", "humidity": "relative_humidity_2m"}
ORDER = ("max_temp", "min_temp", "rain", "wind", "humidity")
HALF = 15


class NotApplicable(Exception):
    """The day's data offers no city to break the report this way."""


def parse(text):
    variable, op, number = re.fullmatch(r"\s*(\w+)\s*(>=|<=|>|<)\s*([+-]?[0-9]*\.?[0-9]+)\s*", text).groups()
    return text, variable, op, Decimal(number)


def test(value, op, threshold, strict_ge=False):
    if op == ">=":
        return value > threshold if strict_ge else value >= threshold
    if op == ">":
        return value > threshold
    if op == "<=":
        return value <= threshold
    return value < threshold


def rounded(value, rounding=ROUND_HALF_UP):
    return float(value.quantize(Decimal("0.1"), rounding=rounding))


def judge(task, daily, *, match=None, strict_ge=False, rounding=ROUND_HALF_UP):
    """Every city's judgement under the instructions: {"alerts", "unavailable"}, in city order."""
    match = match or task["match"]
    conditions = [parse(text) for text in task["conditions"]]
    needed = [variable for variable in ORDER if any(condition[1] == variable for condition in conditions)]
    alerts, unavailable = [], []
    rows = {row["city"]: row for row in daily["cities"]}
    for city in CITIES:
        row = rows[city]
        lacking = [variable for variable in needed if SOURCE[variable] in row["missing"] or variable not in row["values"]]
        if lacking:
            unavailable.append({"city": city, "variables": lacking})
            continue
        values = {variable: Decimal(str(row["values"][variable])) for variable in needed}
        matched = [text for text, variable, op, threshold in conditions if test(values[variable], op, threshold, strict_ge)]
        if (match == "all" and len(matched) == len(conditions)) or (match == "any" and matched):
            alerts.append({"city": city, "values": {variable: rounded(values[variable], rounding) for variable in needed}, "matched": matched})
    return {"alerts": alerts, "unavailable": unavailable}


def call(task, judgement, cities):
    covered = set(cities)
    return {
        "task": task,
        "examined": list(cities),
        "alerts": [entry for entry in judgement["alerts"] if entry["city"] in covered],
        "unavailable": [entry for entry in judgement["unavailable"] if entry["city"] in covered],
    }


def halves(task, judgement):
    return [call(task, judgement, CITIES[:HALF]), call(task, judgement, CITIES[HALF:])]


def available_non_alert(task, daily, judgement):
    """A city with every needed value that is no alert, with its values rounded as a report gives them."""
    taken = {entry["city"] for entry in judgement["alerts"] + judgement["unavailable"]}
    rows = {row["city"]: row for row in daily["cities"]}
    needed = [variable for variable in ORDER if any(parse(text)[1] == variable for text in task["conditions"])]
    for city in CITIES:
        if city not in taken and all(variable in rows[city]["values"] for variable in needed):
            return city, {variable: rounded(Decimal(str(rows[city]["values"][variable]))) for variable in needed}
    raise NotApplicable("every city is an alert or unavailable")


def mutated(name, task, daily):
    """The judgement broken one way (``name``), or NotApplicable when the day's data offers no case."""
    gold = judge(task, daily)
    if name == "boundary":  # >= taken as >
        broken = judge(task, daily, strict_ge=True)
    elif name == "bankers":  # half-even rounding
        broken = judge(task, daily, rounding=ROUND_HALF_EVEN)
    elif name == "any_vs_all":
        broken = judge(task, daily, match="all" if task["match"] == "any" else "any")
    elif name == "missing_alert":
        if not gold["alerts"]:
            raise NotApplicable("no alert to drop")
        broken = {**gold, "alerts": gold["alerts"][1:]}
    elif name == "extra_alert":
        city, values = available_non_alert(task, daily, gold)
        extra = {"city": city, "values": values, "matched": [task["conditions"][0]]}
        broken = {**gold, "alerts": sorted(gold["alerts"] + [extra], key=lambda entry: CITIES.index(entry["city"]))}
    elif name == "fabricated":  # one value off by 0.1
        if not gold["alerts"]:
            raise NotApplicable("no alert to change")
        first = dict(gold["alerts"][0])
        variable = next(iter(first["values"]))
        first["values"] = {**first["values"], variable: round(first["values"][variable] + 0.1, 1)}
        broken = {**gold, "alerts": [first] + gold["alerts"][1:]}
    elif name == "wrong_matched":  # a holding condition left out of matched
        target = next((entry for entry in gold["alerts"] if len(entry["matched"]) > 1), None)
        if target is None or task["match"] == "all":
            raise NotApplicable("no alert matches two conditions under any")
        changed = {**target, "matched": target["matched"][:-1]}
        broken = {**gold, "alerts": [changed if entry is target else entry for entry in gold["alerts"]]}
    elif name == "drop_unavailable":
        if not gold["unavailable"]:
            raise NotApplicable("no unavailable city")
        broken = {**gold, "unavailable": gold["unavailable"][1:]}
    elif name == "empty":
        if not gold["alerts"] and not gold["unavailable"]:
            raise NotApplicable("the gold report is already empty")
        broken = {"alerts": [], "unavailable": []}
    else:
        raise ValueError(f"unknown mutation {name}")
    if broken == gold:
        raise NotApplicable(f"{name} changes nothing on this day")
    return broken


def plan(task, daily, mutations=()):
    """The report_alerts calls: gold when ``mutations`` is empty, else the first applicable mutation's.
    Returns {"calls": [...], "applied": name or "gold", "skipped": [...names not applicable]}."""
    skipped = []
    for name in mutations:
        try:
            return {"calls": halves(task, mutated(name, task, daily)), "applied": name, "skipped": skipped}
        except NotApplicable:
            skipped.append(name)
    if mutations:
        raise NotApplicable(f"none of {list(mutations)} applies to this day's data")
    return {"calls": halves(task, judge(task, daily)), "applied": "gold", "skipped": skipped}

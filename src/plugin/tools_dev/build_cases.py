"""Build the scripted-harness cases under tests/cases/ from the pinned snapshot with the shared rules.

    uv run python src/plugin/tools_dev/build_cases.py

Each case is what ~/mochi-dev/harness/scripted_task.py runs: the task's arguments, the scripted
agent's steps and the expected verdict. The prescribed path is get_daily once with all 30 cities,
then report_alerts three times with 10 examined cities each (so a hosted model converts 10 cities
per step, inside its 2,048-token reply cap), then the answer: 4 tool calls of Mochi's 6 steps.
gold does it in one report_alerts call, gold_chunked in three, batched_daily reads the cities in
two get_daily calls after list_cities, corrected fixes an earlier chunk's mistakes in a later call,
and unexamined stops after two chunks. Each wrong case breaks the gold report in one way the plan
lists as adversarial. Every expected verdict is checked here with the real verifier, the report
steps applied through report_alerts itself, before the case is written.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from slimconfig import build_output, plain_output

import verifier
from contract import AlertInput, HourlyInput, PinOutput, ReportInput
from plugin.alerts import get_hourly, pin_alerts, report_alerts
from plugin.data import snapshot
from plugin.rules import (
    MAX_CITIES_PER_CALL,
    Units,
    aggregate,
    convert,
    evaluate,
    exact_metric,
    parse_conditions,
    plain_report,
    round_half_up,
)
from verifier.pin import expected_of, metrics_of

CASES = ROOT / "tests" / "cases"
TASK = "daily-alert"
DST_DATE = "2026-09-06"  # Santiago's 23-hour day
CHUNKS = 3  # report_alerts calls on the prescribed path


def names() -> list[str]:
    return [city.name for city in snapshot().cities]


def pin(arguments: dict) -> PinOutput:
    return pin_alerts(build_output(arguments, AlertInput), Path(tempfile.mkdtemp()))


def chunk_report(report: dict, cities: list[str]) -> dict:
    """The part of a full report that judges ``cities``: one report_alerts call."""
    covered = set(cities)
    return {
        "date": report["date"],
        "examined": list(cities),
        "alerts": [entry for entry in report["alerts"] if entry["city"] in covered],
        "unavailable": [entry for entry in report["unavailable"] if entry["city"] in covered],
    }


def report_steps(report: dict | None, chunks: int = 1) -> list[dict]:
    """The report_alerts calls that write ``report``: one call, or ``chunks`` calls over its examined cities."""
    if report is None:
        return []
    if chunks == 1:
        return [{"tool": "report_alerts", "arguments": report}]
    examined = report["examined"]
    size = -(-len(examined) // chunks)
    return [{"tool": "report_alerts", "arguments": chunk_report(report, examined[start : start + size])} for start in range(0, len(examined), size)]


def read_steps(date: str, batch: int = MAX_CITIES_PER_CALL, list_first: bool = False) -> list[dict]:
    found = [{"tool": "list_cities", "arguments": {"date": date}, "save_as": "cities"}] if list_first else []
    everyone = names()
    for start in range(0, len(everyone), batch):
        found.append({"tool": "get_daily", "arguments": {"cities": everyone[start : start + batch], "date": date}})
    return found


def verdict(arguments: dict, pinned: PinOutput, steps: list[dict]) -> dict:
    """The real verifier's verdict after the report steps are applied through report_alerts itself."""
    workspace = Path(tempfile.mkdtemp())
    for step in steps:
        if step.get("tool") == "report_alerts" and not step.get("expect_error"):
            report_alerts(build_output(step["arguments"], ReportInput), workspace)
    return verifier.verify_task(TASK, build_output(arguments, AlertInput), build_output(plain_output(pinned), PinOutput), str(workspace))


def write_case(name: str, note: str, arguments: dict, pinned: PinOutput, steps: list[dict], *, expect: dict, answer: str) -> None:
    found = verdict(arguments, pinned, steps)
    if found["valid"] != expect["valid"]:
        raise RuntimeError(f"{name}: the verifier says valid={found['valid']}, the case expects {expect['valid']}: {found['message']}")
    if "message_contains" in expect and expect["message_contains"] not in found["message"]:
        raise RuntimeError(f"{name}: verdict {found['message']!r} lacks {expect['message_contains']!r}")
    if "score_min" in expect and found["score"] < expect["score_min"]:
        raise RuntimeError(f"{name}: score {found['score']} below {expect['score_min']}")
    if "score_max" in expect and found["score"] > expect["score_max"]:
        raise RuntimeError(f"{name}: score {found['score']} above {expect['score_max']}")
    case = {"note": note, "task": TASK, "arguments": arguments, "steps": steps, "answer": answer, "expect": expect}
    (CASES / f"{name}.json").write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
    calls = sum(1 for step in steps if "tool" in step)
    print(f"{name:18} {calls} tool calls  valid={found['valid']!s:5} score={found['score']:.3f}  {found['message'][:100]}")


def summary(report: dict) -> str:
    alerts = report["alerts"]
    if not alerts:
        return "No city crossed the thresholds."
    parts = [f"{a['city']} ({', '.join(f'{k} {v}' for k, v in a['values'].items())}; {', '.join(a['matched'])})" for a in alerts]
    return f"{len(alerts)} alert(s): " + "; ".join(parts) + "."


def humidity_tie(row) -> bool:
    """A 24-hour day whose mean humidity ends in .25: rounding half up (.3) and half even (.2) differ
    (a mean ending in .75 rounds to .8 either way)."""
    return row.humidity_hours == 24 and row.humidity_sum_pct is not None and int(row.humidity_sum_pct) % 24 == 6


def fahrenheit_boundary(row) -> Decimal | None:
    """The day's high in °F when it has at most one decimal (a natural threshold to sit exactly on)."""
    if row.max_temp_c is None:
        return None
    value = Fraction(Decimal(str(row.max_temp_c))) * 9 / 5 + 32
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return exact if exact == exact.quantize(Decimal("0.1")) else None


def find_gold() -> tuple[dict, PinOutput, str, str]:
    """The first day with a city whose mean humidity is a rounding tie and another whose high in °F is
    a whole tenth, with rules that make both alerts: the gold case's arguments, pin, tie city and
    boundary city."""
    data = snapshot()
    probe = {"date": "", "match": "any", "temperature_unit": "F", "wind_unit": "mph", "rain_unit": "in", "conditions": ["max_temp >= 95"]}
    day = datetime.fromisoformat(data.start)
    while day.strftime("%Y-%m-%d") <= data.end:
        date = day.strftime("%Y-%m-%d")
        pinned = pin({**probe, "date": date})
        for tie in [row for row in pinned.metric if humidity_tie(row)]:
            mean = Fraction(int(tie.humidity_sum_pct), 24)
            # The humidity rule that the tie city satisfies; its threshold is a round number below the mean.
            threshold = (mean.numerator // mean.denominator) // 5 * 5
            if threshold < 40:
                continue
            for boundary in pinned.metric:
                exact = fahrenheit_boundary(boundary)
                if exact is None or boundary.city == tie.city or exact < 80:
                    continue
                arguments = {**probe, "date": date, "conditions": [f"max_temp >= {exact}", f"humidity >= {threshold}", "rain > 0.5", "wind >= 20"]}
                expected = expected_of(build_output(arguments, AlertInput), pinned)
                cities = {alert.city: alert for alert in expected.alerts}
                # The boundary city must be an alert through its high alone, so excluding it is the ">" mistake.
                lone = boundary.city in cities and cities[boundary.city].matched == (f"max_temp >= {exact}",)
                if lone and tie.city in cities and 4 <= len(expected.alerts) <= 14:
                    return arguments, pin(arguments), tie.city, boundary.city
        day += timedelta(days=1)
    raise RuntimeError("no day in the snapshot offers both a humidity tie and a Fahrenheit boundary city")


def utc_day_metrics(city: str, date: str):
    """The city's aggregates over the 24 UTC hours of ``date``: the mistake of ignoring the local day."""
    rows = []
    for offset in (-1, 0, 1):
        local = (datetime.fromisoformat(date) + timedelta(days=offset)).strftime("%Y-%m-%d")
        if snapshot().start <= local <= snapshot().end:
            for time, values in snapshot().day(city, local):
                instant = datetime.fromisoformat(time).astimezone(UTC)
                if instant.strftime("%Y-%m-%d") == date:
                    rows.append(values)
    if len(rows) != 24:
        raise RuntimeError(f"{city}: {len(rows)} UTC rows on {date}")
    return aggregate(city, rows)


def rounded(variable: str, metric, units: Units) -> float:
    exact = convert(variable, exact_metric(metric, variable), units)
    return float(round_half_up(exact))


def main() -> int:
    CASES.mkdir(parents=True, exist_ok=True)
    for stale in CASES.glob("*.json"):
        stale.unlink()

    # ----- The rich gold day: a rounding tie, a boundary city, mixed rules -----
    arguments, pinned, tie_city, boundary_city = find_gold()
    date = arguments["date"]
    units = Units("F", "mph", "in")
    expected = expected_of(build_output(arguments, AlertInput), pinned)
    gold = plain_report(date, expected)
    read = read_steps(date)
    print(f"gold day {date}: rules {arguments['conditions']}; tie city {tie_city}; boundary city {boundary_city}; {len(gold['alerts'])} alerts")
    valid = {"valid": True, "score_min": 1.0}
    write_case("gold", f"The exact report for {date} in one report_alerts call: {tie_city}'s humidity is a rounding tie (half up), {boundary_city}'s high sits exactly on its threshold.",
               arguments, pinned, read + report_steps(gold), expect=valid, answer=summary(gold))
    write_case("gold_chunked", "The prescribed path: get_daily with all 30 cities, then three report_alerts calls of 10 examined cities each.",
               arguments, pinned, read + report_steps(gold, CHUNKS), expect=valid, answer=summary(gold))
    write_case("batched_daily", "The exact report after list_cities and two get_daily calls of 15 instead of one.",
               arguments, pinned, read_steps(date, batch=15, list_first=True) + report_steps(gold), expect=valid, answer=summary(gold))

    chunks = report_steps(gold, CHUNKS)
    write_case("unexamined", "Only two of the three report_alerts calls: ten cities are never examined.",
               arguments, pinned, read + chunks[:2], expect={"valid": False, "message_contains": "unexamined", "score_max": 0.7}, answer=summary(gold))

    # corrected: the first chunk gets one value wrong and alerts a city that does not qualify; the last
    # chunk examines both again, with the right values for one and no entry for the other
    first = chunks[0]["arguments"]
    alerted = {entry["city"] for entry in gold["alerts"]}
    wrong_city = next(entry["city"] for entry in first["alerts"])
    bogus_city = next(city for city in first["examined"] if city not in alerted and city not in {u["city"] for u in gold["unavailable"]})
    broken = json.loads(json.dumps(chunks))
    for entry in broken[0]["arguments"]["alerts"]:
        if entry["city"] == wrong_city:
            key = next(iter(entry["values"]))
            entry["values"][key] = round(entry["values"][key] + 0.1, 1)
    spare_row = next(row for row in pinned.metric if row.city == bogus_city)
    spare_metric = metrics_of(pinned)[[row.city for row in pinned.metric].index(bogus_city)]
    conditions = parse_conditions(arguments["conditions"])
    bogus_values = {variable: rounded(variable, spare_metric, units) for variable in dict.fromkeys(c.variable for c in conditions)}
    broken[0]["arguments"]["alerts"].append({"city": bogus_city, "values": bogus_values, "matched": [arguments["conditions"][1]]})
    last = broken[-1]["arguments"]
    last["examined"] = last["examined"] + [wrong_city, bogus_city]
    last["alerts"] = last["alerts"] + [entry for entry in gold["alerts"] if entry["city"] == wrong_city]
    write_case("corrected", f"The first call gets {wrong_city}'s value wrong and alerts {bogus_city}, which does not qualify; the last call examines both again with {wrong_city} corrected and no entry for {bogus_city}: the final file wins.",
               arguments, pinned, read + broken, expect=valid, answer=summary(gold))
    del spare_row

    def alert(report: dict, city: str) -> dict:
        return next(a for a in report["alerts"] if a["city"] == city)

    # wrong unit: the metric values reported as if they were the requested units
    wrong = json.loads(json.dumps(gold))
    by_city = {row.city: row for row in pinned.metric}
    for entry in wrong["alerts"]:
        row = by_city[entry["city"]]
        entry["values"]["max_temp"] = row.max_temp_c
        entry["values"]["wind"] = row.max_wind_kmh
        entry["values"]["rain"] = row.rain_mm
    write_case("wrong_unit", "Values left in °C, km/h and mm although °F, mph and in were requested.", arguments, pinned, read + report_steps(wrong),
               expect={"valid": False, "message_contains": "with a wrong value", "score_max": 0.5}, answer=summary(wrong))

    # banker's rounding: the tie city's humidity rounded half to even
    wrong = json.loads(json.dumps(gold))
    row = by_city[tie_city]
    mean = Fraction(int(row.humidity_sum_pct), 24)
    even = float((Decimal(mean.numerator) / Decimal(mean.denominator)).quantize(Decimal("0.1"), ROUND_HALF_EVEN))
    if even == alert(gold, tie_city)["values"]["humidity"]:
        raise RuntimeError("the tie did not change under half-even rounding")
    alert(wrong, tie_city)["values"]["humidity"] = even
    write_case("bankers_rounding", f"{tie_city}'s mean humidity {mean.numerator}/24 rounded half to even ({even}) instead of half up.", arguments, pinned, read + report_steps(wrong),
               expect={"valid": False, "message_contains": "with a wrong value"}, answer=summary(wrong))

    # boundary: the city whose high equals the threshold left out, as if >= were >
    wrong = json.loads(json.dumps(gold))
    wrong["alerts"] = [a for a in wrong["alerts"] if a["city"] != boundary_city]
    write_case("boundary", f"{boundary_city}'s high is exactly the max_temp threshold and satisfies >=, but was treated as if the rule were >.", arguments, pinned, read + report_steps(wrong),
               expect={"valid": False, "message_contains": "alert(s) missing"}, answer=summary(wrong))

    # missing city: one alert dropped
    wrong = json.loads(json.dumps(gold))
    dropped = wrong["alerts"].pop(len(wrong["alerts"]) // 2)
    write_case("missing_city", f"{dropped['city']} left out of the alerts.", arguments, pinned, read + report_steps(wrong),
               expect={"valid": False, "message_contains": "alert(s) missing", "score_max": 0.99}, answer=summary(wrong))

    # extra city: a city that does not qualify added with its true values
    spare = next(row for row in pinned.metric if row.city not in alerted and row.city not in {u["city"] for u in gold["unavailable"]})
    spare_metric = metrics_of(pinned)[[row.city for row in pinned.metric].index(spare.city)]
    wrong = json.loads(json.dumps(gold))
    values = {variable: rounded(variable, spare_metric, units) for variable in dict.fromkeys(c.variable for c in conditions)}
    wrong["alerts"].append({"city": spare.city, "values": values, "matched": [arguments["conditions"][1]]})
    write_case("extra_city", f"{spare.city} added although no rule holds for it.", arguments, pinned, read + report_steps(wrong),
               expect={"valid": False, "message_contains": "extra alert(s)", "score_max": 0.99}, answer=summary(wrong))

    # any vs all: the rules evaluated with "all" although the task says "any"
    as_all = plain_report(date, evaluate(metrics_of(pinned), conditions, "all", units))
    if as_all == gold:
        raise RuntimeError("all and any give the same report")
    write_case("any_vs_all", 'Rules combined with "all" although the task asked for "any".', arguments, pinned, read + report_steps(as_all),
               expect={"valid": False, "message_contains": "alert(s) missing"}, answer=summary(as_all))

    # fabricated values: one value nudged and one matched rule added
    wrong = json.loads(json.dumps(gold))
    first_alert = wrong["alerts"][0]
    key = next(iter(first_alert["values"]))
    first_alert["values"][key] = round(first_alert["values"][key] + 0.1, 1)
    second = wrong["alerts"][1]
    second["matched"] = second["matched"] + [c for c in arguments["conditions"] if c not in second["matched"]][:1]
    write_case("fabricated", f"{first_alert['city']}'s {key} nudged by 0.1 and a rule {second['city']} does not meet listed as matched.", arguments, pinned, read + report_steps(wrong),
               expect={"valid": False, "message_contains": "wrong"}, answer=summary(wrong))

    # empty report: every city examined, nothing reported
    empty = {"date": date, "examined": list(gold["examined"]), "alerts": [], "unavailable": []}
    write_case("empty_report", "An empty report although cities qualify.", arguments, pinned, read + report_steps(empty),
               expect={"valid": False, "message_contains": "alert(s) missing", "score_max": 0.0}, answer=summary(empty))

    # no report at all
    write_case("no_report", "The agent reads the data but never calls report_alerts.", arguments, pinned, read,
               expect={"valid": False, "message_contains": "no report", "score_max": 0.0}, answer="I could not decide.")

    # duplicate city: refused by report_alerts, so no report is written
    doubled = json.loads(json.dumps(gold))
    doubled["alerts"].append(json.loads(json.dumps(doubled["alerts"][0])))
    write_case("duplicate_city", "The same city reported twice: report_alerts refuses it, so no report is saved.", arguments, pinned,
               read + [{"tool": "report_alerts", "arguments": doubled, "expect_error": True}],
               expect={"valid": False, "message_contains": "no report", "score_max": 0.0}, answer=summary(doubled))

    # ----- The DST day: Santiago's 23-hour day, on the prescribed chunked path -----
    dst_arguments = {"date": DST_DATE, "match": "any", "temperature_unit": "F", "wind_unit": "ms", "rain_unit": "mm", "conditions": ["min_temp < 50", "humidity >= 85"]}
    dst_pin = pin(dst_arguments)
    dst_gold = plain_report(DST_DATE, expected_of(build_output(dst_arguments, AlertInput), dst_pin))
    santiago = next(row for row in dst_pin.metric if row.city == "Santiago")
    if santiago.hours != 23 or "Santiago" not in {a["city"] for a in dst_gold["alerts"]}:
        raise RuntimeError("Santiago is not a 23-hour alert on the DST day")
    dst_read = read_steps(DST_DATE)
    write_case("gold_dst", "The exact report for Santiago's 23-hour day (clocks went forward at midnight), in three report_alerts calls.",
               dst_arguments, dst_pin, dst_read + report_steps(dst_gold, CHUNKS), expect=valid, answer=summary(dst_gold))

    # Santiago aggregated over the 24 UTC hours of the date instead of its local day
    utc = utc_day_metrics("Santiago", DST_DATE)
    wrong = json.loads(json.dumps(dst_gold))
    entry = alert(wrong, "Santiago")
    dst_units = Units("F", "ms", "mm")
    for variable in list(entry["values"]):
        entry["values"][variable] = rounded(variable, utc, dst_units)
    if entry["values"] == alert(dst_gold, "Santiago")["values"]:
        raise RuntimeError("the UTC day gives Santiago the same values as its local day")
    write_case("dst_utc_day", "Santiago's values taken over the 24 UTC hours of the date instead of its 23-hour local day.",
               dst_arguments, dst_pin, dst_read + report_steps(wrong, CHUNKS), expect={"valid": False, "message_contains": "with a wrong value"}, answer=summary(wrong))

    # sanity: the hourly rows of the DST day really skip the missing hour
    hours = get_hourly(build_output({"city": "Santiago", "date": DST_DATE}, HourlyInput), Path(tempfile.mkdtemp()))
    print(f"Santiago {DST_DATE}: {len(hours.rows)} rows, first {hours.rows[0].time}")
    print(f"\nwrote {len(list(CASES.glob('*.json')))} cases to {CASES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

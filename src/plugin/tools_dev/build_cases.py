"""Build the scripted-harness cases under tests/cases/ from the pinned snapshot with the shared rules.

    uv run python src/plugin/tools_dev/build_cases.py

Each case is what ~/mochi-dev/harness/scripted_task.py runs: the task's arguments, the scripted
agent's steps and the expected verdict. The prescribed path is get_daily once with all 30 cities,
(which gives each needed value in the task's units), then report_alerts twice with 15 examined
cities each (so a hosted model's reply, reasoning included, stays inside its 2,048-token cap), then
submit with the report the last call returned: four tool calls of the hosted agent's six steps.
gold does it in one report_alerts call, gold_chunked in two, batched_daily reads the cities in
two get_daily calls, corrected fixes the first chunk's mistakes in the second, unexamined stops
after one chunk, overlapping_calls restates five cities in two chunks, stale_old_version and
stale_previous_run plant a leftover file that the first call must replace, stale_no_calls and
no_report submit without reporting, and divergent_submission submits something other than the
report the last call returned. Each wrong case breaks the gold report in one way the plan lists as
adversarial. Every expected verdict is checked here with the real verifier, the run marked and the
report steps applied through report_alerts itself, before the case is written.
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
from contract import AlertInput, AlertReportOutput, HourlyInput, PinOutput, ReportInput
from plugin.alerts import get_hourly, pin_alerts, report_alerts, write_run_marker
from plugin.data import snapshot
from plugin.rules import (
    CHUNK,
    MAX_CITIES_PER_CALL,
    Units,
    aggregate,
    convert,
    evaluate,
    exact_metric,
    expected_output,
    parse_conditions,
    plain_task,
    round_half_up,
)
from verifier.pin import expected_of, metrics_of

CASES = ROOT / "tests" / "cases"
TASK = "daily-alert"
DST_DATE = "2026-09-06"  # Santiago's 23-hour day
SUBMIT_LAST = {"tool": "submit", "arguments": "$ref:last.report"}


def names() -> list[str]:
    return [city.name for city in snapshot().cities]


def pin(arguments: dict) -> PinOutput:
    return pin_alerts(build_output(arguments, AlertInput), Path(tempfile.mkdtemp()))


def chunk_call(task: dict, judgement: dict, cities: list[str]) -> dict:
    """The part of a whole judgement ({"alerts", "unavailable"}) that covers ``cities``: one report_alerts call."""
    covered = set(cities)
    return {
        "task": task,
        "examined": list(cities),
        "alerts": [entry for entry in judgement["alerts"] if entry["city"] in covered],
        "unavailable": [entry for entry in judgement["unavailable"] if entry["city"] in covered],
    }


def report_steps(task: dict, judgement: dict, chunks: int = 1, examined: list[str] | None = None) -> list[dict]:
    """The report_alerts calls that write ``judgement`` over ``examined`` (every city by default): one
    call, or ``chunks`` calls; the last one's output is saved as `last` for the submit step."""
    examined = names() if examined is None else examined
    size = -(-len(examined) // chunks)
    return chunked_steps(task, judgement, examined, size)


def prescribed_steps(task: dict, judgement: dict) -> list[dict]:
    """The report_alerts calls of the prescribed path: CHUNK cities each, in order."""
    return chunked_steps(task, judgement, names(), CHUNK)


def chunked_steps(task: dict, judgement: dict, examined: list[str], size: int) -> list[dict]:
    steps = [{"tool": "report_alerts", "arguments": chunk_call(task, judgement, examined[start : start + size])} for start in range(0, len(examined), size)]
    steps[-1]["save_as"] = "last"
    return steps


def read_steps(task: dict, batch: int = MAX_CITIES_PER_CALL) -> list[dict]:
    everyone = names()
    return [{"tool": "get_daily", "arguments": {"task": task, "cities": everyone[start : start + batch]}} for start in range(0, len(everyone), batch)]


def resolve(value, outputs):
    if isinstance(value, str) and value.startswith("$ref:"):
        node = outputs
        for part in value[5:].split("."):
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node
    return value


def verdict(arguments: dict, pinned: PinOutput, steps: list[dict]) -> dict:
    """The real verifier's verdict after the steps are applied as the host applies them: the server
    marks the run at its start, python steps act behind the tools' back, report steps go through
    report_alerts itself, and the submit step's payload is the task's output."""
    workspace = Path(tempfile.mkdtemp())
    write_run_marker(workspace)
    outputs: dict = {}
    submitted = None
    for step in steps:
        if "python" in step:
            exec(step["python"], {"workspace": workspace, "outputs": outputs, "json": json, "Path": Path})  # noqa: S102 - the case's own step
        elif step.get("tool") == "report_alerts" and not step.get("expect_error"):
            out = plain_output(report_alerts(build_output(step["arguments"], ReportInput), workspace))
            if step.get("save_as"):
                outputs[step["save_as"]] = out
        elif step.get("tool") == "submit":
            submitted = resolve(step["arguments"], outputs)
    if submitted is None:
        raise RuntimeError("the case never submits")
    return verifier.verify_task(TASK, build_output(arguments, AlertInput), build_output(submitted, AlertReportOutput), build_output(plain_output(pinned), PinOutput), str(workspace))


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
    calls = sum(1 for step in steps if "tool" in step)
    if calls > 5:
        raise RuntimeError(f"{name}: {calls} tool calls leave no step for the final answer")
    case = {"note": note, "task": TASK, "arguments": arguments, "steps": steps, "answer": answer, "expect": expect}
    (CASES / f"{name}.json").write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
    print(f"{name:22} {calls} tool calls  valid={found['valid']!s:5} score={found['score']:.3f}  {found['message'][:100]}")


def summary(judgement: dict) -> str:
    alerts = judgement["alerts"]
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


def judgement_of(output: dict) -> dict:
    return {"alerts": output["alerts"], "unavailable": output["unavailable"]}


def main() -> int:
    CASES.mkdir(parents=True, exist_ok=True)
    for stale in CASES.glob("*.json"):
        stale.unlink()

    # ----- The rich gold day: a rounding tie, a boundary city, mixed rules -----
    arguments, pinned, tie_city, boundary_city = find_gold()
    date = arguments["date"]
    task = plain_task(build_output(arguments, AlertInput))
    units = Units("F", "mph", "in")
    expected = expected_of(build_output(arguments, AlertInput), pinned)
    gold = expected_output(task, expected)
    judgement = judgement_of(gold)
    read = read_steps(task)
    print(f"gold day {date}: rules {arguments['conditions']}; tie city {tie_city}; boundary city {boundary_city}; {len(gold['alerts'])} alerts")
    valid = {"valid": True, "score_min": 1.0}
    write_case("gold", f"The exact report for {date} in one report_alerts call, then submit: {tie_city}'s humidity is a rounding tie (half up), {boundary_city}'s high sits exactly on its threshold.",
               arguments, pinned, read + report_steps(task, judgement) + [SUBMIT_LAST], expect=valid, answer=summary(gold))
    write_case("gold_chunked", "The prescribed path: get_daily with the task and all 30 cities, two report_alerts calls of 15 examined cities each, then submit with the report the last call returned.",
               arguments, pinned, read + prescribed_steps(task, judgement) + [SUBMIT_LAST], expect=valid, answer=summary(gold))
    write_case("batched_daily", "The exact report after two get_daily calls of 15 instead of one.",
               arguments, pinned, read_steps(task, batch=15) + report_steps(task, judgement) + [SUBMIT_LAST], expect=valid, answer=summary(gold))

    chunks = prescribed_steps(task, judgement)
    if len(chunks) != 2:
        raise RuntimeError("the prescribed path has two chunks")
    one = json.loads(json.dumps(chunks[:1]))
    one[-1]["save_as"] = "last"
    write_case("unexamined", "Only the first of the two report_alerts calls, then submit: fifteen cities are never examined.",
               arguments, pinned, read + one + [SUBMIT_LAST], expect={"valid": False, "message_contains": "unexamined", "score_max": 0.6}, answer=summary(gold))

    # overlapping calls: the second chunk restates five cities of the first with the same judgement
    everyone = names()
    overlapping = [chunk_call(task, judgement, everyone[0:10]), chunk_call(task, judgement, everyone[5:20]), chunk_call(task, judgement, everyone[20:30])]
    overlapping_steps = [{"tool": "report_alerts", "arguments": part} for part in overlapping]
    overlapping_steps[-1]["save_as"] = "last"
    write_case("overlapping_calls", "Three report_alerts calls whose second one restates five cities of the first: merged in sequence, the report is still exact.",
               arguments, pinned, read + overlapping_steps + [SUBMIT_LAST], expect=valid, answer=summary(gold))

    # stale files: the member's folder persists across runs, so a leftover report may sit in the workspace
    plant = "(workspace / 'reports').mkdir(exist_ok=True); (workspace / 'reports' / '{date}.json').write_text(json.dumps({content}))"
    old_shape = {"date": date, "run": "deadbeefdeadbeef", "examined": everyone, "alerts": [{"city": "Lima", "values": {"max_temp": 1.0}, "matched": []}], "unavailable": []}
    write_case("stale_old_version", "An older version's report (no task, another run's marker) lies in the workspace: the first report_alerts call replaces it.",
               arguments, pinned, read + [{"python": plant.format(date=date, content=json.dumps(old_shape))}] + chunks + [SUBMIT_LAST], expect=valid, answer=summary(gold))
    previous = {"date": date, "run": "deadbeefdeadbeef", "task": task, "examined": everyone, **judgement}
    write_case("stale_previous_run", "A complete, correct report of an EARLIER run lies in the workspace and this run judges only 15 cities: the leftover is replaced, not inherited, so fifteen cities are unexamined.",
               arguments, pinned, read + [{"python": plant.format(date=date, content=json.dumps(previous))}] + one + [SUBMIT_LAST],
               expect={"valid": False, "message_contains": "unexamined", "score_max": 0.6}, answer=summary(gold))
    write_case("stale_no_calls", "A complete, correct report of an earlier run lies in the workspace and the agent submits the right report without calling report_alerts: the verifier refuses the leftover.",
               arguments, pinned, read + [{"python": plant.format(date=date, content=json.dumps(previous))}] + [{"tool": "submit", "arguments": gold}],
               expect={"valid": False, "message_contains": "not a report of this run", "score_max": 0.0}, answer=summary(gold))

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
    spare_metric = metrics_of(pinned)[[row.city for row in pinned.metric].index(bogus_city)]
    conditions = parse_conditions(arguments["conditions"])
    bogus_values = {variable: rounded(variable, spare_metric, units) for variable in dict.fromkeys(c.variable for c in conditions)}
    broken[0]["arguments"]["alerts"].append({"city": bogus_city, "values": bogus_values, "matched": [arguments["conditions"][1]]})
    last = broken[-1]["arguments"]
    last["examined"] = last["examined"] + [wrong_city, bogus_city]
    last["alerts"] = last["alerts"] + [entry for entry in gold["alerts"] if entry["city"] == wrong_city]
    write_case("corrected", f"The first call gets {wrong_city}'s value wrong and alerts {bogus_city}, which does not qualify; the last call examines both again with {wrong_city} corrected and no entry for {bogus_city}: the final report wins.",
               arguments, pinned, read + broken + [SUBMIT_LAST], expect=valid, answer=summary(gold))

    def alert(judgement: dict, city: str) -> dict:
        return next(a for a in judgement["alerts"] if a["city"] == city)

    def wrong_case(name: str, note: str, wrong: dict, *, message: str, score_max: float | None = None) -> None:
        expect = {"valid": False, "message_contains": message}
        if score_max is not None:
            expect["score_max"] = score_max
        write_case(name, note, arguments, pinned, read + report_steps(task, wrong) + [SUBMIT_LAST], expect=expect, answer=summary(wrong))

    # wrong unit: the metric values reported as if they were the requested units
    wrong = json.loads(json.dumps(judgement))
    by_city = {row.city: row for row in pinned.metric}
    for entry in wrong["alerts"]:
        row = by_city[entry["city"]]
        entry["values"]["max_temp"] = row.max_temp_c
        entry["values"]["wind"] = row.max_wind_kmh
        entry["values"]["rain"] = row.rain_mm
    wrong_case("wrong_unit", "Values left in °C, km/h and mm although °F, mph and in were requested.", wrong, message="with a wrong value", score_max=0.5)

    # banker's rounding: the tie city's humidity rounded half to even
    wrong = json.loads(json.dumps(judgement))
    row = by_city[tie_city]
    mean = Fraction(int(row.humidity_sum_pct), 24)
    even = float((Decimal(mean.numerator) / Decimal(mean.denominator)).quantize(Decimal("0.1"), ROUND_HALF_EVEN))
    if even == alert(gold, tie_city)["values"]["humidity"]:
        raise RuntimeError("the tie did not change under half-even rounding")
    alert(wrong, tie_city)["values"]["humidity"] = even
    wrong_case("bankers_rounding", f"{tie_city}'s mean humidity {mean.numerator}/24 rounded half to even ({even}) instead of half up.", wrong, message="with a wrong value")

    # boundary: the city whose high equals the threshold left out, as if >= were >
    wrong = json.loads(json.dumps(judgement))
    wrong["alerts"] = [a for a in wrong["alerts"] if a["city"] != boundary_city]
    wrong_case("boundary", f"{boundary_city}'s high is exactly the max_temp threshold and satisfies >=, but was treated as if the rule were >.", wrong, message="alert(s) missing")

    # missing city: one alert dropped
    wrong = json.loads(json.dumps(judgement))
    dropped = wrong["alerts"].pop(len(wrong["alerts"]) // 2)
    wrong_case("missing_city", f"{dropped['city']} left out of the alerts.", wrong, message="alert(s) missing", score_max=0.99)

    # extra city: a city that does not qualify added with its true values
    spare = next(row for row in pinned.metric if row.city not in alerted and row.city not in {u["city"] for u in gold["unavailable"]})
    spare_metric = metrics_of(pinned)[[row.city for row in pinned.metric].index(spare.city)]
    wrong = json.loads(json.dumps(judgement))
    values = {variable: rounded(variable, spare_metric, units) for variable in dict.fromkeys(c.variable for c in conditions)}
    wrong["alerts"].append({"city": spare.city, "values": values, "matched": [arguments["conditions"][1]]})
    wrong_case("extra_city", f"{spare.city} added although no rule holds for it.", wrong, message="extra alert(s)", score_max=0.99)

    # any vs all: the rules evaluated with "all" although the task says "any"
    as_all = judgement_of(expected_output(task, evaluate(metrics_of(pinned), conditions, "all", units)))
    if as_all == judgement:
        raise RuntimeError("all and any give the same report")
    wrong_case("any_vs_all", 'Rules combined with "all" although the task asked for "any".', as_all, message="alert(s) missing")

    # fabricated values: one value nudged and one matched rule added
    wrong = json.loads(json.dumps(judgement))
    first_alert = wrong["alerts"][0]
    key = next(iter(first_alert["values"]))
    first_alert["values"][key] = round(first_alert["values"][key] + 0.1, 1)
    second = wrong["alerts"][1]
    second["matched"] = second["matched"] + [c for c in arguments["conditions"] if c not in second["matched"]][:1]
    wrong_case("fabricated", f"{first_alert['city']}'s {key} nudged by 0.1 and a rule {second['city']} does not meet listed as matched.", wrong, message="wrong")

    # empty report: every city examined, nothing reported
    wrong_case("empty_report", "An empty report although cities qualify.", {"alerts": [], "unavailable": []}, message="alert(s) missing", score_max=0.0)

    # no report at all: the agent reads the data and submits the right report from memory
    write_case("no_report", "The agent reads the data and submits the right report without ever calling report_alerts.", arguments, pinned, read + [{"tool": "submit", "arguments": gold}],
               expect={"valid": False, "message_contains": "no report", "score_max": 0.0}, answer=summary(gold))

    # duplicate city: refused by report_alerts, so no report is written
    doubled = chunk_call(task, judgement, everyone)
    doubled["alerts"].append(json.loads(json.dumps(doubled["alerts"][0])))
    write_case("duplicate_city", "The same city reported twice: report_alerts refuses it, so no report is saved and the submission is refused.", arguments, pinned,
               read + [{"tool": "report_alerts", "arguments": doubled, "expect_error": True}, {"tool": "submit", "arguments": gold}],
               expect={"valid": False, "message_contains": "no report", "score_max": 0.0}, answer=summary(gold))

    # divergent submission: the report is exact, but the agent submits a retyped copy with one value off
    retyped = json.loads(json.dumps(gold))
    retyped_alert = retyped["alerts"][0]
    key = next(iter(retyped_alert["values"]))
    retyped_alert["values"][key] = round(retyped_alert["values"][key] + 0.1, 1)
    write_case("divergent_submission", f"Three exact report_alerts calls, then a submission retyped with {retyped_alert['city']}'s {key} off by 0.1: it must be the report the last call returned.",
               arguments, pinned, read + chunks + [{"tool": "submit", "arguments": retyped}],
               expect={"valid": False, "message_contains": "differs from the saved report", "score_max": 0.0}, answer=summary(retyped))

    # ----- The DST day: Santiago's 23-hour day, on the prescribed chunked path -----
    dst_arguments = {"date": DST_DATE, "match": "any", "temperature_unit": "F", "wind_unit": "ms", "rain_unit": "mm", "conditions": ["min_temp < 50", "humidity >= 85"]}
    dst_pin = pin(dst_arguments)
    dst_task = plain_task(build_output(dst_arguments, AlertInput))
    dst_gold = expected_output(dst_task, expected_of(build_output(dst_arguments, AlertInput), dst_pin))
    santiago = next(row for row in dst_pin.metric if row.city == "Santiago")
    if santiago.hours != 23 or "Santiago" not in {a["city"] for a in dst_gold["alerts"]}:
        raise RuntimeError("Santiago is not a 23-hour alert on the DST day")
    dst_read = read_steps(dst_task)
    write_case("gold_dst", "The exact report for Santiago's 23-hour day (clocks went forward at midnight) on the prescribed path: two report_alerts calls of 15 cities, then submit.",
               dst_arguments, dst_pin, dst_read + prescribed_steps(dst_task, judgement_of(dst_gold)) + [SUBMIT_LAST], expect=valid, answer=summary(dst_gold))

    # Santiago aggregated over the 24 UTC hours of the date instead of its local day
    utc = utc_day_metrics("Santiago", DST_DATE)
    wrong = judgement_of(json.loads(json.dumps(dst_gold)))
    entry = alert(wrong, "Santiago")
    dst_units = Units("F", "ms", "mm")
    for variable in list(entry["values"]):
        entry["values"][variable] = rounded(variable, utc, dst_units)
    if entry["values"] == alert(dst_gold, "Santiago")["values"]:
        raise RuntimeError("the UTC day gives Santiago the same values as its local day")
    write_case("dst_utc_day", "Santiago's values taken over the 24 UTC hours of the date instead of its 23-hour local day.",
               dst_arguments, dst_pin, dst_read + prescribed_steps(dst_task, wrong) + [SUBMIT_LAST], expect={"valid": False, "message_contains": "with a wrong value"}, answer=summary(wrong))

    # sanity: the hourly rows of the DST day really skip the missing hour
    hours = get_hourly(build_output({"city": "Santiago", "date": DST_DATE}, HourlyInput), Path(tempfile.mkdtemp()))
    print(f"Santiago {DST_DATE}: {len(hours.rows)} rows, first {hours.rows[0].time}")
    print(f"\nwrote {len(list(CASES.glob('*.json')))} cases to {CASES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

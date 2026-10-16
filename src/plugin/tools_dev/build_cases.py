"""Build the scripted cases under tests/cases/ for NanoAgent's task runner (~/mochi-dev/harness/scripted_task.py).

    uv run python src/plugin/tools_dev/build_cases.py

A case runs the task exactly as the host does, with a scripted agent instead of a model: get_daily with
all 30 cities, a python step that judges the day's data with tests/scripted_judge.py (inlined, so the
case judges whatever data the plugin fetched, written from the instructions, never from plugin.rules),
two report_alerts calls of fifteen cities, and submit. Gold cases expect all 21 checks to hold; each
wrong case breaks the run in one way and expects the check that catches it. Committed cases use
archive days, whose data no longer changes; tools_dev/run_matrix.py builds cases for dates relative to
today. Every expectation is checked here first, offline, against the recorded Open-Meteo answers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]

CASES = ROOT / "tests" / "cases"
JUDGE = (ROOT / "tests" / "scripted_judge.py").read_text(encoding="utf-8")
CITIES = [
    "Athens", "Auckland", "Bangkok", "Buenos Aires", "Cairo", "Cape Town", "Denver", "Dubai", "Honolulu", "Houston",
    "Jakarta", "Lagos", "Lima", "London", "Madrid", "Mexico City", "Moscow", "Mumbai", "Nairobi", "Oslo",
    "Perth", "Phoenix", "Reykjavik", "Santiago", "Seoul", "Singapore", "Sydney", "Tokyo", "Toronto", "Vancouver",
]  # fmt: skip
GOLD = {"valid": True, "score_min": 1.0, "message_contains": "All 21 checks hold"}


def task(date: str, match: str = "any", temperature: str = "F", wind: str = "mph", rain: str = "in", conditions=None) -> dict:
    conditions = conditions or ["max_temp >= 95", "humidity >= 85", "rain > 0.5", "wind >= 20"]
    return {"date": date, "match": match, "temperature_unit": temperature, "wind_unit": wind, "rain_unit": rain, "conditions": conditions}


def judge_step(arguments: dict, mutations=(), extra: str = "") -> dict:
    """A python step that plans the report calls from get_daily's answer (saved as `daily`)."""
    code = f"{JUDGE}\nTASK = {json.dumps(arguments)}\noutputs['plan'] = plan(TASK, outputs['daily'], {list(mutations)!r})\n{extra}"
    return {"python": code}


def read_step(arguments: dict) -> dict:
    return {"tool": "get_daily", "arguments": {"task": arguments, "cities": CITIES}, "save_as": "daily"}


def report_steps(halves=(0, 1)) -> list[dict]:
    steps = [{"tool": "report_alerts", "arguments": f"$ref:plan.calls.{half}"} for half in halves]
    steps[-1]["save_as"] = "last"
    return steps


SUBMIT_LAST = {"tool": "submit", "arguments": "$ref:last.report"}


def run_case(arguments: dict, mutations=(), expect=None, note: str = "") -> dict:
    """The prescribed path: get_daily, judge, two report calls, submit what the last returned."""
    return {
        "note": note,
        "task": "daily-alert",
        "arguments": arguments,
        "steps": [read_step(arguments), judge_step(arguments, mutations), *report_steps(), SUBMIT_LAST],
        "answer": "Reported every alert and unavailable city.",
        "expect": expect or GOLD,
    }


def cases() -> dict[str, dict]:
    july = task("2026-07-07")
    found = {
        # ----- gold, across the kinds of past data and the calendar's corners -----
        "gold": run_case(july, note="2026-07-07 from the ECMWF IFS archive, four rules in °F, mph and inches."),
        "gold_dst_santiago": run_case(
            task("2026-09-06", temperature="F", wind="ms", rain="mm", conditions=["min_temp < 50", "humidity >= 85"]),
            note="Santiago springs forward at midnight: its day starts at 01:00 local and has 23 hours.",
        ),
        "gold_dst_europe": run_case(task("2026-03-29", temperature="C", wind="kmh", rain="mm", conditions=["max_temp >= 15", "wind >= 30"]), note="Europe springs forward: 23-hour days."),
        "gold_dst_south": run_case(task("2026-04-05", match="all", temperature="C", wind="kmh", rain="mm", conditions=["max_temp >= 20", "humidity >= 60"]), note="Santiago, Sydney and Auckland fall back: 25-hour days."),
        "gold_1940": run_case(
            task("1940-01-01", temperature="C", wind="kmh", rain="mm", conditions=["max_temp >= 25", "rain > 1"]),
            note="ERA5's first day: no 1939 hours, so cities east of UTC miss readings and are unavailable.",
        ),
        "gold_1943": run_case(task("1943-06-01", temperature="C", wind="kmh", rain="mm", conditions=["max_temp >= 30", "humidity >= 80"]), note="Wartime offsets (Jakarta +09:00, Nairobi +02:45)."),
        "gold_seam_era5": run_case(task("2017-01-01", temperature="C", wind="ms", rain="mm", conditions=["min_temp < 0", "wind >= 8"]), note="The last ERA5 day."),
        "gold_seam_ifs": run_case(task("2017-01-02", temperature="C", wind="ms", rain="mm", conditions=["min_temp < 0", "wind >= 8"]), note="The first IFS archive day."),
        "gold_leap_day": run_case(task("2024-02-29", match="all", conditions=["max_temp >= 60", "humidity >= 50"]), note="A leap day."),
        # ----- wrong judgements, each caught by its check -----
        "boundary": run_case(
            task("2026-07-07", temperature="C", wind="kmh", rain="mm", conditions=["max_temp >= 33.3"]), ["boundary"],
            {"valid": False, "score_max": 0.99, "message_contains": "Missing alert"}, ">= taken as >: Athens' high is exactly 33.3 °C.",
        ),
        "bankers_rounding": run_case(
            task("2026-07-07", conditions=["humidity >= 80"]), ["bankers"],
            {"valid": False, "score_max": 0.99, "message_contains": "Perth humidity is 83.3 (exact 83.250000000, rounded half up), reported 83.2"},
            "Half-even rounding: Perth's mean humidity is exactly 83.25.",
        ),
        "missing_alert": run_case(july, ["missing_alert"], {"valid": False, "score_max": 0.99, "message_contains": "Missing alert"}, "The first alert city left out."),
        "extra_alert": run_case(july, ["extra_alert"], {"valid": False, "score_max": 0.99, "message_contains": "Extra alert"}, "A city that meets no rule reported."),
        "fabricated_value": run_case(july, ["fabricated"], {"valid": False, "score_max": 0.99, "message_contains": "Wrong value"}, "One value 0.1 off."),
        "wrong_matched": run_case(july, ["wrong_matched"], {"valid": False, "score_max": 0.99, "message_contains": "Wrong matched conditions"}, "A holding condition left out of matched."),
        "any_vs_all": run_case(july, ["any_vs_all"], {"valid": False, "score_max": 0.99, "message_contains": "Missing alert"}, "Rules combined with all although the task asks for any."),
        "empty_report": run_case(july, ["empty"], {"valid": False, "score_max": 0.99, "message_contains": "Missing alert"}, "Nothing reported."),
        "dropped_unavailable": run_case(
            task("1940-01-01", temperature="C", wind="kmh", rain="mm", conditions=["max_temp >= 25", "rain > 1"]), ["drop_unavailable"],
            {"valid": False, "score_max": 0.99, "message_contains": "Missing unavailable"}, "A city with missing 1940 readings left out.",
        ),
    }
    # ----- the run's integrity -----
    found["unexamined"] = {
        "note": "Only the first fifteen cities judged.",
        "task": "daily-alert", "arguments": july,
        "steps": [read_step(july), judge_step(july), *report_steps((0,)), SUBMIT_LAST],
        "expect": {"valid": False, "score_max": 0.99, "message_contains": "15 cities unexamined"},
    }  # fmt: skip
    found["divergent_submission"] = {
        "note": "The submission drops an alert the saved report holds.",
        "task": "daily-alert", "arguments": july,
        "steps": [read_step(july), judge_step(july), *report_steps(), {"python": "sub = dict(outputs['last']['report']); sub['alerts'] = sub['alerts'][1:]; outputs['sub'] = sub"}, {"tool": "submit", "arguments": "$ref:sub"}],
        "expect": {"valid": False, "score_max": 0.0, "message_contains": "differs from the saved report"},
    }  # fmt: skip
    found["wrong_data_label"] = {
        "note": "The submission names other data than the agent read.",
        "task": "daily-alert", "arguments": july,
        "steps": [read_step(july), judge_step(july), *report_steps(), {"python": "sub = dict(outputs['last']['report']); sub['data'] = {**sub['data'], 'model': 'era5', 'label': 'ERA5 reanalysis (ECMWF), hourly'}; outputs['sub'] = sub"}, {"tool": "submit", "arguments": "$ref:sub"}],
        "expect": {"valid": False, "score_max": 0.0, "message_contains": "the agent read the ECMWF IFS archive"},
    }  # fmt: skip
    found["no_report"] = {
        "note": "Judged, but never reported: the submission is typed in.",
        "task": "daily-alert", "arguments": july,
        "steps": [read_step(july), judge_step(july, extra="outputs['sub'] = {'task': TASK, 'data': outputs['daily']['source'], **judge(TASK, outputs['daily'])}"), {"tool": "submit", "arguments": "$ref:sub"}],
        "expect": {"valid": False, "score_max": 0.0, "message_contains": "no report at reports/2026-07-07.json"},
    }  # fmt: skip
    found["report_before_reading"] = {
        "note": "report_alerts before get_daily is refused; the run then reads and reports properly.",
        "task": "daily-alert", "arguments": july,
        "steps": [{"tool": "report_alerts", "arguments": {"task": july, "examined": ["Oslo"], "alerts": [], "unavailable": []}, "expect_error": True}, read_step(july), judge_step(july), *report_steps(), SUBMIT_LAST],
        "expect": GOLD,
    }  # fmt: skip
    found["tampered_receipt"] = {
        "note": "The receipt of the data read is changed behind the tools' back.",
        "task": "daily-alert", "arguments": july,
        "steps": [read_step(july), judge_step(july), *report_steps(), {"python": "path = workspace / 'reports' / '2026-07-07.source.json'; receipt = json.loads(path.read_text()); receipt['cities'][0]['temperature_2m'][0] += 1; path.write_text(json.dumps(receipt))"}, SUBMIT_LAST],
        "expect": {"valid": False, "score_max": 0.0, "message_contains": "does not match its own sha256"},
    }  # fmt: skip
    found["stale_report_replaced"] = {
        "note": "An earlier run's complete report sits in the folder: the first call replaces it.",
        "task": "daily-alert", "arguments": july,
        "steps": [{"python": "(workspace / 'reports').mkdir(exist_ok=True); (workspace / 'reports' / '2026-07-07.json').write_text(json.dumps({'date': '2026-07-07', 'run': 'an-earlier-run', 'examined': []}))"}, read_step(july), judge_step(july), *report_steps(), SUBMIT_LAST],
        "expect": GOLD,
    }  # fmt: skip
    found["refused_date"] = {
        "note": "A date past the seasonal outlook is refused before the agent starts.",
        "task": "daily-alert", "arguments": task("2031-01-01"), "steps": [],
        "expect": {"error_contains": "the last day the ECMWF seasonal outlook covers"},
    }  # fmt: skip
    found["refused_malformed"] = {
        "note": "A malformed date is refused before the agent starts.",
        "task": "daily-alert", "arguments": task("2026-02-30"), "steps": [],
        "expect": {"error_contains": "not a calendar day"},
    }  # fmt: skip
    found["check_date_today"] = {"note": "Today's date and its data.", "task": "check-date", "arguments": {"date": "today"}, "steps": [], "expect": {"valid": True, "score_min": 1.0}}
    found["check_date_far"] = {"note": "A date past the outlook: answered, not refused.", "task": "check-date", "arguments": {"date": "2031-01-01"}, "steps": [], "expect": {"valid": True, "score_min": 1.0}}
    return found


def main() -> None:
    from datetime import datetime

    from fakes import FakeOpenMeteo, index
    from replay import matches, replay

    from plugin import openmeteo, sources

    clock = datetime.fromisoformat(index()["clock"])
    fake = FakeOpenMeteo(lambda: clock)
    openmeteo.OPENER, openmeteo.SLEEP, sources.CLOCK = fake, (lambda seconds: None), (lambda: fake.clock())
    CASES.mkdir(parents=True, exist_ok=True)
    for old in CASES.glob("*.json"):
        old.unlink()
    built = cases()
    for name, case in built.items():
        found = replay(case)
        problems = matches(found, case["expect"])
        if problems:
            raise SystemExit(f"{name}: {problems}")
        (CASES / f"{name}.json").write_text(json.dumps(case, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{name}: {found.get('message') or found.get('error')}"[:160])
    print(f"{len(built)} cases written to {CASES}")


if __name__ == "__main__":
    main()

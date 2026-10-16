"""Run the task live on dates relative to today through NanoAgent's task runner, with a scripted agent
and no model:

    uv run python src/plugin/tools_dev/run_matrix.py [--sandbox seatbelt] [--pause 8] [--out DIR]
        [--python <NanoAgent's venv python>] [--harness <scripted_task.py>] [--committed gold boundary ...]

For each date (yesterday, today, tomorrow, T+13, T+14, T+16, the outlook's last day, the first refused
day, in UTC) it writes a gold case and, for some, a wrong one (the first break the day's data allows),
built the way build_cases.py builds them, then runs each case one at a time, pausing between runs to
stay under Open-Meteo's per-minute limit (a run asks for 30 locations once or twice). --committed adds
cases from tests/cases/ by name. The cases, each run's output and summary.json go to --out.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]

from plugin.rules import date_text, day_number, outlook_last_day
from plugin.tools_dev.build_cases import run_case, task

HOME = Path.home()
WRONG = {"valid": False, "score_max": 0.99}
BREAKS = ["fabricated", "extra_alert", "missing_alert"]


def matrix(today: int) -> dict[str, dict]:
    """The dates relative to ``today`` (a day number), with the rules each is judged by."""
    named = {
        "yesterday": today - 1,
        "today": today,
        "tomorrow": today + 1,
        "t_plus_13": today + 13,
        "t_plus_14": today + 14,
        "t_plus_16": today + 16,
        "outlook_last": outlook_last_day(today),
    }
    cases = {}
    for name, day in named.items():
        date = date_text(day)
        rules = task(date, temperature="C", wind="kmh", rain="mm", conditions=["max_temp >= 25", "min_temp < 5", "rain > 1", "wind >= 30"])
        cases[f"live_{name}_gold"] = run_case(rules, note=f"{name}: {date}, judged on whatever data the rules choose today")
        if name in ("tomorrow", "t_plus_14", "yesterday"):
            cases[f"live_{name}_wrong"] = run_case(rules, BREAKS, {**WRONG}, note=f"{name}: {date}, one break the day's data allows")
    first_refused = date_text(outlook_last_day(today) + 1)
    cases["live_first_refused"] = {
        "note": f"{first_refused}: the day after the outlook's last, refused before the agent starts",
        "task": "daily-alert", "arguments": task(first_refused), "steps": [],
        "expect": {"error_contains": "the last day the ECMWF seasonal outlook covers"},
    }  # fmt: skip
    cases["live_check_date_today"] = {"note": "today, from the clock", "task": "check-date", "arguments": {"date": "today"}, "steps": [], "expect": {"valid": True, "score_min": 1.0}}
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=str(HOME / "mochi-dev/rushlab/nanoagent/.venv/bin/python"))
    parser.add_argument("--harness", default=str(HOME / "mochi-dev/harness/scripted_task.py"))
    parser.add_argument("--sandbox", default="seatbelt")
    parser.add_argument("--pause", type=float, default=8.0)
    parser.add_argument("--out", default=None)
    parser.add_argument("--committed", nargs="*", default=[])
    parser.add_argument("--live", nargs="*", default=None, help="only these live cases (names without the live_ prefix); default all")
    args = parser.parse_args()
    now = datetime.now(UTC)
    out = Path(args.out or HOME / "mochi-dev/runs/weather-alert" / f"matrix-{now:%Y%m%dT%H%MZ}")
    (out / "cases").mkdir(parents=True, exist_ok=True)
    cases = matrix(day_number(now.date().isoformat()))
    if args.live is not None:
        cases = {name: case for name, case in cases.items() if name.removeprefix("live_") in args.live}
    for name in args.committed:
        cases[name] = json.loads((ROOT / "tests" / "cases" / f"{name}.json").read_text(encoding="utf-8"))
    results = []
    for index, (name, case) in enumerate(cases.items()):
        path = out / "cases" / f"{name}.json"
        path.write_text(json.dumps(case, indent=1) + "\n", encoding="utf-8")
        started = time.perf_counter()
        if case.get("agent_folder"):  # a committed case's folder, next to the copy
            source = ROOT / "tests" / "cases" / case["agent_folder"]
            (path.parent / case["agent_folder"]).parent.mkdir(parents=True, exist_ok=True)
            (path.parent / case["agent_folder"]).write_bytes(source.read_bytes())
        completed = subprocess.run(
            [args.python, args.harness, str(ROOT), str(path), "--sandbox", args.sandbox, "--out", str(out / name)],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        summary_path = out / name / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
        result = {
            "case": name,
            "date": case["arguments"].get("date"),
            "ok": completed.returncode == 0,
            "seconds": round(time.perf_counter() - started, 1),
            "valid": summary.get("valid"),
            "score": summary.get("score"),
            "message": summary.get("message") or summary.get("error") or completed.stderr[-300:],
            "tool_calls": summary.get("tool_calls"),
        }
        results.append(result)
        print(f"{'OK  ' if result['ok'] else 'FAIL'} {name} ({result['date']}) {result['seconds']}s: {str(result['message'])[:150]}", flush=True)
        if index < len(cases) - 1:
            time.sleep(args.pause)
    (out / "summary.json").write_text(json.dumps({"at": now.isoformat(timespec="seconds"), "sandbox": args.sandbox, "results": results}, indent=1) + "\n", encoding="utf-8")
    failed = [result["case"] for result in results if not result["ok"]]
    print(f"{len(results) - len(failed)}/{len(results)} matched their expectation" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

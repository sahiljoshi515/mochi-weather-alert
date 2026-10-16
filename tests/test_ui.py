"""The page's renderers drawn with NanoAgent's served kit under Node (the lab's page check, plus draws
with the real verdicts of committed cases, whose checks list the page check cannot pass), the rules
module shared byte for byte, and ui/ holding only what NanoAgent serves."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from replay import played

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "cases"
PAGE_CHECK = Path.home() / "mochi-dev" / "harness" / "page_check.sh"
KIT = Path.home() / "mochi-dev" / "rushlab" / "nanoagent" / "src" / "nanoagent" / "plugin" / "page" / "kit"
SHIM = Path.home() / "mochi-dev" / "harness" / "page" / "shim.mjs"
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None or not PAGE_CHECK.is_file() or not KIT.is_dir(), reason="needs node and the lab's page check")


def test_the_verifier_and_the_server_share_one_rules_module() -> None:
    assert (ROOT / "verifier" / "rules.py").read_bytes() == (ROOT / "src" / "plugin" / "rules.py").read_bytes()


def test_ui_holds_only_the_renderers() -> None:
    assert sorted(path.name for path in (ROOT / "ui").iterdir()) == ["renderers.css", "renderers.js"]


def report() -> dict:
    case = json.loads((ROOT / "tests" / "cases" / "gold.json").read_text(encoding="utf-8"))
    return {
        "task": case["arguments"],
        "run": "a1b2c3d4e5f60718",
        "data": {"kind": "archive", "model": "ecmwf_ifs", "run": "", "step_hours": 1, "today": "2026-10-16", "label": "ECMWF IFS archive (analysis), hourly"},
        "alerts": [
            {"city": "Dubai", "values": {"max_temp": 101.3, "rain": 0.0, "wind": 9.4, "humidity": 50.7}, "matched": ["max_temp >= 95"]},
            {"city": "Mumbai", "values": {"max_temp": 82.6, "rain": 2.2, "wind": 16.5, "humidity": 87.6}, "matched": ["humidity >= 85", "rain > 0.5"]},
        ],
        "unavailable": [{"city": "Cairo", "variables": ["humidity"]}],
    }


DATE_ANSWER = {
    "today": "2026-10-16", "clock": "2026-10-16T04:00", "date": "2026-10-17", "weekday": "Saturday", "kind": "forecast",
    "source": "ECMWF IFS forecast issued 2026-10-15 18:00 UTC, hourly",
    "archive_first": "1940-01-01", "archive_last": "2026-10-14", "forecast_first": "2026-10-15", "forecast_last": "2026-10-29",
    "outlook_first": "2026-10-30", "outlook_last": "2027-04-30",
    "message": "Today is 2026-10-16 (UTC). 2026-10-17 is a Saturday; a daily-alert task on it uses the ECMWF IFS forecast issued 2026-10-15 18:00 UTC, hourly.",
}  # fmt: skip


@NEEDS_NODE
def test_the_page_check_draws_both_outputs(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text(json.dumps(report()), encoding="utf-8")
    (tmp_path / "empty.json").write_text(json.dumps({**report(), "alerts": [], "unavailable": []}), encoding="utf-8")
    (tmp_path / "date.json").write_text(json.dumps(DATE_ANSWER), encoding="utf-8")
    for name, sample, words in (
        ("AlertReportOutput", "report.json", ["Dubai", "101.3", "High ≥ 95 °F", "ECMWF IFS archive (analysis)", "Open-Meteo.com", "Cairo"]),
        ("AlertReportOutput", "empty.json", ["No city crossed"]),
        ("DateOutput", "date.json", ["Forecast run", "2026-10-15 to 2026-10-29", "Today is 2026-10-16"]),
    ):
        completed = subprocess.run(["bash", str(PAGE_CHECK), str(ROOT), f"{name}={tmp_path / sample}"], capture_output=True, text=True, timeout=120, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert f"render {name}: OK" in completed.stdout and ": FAILED" not in completed.stdout, completed.stdout
        for word in words:
            assert word in completed.stdout, (word, completed.stdout[-1500:])


DRAW = """
import "./shim.mjs";
import { readFileSync } from "node:fs";
const renderers = (await import("./renderers.js")).default;
const value = JSON.parse(readFileSync("./sample.json", "utf8"));
const verdict = JSON.parse(readFileSync("./verdict.json", "utf8"));
const node = renderers[process.argv[2]](value, { task: {}, verdict, send: () => {} });
console.log(node.textContent);
"""


@NEEDS_NODE
@pytest.mark.parametrize(("name", "output_class"), [("gold", "AlertReportOutput"), ("fabricated_value", "AlertReportOutput"), ("unexamined", "AlertReportOutput"), ("replayed_earlier_run", "AlertReportOutput"), ("check_date_today", "DateOutput")])
def test_the_checks_list_draws_each_real_verdict(offline, tmp_path: Path, name: str, output_class: str) -> None:
    """A committed case's own output and verdict: every check with its id; each failure with why and, where
    the verdict has them, what was expected against what was found; each skipped check with what it needed."""
    verdict, output = played(json.loads((CASES / f"{name}.json").read_text(encoding="utf-8")), CASES)
    shutil.copytree(KIT, tmp_path / "kit")
    for file in ("renderers.js", "renderers.css"):
        shutil.copy(ROOT / "ui" / file, tmp_path / file)
    shutil.copy(SHIM, tmp_path / "shim.mjs")
    (tmp_path / "sample.json").write_text(json.dumps(output), encoding="utf-8")
    (tmp_path / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    (tmp_path / "check.mjs").write_text(DRAW, encoding="utf-8")
    completed = subprocess.run(["node", "check.mjs", output_class], cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    entries = verdict["details"]["constraints"]
    held = sum(entry["ok"] is True for entry in entries)
    words = [f"Checks: {held} of {len(entries)} hold", *(entry["constraint"] for entry in entries), *(entry["id"] for entry in entries)]
    words += [entry["message"] for entry in entries if entry["ok"] is not True]
    words += ["Expected: "] if any(entry["ok"] is False and None not in (entry["expected"], entry["actual"]) for entry in entries) else []
    for word in words:
        assert word in completed.stdout, (word, completed.stdout)
    assert ("Expected: " in completed.stdout) == (words[-1] == "Expected: ")

"""The tools as the agent and the host call them: get_daily's values and receipt, report_alerts'
merging and refusals, the answer key, the date check, and the call log."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import scripted_judge
from conftest import T0
from helpers import Run, task_for
from slimconfig import build_output, plain_output

from contract import AlertInput, DailyInput, DateInput, ReportInput
from plugin import alerts
from plugin.rules import CITY_NAMES, Units, day_metrics, given_values, parse_conditions


def test_get_daily_gives_each_needed_value_in_the_task_units_and_writes_a_receipt(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    daily = run.read()
    assert daily["source"]["label"] == "ECMWF IFS archive (analysis), hourly" and daily["source"]["today"] == "2026-10-16"
    assert [row["city"] for row in daily["cities"]] == list(CITY_NAMES)
    assert all(set(row) == {"city", "samples", "missing", "values"} for row in daily["cities"])
    assert all(set(row["values"]) == {"max_temp", "rain", "wind", "humidity"} and row["samples"] == 24 for row in daily["cities"])
    assert daily["run"] == alerts.RUN
    receipt = json.loads((run.folder / "2026-07-07.source.json").read_text())
    assert receipt["run"] == alerts.RUN and receipt["sha256"] == run.pin.candidates[0].sha256
    task = run.task
    conditions = parse_conditions(task["conditions"])
    units = Units(task["temperature_unit"], task["wind_unit"], task["rain_unit"])
    for row, series in zip(daily["cities"], receipt["cities"], strict=True):
        assert row["values"] == {name: float(value) for name, value in given_values(day_metrics(series, 1), conditions, units).items()}
    # about 1,300 tokens for four variables: 0.3.1's answer plus the source (about 200 characters)
    assert len(json.dumps(daily, separators=(",", ":"))) < 4500, "get_daily's answer stays small for the hosted model"


def test_get_daily_refuses_unknown_and_repeated_cities(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    with pytest.raises(ValueError, match="unknown city Atlantis"):
        alerts.get_daily(build_output({"task": run.task, "cities": ["Atlantis"]}, DailyInput), run.workspace)
    with pytest.raises(ValueError, match="distinct"):
        alerts.get_daily(build_output({"task": run.task, "cities": ["Oslo", "Oslo"]}, DailyInput), run.workspace)


def test_report_alerts_needs_the_data_read_first(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    call = {"task": run.task, "examined": ["Oslo"], "alerts": [], "unavailable": []}
    with pytest.raises(ValueError, match="call get_daily for 2026-07-07 first"):
        alerts.report_alerts(build_output(call, ReportInput), run.workspace)


def test_report_alerts_merges_halves_restates_cities_and_names_its_data(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    gold = scripted_judge.judge(run.task, run.daily)
    first, second = scripted_judge.halves(run.task, gold)
    out = run.report([first])
    assert out["examined"] == 15 and not out["complete"] and out["report"]["data"] == run.daily["source"]
    assert out["report"]["run"] == alerts.RUN and out["path"] == f"reports/{alerts.RUN}/2026-07-07.json"
    out = run.report([second])
    assert out["examined"] == 30 and out["complete"]
    saved = json.loads((run.folder / "2026-07-07.json").read_text())
    assert saved["sha256"] == run.pin.candidates[0].sha256 and saved["source"] == run.daily["source"]
    # restating a city replaces its entry: a call that examines an alert city with no entry removes its alert
    city = out["report"]["alerts"][0]["city"]
    out = run.report([{"task": run.task, "examined": [city], "alerts": [], "unavailable": []}])
    assert not any(entry["city"] == city for entry in out["report"]["alerts"]) and out["complete"]


def test_a_broken_report_file_in_the_runs_folder_is_replaced_not_merged(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    (run.folder / "2026-07-07.json").write_text(json.dumps({"date": "2026-07-07", "run": "old", "examined": list(CITY_NAMES)}))
    first = scripted_judge.halves(run.task, scripted_judge.judge(run.task, run.daily))[0]
    assert run.report([first])["examined"] == 15


def stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_old_run_folders_are_pruned_but_never_young_ones(offline, monkeypatch) -> None:
    workspace = Path(tempfile.mkdtemp())
    old = T0.timestamp() - 7200  # the markers and the pruning read the plugin's clock: frozen at T0 here
    for index in range(40):
        folder = workspace / "reports" / f"{index:016x}"
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(json.dumps({"run": folder.name, "started": stamp(old + index)}))
    young = workspace / "reports" / "ffffffffffffffff"
    young.mkdir()
    (young / "run.json").write_text(json.dumps({"run": young.name, "started": stamp(T0.timestamp())}))
    held = set(Path(tempfile.gettempdir()).glob("weather-alert-pruned-*"))
    monkeypatch.setattr(alerts, "RUN", "eeeeeeeeeeeeeeee")
    alerts.ensure_run_folder(workspace)
    left = sorted(path.name for path in (workspace / "reports").iterdir())
    assert "ffffffffffffffff" in left and "eeeeeeeeeeeeeeee" in left
    assert len(left) == 30, "30 run folders in all: this run and the 29 newest others"
    assert f"{0:016x}" not in left and f"{39:016x}" in left
    assert set(Path(tempfile.gettempdir()).glob("weather-alert-pruned-*")) == held, "pruned folders leave nothing behind"


def test_a_run_folder_marker_names_the_run_and_when_its_first_call_made_it(offline) -> None:
    """R2-01: the marker's time is the plugin's clock at the run's first tool call, after the answer key."""
    run = Run(task_for("2026-07-07"), offline, clock=T0, agent_after=timedelta(seconds=42))
    run.read()
    marker = json.loads((run.folder / "run.json").read_text())
    assert marker == {"run": alerts.RUN, "started": "2026-10-16T04:00:42Z"}


def test_workspace_writes_never_leave_a_temporary_file_for_a_concurrent_copy(tmp_path: Path) -> None:
    """R2-03: the host stages a second task call by copying the workspace (shutil.copytree, as NanoAgent's
    web staging does) while a first run may be writing. The temporary file lives in the launch's TMPDIR and
    is renamed into place, so a copy never lists a file that then vanishes: no copy fails, none holds a .tmp."""
    workspace = tmp_path / "member-folder"
    folder = workspace / "reports" / "0123456789abcdef"
    folder.mkdir(parents=True)
    names = [folder / f"{index}.json" for index in range(4)]
    stop = threading.Event()
    writes = [0]

    def writer() -> None:
        while not stop.is_set():
            for name in names:
                alerts.write_atomic(name, json.dumps({"n": writes[0], "pad": "x" * 2000}))
                writes[0] += 1

    threads = [threading.Thread(target=writer) for _ in range(3)]
    for thread in threads:
        thread.start()
    failures, temporary, copies = [], [], 0
    try:
        for index in range(300):
            target = tmp_path / f"stage-{index}"
            try:
                shutil.copytree(workspace, target, dirs_exist_ok=True)
            except shutil.Error as error:
                failures.append(str(error)[:200])
            temporary += [path.name for path in target.rglob("*.tmp")]
            copies += 1
            shutil.rmtree(target, ignore_errors=True)
    finally:
        stop.set()
        for thread in threads:
            thread.join()
    assert writes[0] > 300 and copies == 300
    assert failures == [] and temporary == []
    assert alerts.WRITES == {"beside": 0, "cross_device": False}, "on one volume every write goes through TMPDIR"
    assert all(json.loads(name.read_text())["pad"] == "x" * 2000 for name in names)


def test_a_write_falls_back_beside_the_file_when_tmpdir_is_on_another_volume(tmp_path: Path, monkeypatch) -> None:
    """When the rename from TMPDIR fails across volumes (EXDEV), the write goes through a temporary file
    beside the target, as before, and the process stops trying TMPDIR; the call log notes it."""
    import errno
    import os

    real = os.replace
    crossings = []

    def replace(source, target) -> None:
        if not str(source).startswith(str(tmp_path)):
            crossings.append(source)
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real(source, target)

    monkeypatch.setattr(alerts, "WRITES", {"beside": 0, "cross_device": False})
    monkeypatch.setattr(alerts.os, "replace", replace)
    for value in range(3):
        alerts.write_atomic(tmp_path / "report.json", json.dumps({"value": value}))
    assert json.loads((tmp_path / "report.json").read_text()) == {"value": 2}
    assert len(crossings) == 1, "after one cross-device failure, no more TMPDIR attempts"
    assert alerts.WRITES == {"beside": 3, "cross_device": True}
    assert [path.name for path in tmp_path.iterdir()] == ["report.json"]
    assert not list(Path(tempfile.gettempdir()).glob(".report.json.*.tmp")), "the failed TMPDIR file is removed"


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"alerts": [{"city": "Oslo", "values": {"max_temp": 1.0}, "matched": ["max_temp >= 95"]}]}, "exactly the variables"),
        ({"alerts": [{"city": "Oslo", "values": {"max_temp": 1.0, "rain": 0.0, "wind": 1.0, "humidity": 1.0}, "matched": ["wind > 1"]}]}, "does not have"),
        ({"examined": ["Oslo"], "unavailable": [{"city": "Lima", "variables": ["rain"]}]}, "must be listed in examined"),
        ({"examined": ["Oslo", "Oslo"]}, "distinct"),
    ],
)
def test_report_alerts_refuses_malformed_judgements(offline, change: dict, words: str) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    run.read()
    call = {"task": run.task, "examined": ["Oslo"], "alerts": [], "unavailable": [], **change}
    with pytest.raises(ValueError, match=words):
        alerts.report_alerts(build_output(call, ReportInput), run.workspace)


def test_the_answer_key_refuses_a_date_too_far_ahead_naming_today(offline) -> None:
    with pytest.raises(ValueError, match=r"Today is 2026-10-16 \(UTC\)"):
        alerts.pin_alerts(build_output(task_for("2027-06-01"), AlertInput), Path("."))


@pytest.mark.parametrize("minute", [0, 5, 9])
def test_check_date_refuses_a_date_the_answer_key_would_refuse_saying_when_it_works(offline, minute: int) -> None:
    """R2-04: from 00:00 to 00:10 UTC on the 7th the new outlook month is refused by a daily-alert task's
    answer key (its agent's tools read the clock 10 minutes behind); check_date says so, and from when."""
    offline.clock = lambda: datetime.fromisoformat(f"2026-10-07T00:{minute:02d}:30+00:00")
    answer = plain_output(alerts.check_date(build_output({"date": "2027-04-15"}, DateInput), Path(".")))
    assert answer["kind"] is None and answer["weekday"] == "Thursday" and answer["outlook_last"] == "2027-04-30"
    assert answer["source"].endswith("a task on it works from 2026-10-07T00:10 UTC.")
    assert answer["message"].startswith("2027-04-15 cannot be judged: 2027-04-15 came into range only in the last 10 minutes")
    offline.clock = lambda: datetime.fromisoformat("2026-10-07T00:10:00+00:00")
    answer = plain_output(alerts.check_date(build_output({"date": "2027-04-15"}, DateInput), Path(".")))
    assert answer["kind"] == "outlook", "from 00:10 the date works, as it does for daily-alert"


def test_check_date_answers_today_from_the_clock(offline) -> None:
    answer = plain_output(alerts.check_date(build_output({"date": "today"}, DateInput), Path(".")))
    assert answer["today"] == answer["date"] == "2026-10-16" and answer["weekday"] == "Friday"
    assert answer["kind"] == "forecast" and "ECMWF IFS forecast issued 2026-10-15 06:00 UTC" in answer["source"]
    assert answer["forecast_last"] == "2026-10-29" and answer["outlook_last"] == "2027-04-30"


def test_every_call_leaves_one_line_in_the_call_log(offline) -> None:
    run = Run(task_for("2026-07-07"), offline, clock=T0)
    alerts.log_call(run.workspace, "get_daily", {"task": run.task, "cities": list(CITY_NAMES)}, "ok", 0.5)
    alerts.log_call(run.workspace, "report_alerts", {"task": run.task, "examined": ["Oslo"], "alerts": [], "unavailable": []}, "error: x", 0.1)
    lines = [json.loads(line) for line in (run.folder / "calls.log").read_text().splitlines()]
    assert [line["tool"] for line in lines] == ["get_daily", "report_alerts"]
    assert lines[0]["cities"] == 30 and lines[1]["examined"] == 1 and lines[1]["result"] == "error: x"

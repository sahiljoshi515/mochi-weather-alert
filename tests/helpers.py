"""A whole daily-alert run, offline: the answer key, the agent's read, a scripted judgement, the two
report calls, and the verdict, with the clocks and answers a test chooses."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import scripted_judge
from slimconfig import build_output, plain_output

import verifier
from contract import AlertInput, AlertReportOutput, DailyInput, PinOutput, ReportInput
from plugin import alerts, sources
from plugin.rules import CITY_NAMES

TASK = {"match": "any", "temperature_unit": "F", "wind_unit": "mph", "rain_unit": "in", "conditions": ["max_temp >= 95", "humidity >= 85", "rain > 0.5", "wind >= 20"]}


def task_for(date: str, **changes) -> dict:
    return {"date": date, **TASK, **changes}


class Run:
    """One run's pieces: the pin, the agent's workspace, its get_daily answer, and the verdict."""

    def __init__(
        self,
        task: dict,
        fake,
        *,
        clock: datetime,
        agent_after: timedelta = timedelta(seconds=20),
        workspace: Path | None = None,
        staging: str = "web",
    ) -> None:
        self.task = task
        self.fake = fake
        self.workspace = workspace or Path(tempfile.mkdtemp(prefix="weather-run-"))
        fake.clock = lambda: clock
        sources.CACHE.clear()
        # The answer key works in a scratch folder: a copy of the workspace, as the site stages it, or empty
        # but for the task's inputs, as the command line stages it ("cli").
        scratch = Path(tempfile.mkdtemp(prefix="weather-ref-"))
        if staging == "web":
            shutil.copytree(self.workspace, scratch, dirs_exist_ok=True)
        self.pin = alerts.pin_alerts(build_output(task, AlertInput), scratch)
        fake.clock = lambda: clock + agent_after
        self.daily = None
        self.last = None

    @property
    def folder(self) -> Path:
        """This run's folder in the workspace (the plugin process's run, alerts.RUN)."""
        return self.workspace / "reports" / alerts.RUN

    def read(self, cities=None) -> dict:
        given = {"task": self.task, "cities": list(cities or CITY_NAMES)}
        self.daily = plain_output(alerts.get_daily(build_output(given, DailyInput), self.workspace))
        return self.daily

    def report(self, calls: list[dict]) -> dict:
        for call in calls:
            self.last = plain_output(alerts.report_alerts(build_output(call, ReportInput), self.workspace))
        return self.last

    def gold(self) -> dict:
        self.read()
        return self.report(scripted_judge.plan(self.task, self.daily)["calls"])["report"]

    def verdict(self, submitted: dict | None = None) -> dict:
        submitted = submitted if submitted is not None else self.last["report"]
        arguments = build_output(self.task, AlertInput)
        output = build_output(submitted, AlertReportOutput)
        reference = build_output(plain_output(self.pin), PinOutput)
        first = verifier.verify_task("daily-alert", arguments, output, reference, str(self.workspace))
        second = verifier.verify_task("daily-alert", build_output(self.task, AlertInput), build_output(submitted, AlertReportOutput), build_output(plain_output(self.pin), PinOutput), str(self.workspace))
        assert json.dumps(first, sort_keys=True, default=str) == json.dumps(second, sort_keys=True, default=str), "the verdict must be deterministic"
        assert (first["score"] == 1.0) == first["valid"], "a score of 1.0 means valid, and only then"
        return first


def failed(verdict: dict) -> list[str]:
    return [entry["id"] for entry in verdict["details"]["constraints"] if entry["ok"] is False]


def unchecked(verdict: dict) -> list[str]:
    return [entry["id"] for entry in verdict["details"]["constraints"] if entry["ok"] is None]

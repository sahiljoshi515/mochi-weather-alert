"""The MCP server over stdio, as NanoAgent speaks to it: schemas, a gold path, errors and the call log."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import pytest
import scripted_judge
from conftest import T0
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from slimconfig import build_output, output_schema, plain_output

import verifier
from contract import TOOLS, AlertInput, AlertReportOutput, PinOutput
from plugin import alerts, sources

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-09-06"
TASK = {"date": DATE, "match": "any", "temperature_unit": "F", "wind_unit": "ms", "rain_unit": "mm", "conditions": ["min_temp < 50", "humidity >= 85"]}


async def talk(workspace: Path, script):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "tests" / "offline_server.py"), T0.isoformat()],
        cwd=str(ROOT),
        env={**os.environ, "NANOAGENT_WORKSPACE": str(workspace)},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        return await script(session)


def test_offers_every_tool_with_its_contract_schemas(tmp_path: Path) -> None:
    async def script(session):
        return {tool.name: tool for tool in (await session.list_tools()).tools}

    offered = anyio.run(talk, tmp_path, script)
    assert set(offered) == {tool.value for tool in TOOLS}
    for tool, (given, made) in TOOLS.items():
        assert offered[tool.value].inputSchema == output_schema(given)
        assert offered[tool.value].outputSchema == output_schema(made)


def test_runs_a_gold_path_over_stdio(tmp_path: Path) -> None:
    async def script(session):
        early = await session.call_tool("report_alerts", {"task": TASK, "examined": ["Oslo"], "alerts": [], "unavailable": []})
        assert early.isError and "call get_daily for 2026-09-06 first" in early.content[0].text
        daily = await session.call_tool("get_daily", {"task": TASK, "cities": scripted_judge.CITIES})
        assert not daily.isError and daily.structuredContent["source"]["label"] == "ECMWF IFS archive (analysis), hourly"
        santiago = next(row for row in daily.structuredContent["cities"] if row["city"] == "Santiago")
        assert santiago["samples"] == 23, "Santiago's 23-hour day"
        last = None
        for call in scripted_judge.plan(TASK, daily.structuredContent)["calls"]:
            last = await session.call_tool("report_alerts", call)
            assert not last.isError
        assert last.structuredContent["complete"] and last.structuredContent["report"]["data"] == daily.structuredContent["source"]
        unknown = await session.call_tool("get_daily", {"task": TASK, "cities": ["Atlantis"]})
        assert unknown.isError and "unknown city Atlantis" in unknown.content[0].text
        far = await session.call_tool("get_daily", {"task": {**TASK, "date": "2031-01-01"}, "cities": ["Oslo"]})
        assert far.isError and "the last day the ECMWF seasonal outlook covers" in far.content[0].text
        today = await session.call_tool("check_date", {"date": "today"})
        assert not today.isError and today.structuredContent["today"] == "2026-10-16"
        return last.structuredContent

    last = anyio.run(talk, tmp_path, script)
    run = last["report"]["run"]
    marker = json.loads((tmp_path / "reports" / run / "run.json").read_text())
    saved = json.loads((tmp_path / "reports" / run / f"{DATE}.json").read_text())
    receipt = json.loads((tmp_path / "reports" / run / f"{DATE}.source.json").read_text())
    assert saved["run"] == receipt["run"] == marker["run"] == run and saved["sha256"] == receipt["sha256"]
    logged = [json.loads(line) for line in (tmp_path / "reports" / run / "calls.log").read_text().splitlines()]
    assert [entry["tool"] for entry in logged] == ["report_alerts", "get_daily", "report_alerts", "report_alerts", "get_daily", "get_daily", "check_date"]
    assert logged[0]["result"].startswith("error:") and logged[1]["source"] == "ECMWF IFS archive (analysis), hourly"


async def flow(session, task: dict) -> dict:
    """One agent's prescribed path over an open session: read, judge, report twice; the submitted report."""
    daily = await session.call_tool("get_daily", {"task": task, "cities": scripted_judge.CITIES})
    assert not daily.isError, daily.content[0].text
    last = None
    for call in scripted_judge.plan(task, daily.structuredContent)["calls"]:
        last = await session.call_tool("report_alerts", call)
        assert not last.isError, last.content[0].text
        await anyio.sleep(0.05)  # let the other run's calls in between
    return last.structuredContent["report"]


@pytest.mark.parametrize("same_date", [False, True])
def test_two_runs_at_once_in_one_workspace_are_judged_apart(offline, tmp_path: Path, same_date: bool) -> None:
    """WA-01: two task calls of one chat step run at once in one workspace (NanoAgent dispatches a step's
    calls with asyncio.gather): two plugin processes, interleaved calls, both submissions valid."""
    tasks = [
        {"date": DATE, "match": "any", "temperature_unit": "C", "wind_unit": "kmh", "rain_unit": "mm", "conditions": ["max_temp >= 30"]},
        {"date": DATE if same_date else "2026-07-07", "match": "all", "temperature_unit": "F", "wind_unit": "mph", "rain_unit": "in", "conditions": ["rain > 0.1"]},
    ]
    pins = []
    for task in tasks:  # the answer keys, before either agent starts (in a copy of the then-empty workspace)
        sources.CACHE.clear()
        pins.append(plain_output(alerts.pin_alerts(build_output(task, AlertInput), tmp_path / f"scratch-{len(pins)}")))
    workspace = tmp_path / "member-folder"
    workspace.mkdir()

    async def both():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "tests" / "offline_server.py"), T0.isoformat()],
            cwd=str(ROOT),
            env={**os.environ, "NANOAGENT_WORKSPACE": str(workspace)},
        )
        reports = [None, None]
        async with stdio_client(params) as (read_a, write_a), ClientSession(read_a, write_a) as first, stdio_client(params) as (read_b, write_b), ClientSession(read_b, write_b) as second:
            await first.initialize()
            await second.initialize()

            async def one(index, session):
                reports[index] = await flow(session, tasks[index])

            async with anyio.create_task_group() as group:
                group.start_soon(one, 0, first)
                group.start_soon(one, 1, second)
        return reports

    reports = anyio.run(both)
    assert reports[0]["run"] != reports[1]["run"]
    assert sorted(path.name for path in (workspace / "reports").iterdir()) == sorted(report["run"] for report in reports)
    for task, pin, report in zip(tasks, pins, reports, strict=True):
        found = verifier.verify_task("daily-alert", build_output(task, AlertInput), build_output(report, AlertReportOutput), build_output(pin, PinOutput), str(workspace))
        assert found["valid"], found["message"]

"""The MCP server over stdio, as NanoAgent speaks to it: schemas, a gold path, errors and the call log."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import scripted_judge
from conftest import T0
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from slimconfig import output_schema

from contract import TOOLS

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

    anyio.run(talk, tmp_path, script)
    marker = json.loads((tmp_path / "reports" / "run.json").read_text())
    saved = json.loads((tmp_path / "reports" / f"{DATE}.json").read_text())
    receipt = json.loads((tmp_path / "reports" / f"{DATE}.source.json").read_text())
    assert saved["run"] == receipt["run"] == marker["run"] and saved["sha256"] == receipt["sha256"]
    logged = [json.loads(line) for line in (tmp_path / "reports" / "calls.log").read_text().splitlines()]
    assert [entry["tool"] for entry in logged] == ["report_alerts", "get_daily", "report_alerts", "report_alerts", "get_daily", "get_daily", "check_date"]
    assert logged[0]["result"].startswith("error:") and logged[1]["source"] == "ECMWF IFS archive (analysis), hourly"

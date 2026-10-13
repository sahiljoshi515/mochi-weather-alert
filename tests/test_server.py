"""The MCP server over stdio, as NanoAgent speaks to it: schemas, calls, and errors."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from slimconfig import output_schema

from contract import TOOLS

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-09-06"


async def talk(workspace: Path, script):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "plugin"], cwd=str(ROOT), env={**os.environ, "NANOAGENT_WORKSPACE": str(workspace)}
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
        listed = await session.call_tool("list_cities", {"date": DATE})
        assert not listed.isError
        names = [row["city"] for row in listed.structuredContent["cities"]]
        daily = await session.call_tool("get_daily", {"cities": names[:10], "date": DATE})
        assert not daily.isError and len(daily.structuredContent["cities"]) == 10
        hours = await session.call_tool("get_hourly", {"city": "Santiago", "date": DATE})
        assert not hours.isError and len(hours.structuredContent["rows"]) == 23
        report = {"date": DATE, "examined": ["Cairo"], "alerts": [{"city": "Cairo", "values": {"max_temp": 98.8}, "matched": ["max_temp >= 95"]}], "unavailable": []}
        saved = await session.call_tool("report_alerts", report)
        assert not saved.isError and saved.structuredContent == {"path": f"weather-alert/{DATE}.json", "alerts": 1, "unavailable": 0, "examined": 1}
        unknown = await session.call_tool("get_daily", {"cities": ["Atlantis"], "date": DATE})
        assert unknown.isError and "unknown city Atlantis" in unknown.content[0].text
        bad_date = await session.call_tool("list_cities", {"date": "2026-12-25"})
        assert bad_date.isError and "outside the snapshot" in bad_date.content[0].text
        return report

    report = anyio.run(talk, tmp_path, script)
    assert json.loads((tmp_path / "weather-alert" / f"{DATE}.json").read_text()) == report


@pytest.mark.network
def test_network_cases_are_skipped_offline() -> None:
    assert os.environ.get("NANOAGENT_NETWORK"), "only runs with NANOAGENT_NETWORK set"

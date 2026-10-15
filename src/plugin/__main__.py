"""The MCP server: each call's input built into its contract class, its output checked against its own."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from slimconfig import build_output, output_schema, plain_output

from contract import TOOLS, Tool
from plugin import get_daily, get_hourly, list_cities, log_call, pin_alerts, report_alerts, write_run_marker

server = Server("weather-alert")
# NanoAgent names the one folder this plugin may write to.
WORKSPACE_ENV = "NANOAGENT_WORKSPACE"
# Each tool's implementation: (its input, the workspace) -> its output, as contract.TOOLS types them.
IMPLEMENTATIONS = {
    Tool.PIN_ALERTS: pin_alerts,
    Tool.LIST_CITIES: list_cities,
    Tool.GET_DAILY: get_daily,
    Tool.GET_HOURLY: get_hourly,
    Tool.REPORT_ALERTS: report_alerts,
}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(name=tool.value, inputSchema=output_schema(given), outputSchema=output_schema(made))
        for tool, (given, made) in TOOLS.items()
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    # A failure raises; the MCP server returns it as an error result. Either way the call is logged.
    tool = Tool(name)
    given, made = TOOLS[tool]
    workspace = Path(os.environ[WORKSPACE_ENV])
    started = time.perf_counter()
    try:
        output = IMPLEMENTATIONS[tool](build_output(arguments, given), workspace)
        if not isinstance(output, made):
            raise TypeError(f"{name} returned {type(output).__name__}, not {made.__name__}")
    except Exception as error:
        log_call(workspace, name, arguments, f"error: {error}", time.perf_counter() - started)
        raise
    log_call(workspace, name, arguments, "ok", time.perf_counter() - started)
    return plain_output(output)


def main() -> None:
    if WORKSPACE_ENV not in os.environ:
        raise SystemExit(f"{WORKSPACE_ENV} is not set: NanoAgent passes the plugin's workspace folder")
    if set(IMPLEMENTATIONS) != set(Tool):
        raise SystemExit("IMPLEMENTATIONS must implement every contract.Tool")
    # This run's marker: a report file left in the workspace by an earlier run is never taken for this run's.
    write_run_marker(Path(os.environ[WORKSPACE_ENV]))

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()

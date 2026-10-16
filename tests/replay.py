"""Replay a scripted case offline, the way ~/mochi-dev/harness/scripted_task.py runs it under NanoAgent:
the answer key first (its own scratch folder), then each step through the real tools, python steps
behind the tools' back, and the verdict on what the submit step hands in. Open-Meteo and the clock are
whatever the caller has put in plugin.openmeteo.OPENER and plugin.sources.CLOCK."""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from slimconfig import build_output, plain_output

import verifier
from contract import TASKS, TOOLS, Task, Tool
from plugin import alerts, sources

IMPLEMENTATIONS = {
    Tool.GET_DAILY: alerts.get_daily,
    Tool.REPORT_ALERTS: alerts.report_alerts,
    Tool.CHECK_DATE: alerts.check_date,
}


def resolve(value, outputs):
    """A whole-string "$ref:<save_as>.<key>..." as the harness resolves it."""
    if isinstance(value, str) and value.startswith("$ref:"):
        node = outputs
        for part in value[5:].split("."):
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node
    if isinstance(value, list):
        return [resolve(item, outputs) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, outputs) for key, item in value.items()}
    return value


def stage_agent_folder(case: dict, base: Path | None, workspace: Path) -> None:
    """The member's agent folder a case names (a tar or a folder), unpacked into the workspace."""
    if not case.get("agent_folder"):
        return
    source = Path(case["agent_folder"])
    source = source if source.is_absolute() else (base or Path(".")) / source
    if source.is_dir():
        shutil.copytree(source, workspace, dirs_exist_ok=True)
    else:
        with tarfile.open(source) as archive:
            archive.extractall(workspace, filter="data")


def replay(case: dict, base: Path | None = None) -> dict:
    """The verdict of a case, or {"error": ...} when the run is refused before the agent starts. ``base``
    resolves a relative agent_folder (the cases folder)."""
    return played(case, base)[0]


def played(case: dict, base: Path | None = None) -> tuple[dict, dict | None]:
    """(the verdict of a case, or {"error": ...}; the task's output as JSON, or None when there is none)."""
    task = Task(case["task"])
    given_class, output_class = TASKS[task]
    arguments = build_output(case["arguments"], given_class)
    workspace = Path(tempfile.mkdtemp(prefix="weather-case-"))
    stage_agent_folder(case, base, workspace)
    sources.CACHE.clear()
    if task is Task.CHECK_DATE:
        output = alerts.check_date(arguments, workspace)
        return verifier.verify_task(task.value, arguments, output, None, str(workspace)), plain_output(output)
    try:
        check = verifier.verify_input("pin_alerts", arguments)
        if not check["valid"]:
            raise ValueError(f"plugin input verification failed: {check['message']}")
        scratch = Path(tempfile.mkdtemp(prefix="weather-case-ref-"))
        if case.get("agent_folder"):  # the site's staging: the answer key's scratch is a copy of the workspace
            shutil.copytree(workspace, scratch, dirs_exist_ok=True)
        reference = alerts.pin_alerts(arguments, scratch)
    except (ValueError, RuntimeError) as error:
        return {"error": f"{type(error).__name__}: {error}"}, None
    outputs: dict = {}
    submitted = None
    for step in case["steps"]:
        if "python" in step:
            exec(step["python"], {"workspace": workspace, "outputs": outputs, "json": json, "Path": Path})  # noqa: S102 - the case's own step
            continue
        name = step["tool"]
        values = resolve(step.get("arguments", {}), outputs)
        if name == "submit":
            submitted = values
            continue
        tool = Tool(name)
        try:
            output = plain_output(IMPLEMENTATIONS[tool](build_output(values, TOOLS[tool][0]), workspace))
            error = False
        except (ValueError, RuntimeError):
            output, error = None, True
        if error != bool(step.get("expect_error", False)):
            raise AssertionError(f"step {name}: expected error={step.get('expect_error', False)}, got error={error}")
        if not error and step.get("save_as"):
            outputs[step["save_as"]] = output
    if submitted is None:
        return {"error": "the agent ended the task without calling submit"}, None
    output = build_output(submitted, output_class)
    reference = build_output(plain_output(reference), TOOLS[Tool.PIN_ALERTS][1])
    return verifier.verify_task(task.value, arguments, output, reference, str(workspace)), plain_output(output)


def matches(found: dict, expect: dict) -> list[str]:
    """What differs between a verdict (or a refusal) and a case's expectation."""
    problems = []
    if "error_contains" in expect:
        if expect["error_contains"] not in found.get("error", ""):
            problems.append(f"expected a refusal with {expect['error_contains']!r}, got {found.get('error') or found.get('message')!r}")
        return problems
    if "error" in found:
        return [f"the run was refused: {found['error']}"]
    if "valid" in expect and found["valid"] != expect["valid"]:
        problems.append(f"valid={found['valid']} expected {expect['valid']}: {found['message']}")
    if "score_min" in expect and found["score"] < expect["score_min"]:
        problems.append(f"score {found['score']} below {expect['score_min']}")
    if "score_max" in expect and found["score"] > expect["score_max"]:
        problems.append(f"score {found['score']} above {expect['score_max']}")
    if "message_contains" in expect and expect["message_contains"] not in found["message"]:
        problems.append(f"message {found['message']!r} lacks {expect['message_contains']!r}")
    return problems

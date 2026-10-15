# Weather Alert

A `nanoagent.plugin/v1` plugin for NanoAgent 0.5, with the structure of the
reference plugin, [arxiv-source](https://github.com/zeyuyang8/arxiv-source).
Its task, `daily-alert`, asks an agent which of 30 world cities had a day that
crossed a few weather thresholds, in the units the person chose, and to submit
each city's values exactly. The verifier recomputes everything from a pinned
snapshot and demands an exact match of the city set, every value and every
matched rule.

## Tools

| Tool | Does | Code |
|---|---|---|
| `pin_alerts` | Every city's local day in metric units (the exact ingredients of each value) plus the alerts and unavailable cities the rules give; the task's pinned reference | `src/plugin/alerts.py` |
| `list_cities` | The 30 cities, each with its country, time zone and how many hours its local calendar day has on the date (23, 24 or 25) | `src/plugin/alerts.py` |
| `get_daily` | Up to 30 cities' local-day aggregates on a date, so one call covers every city: highest and lowest temperature (°C), precipitation total (mm), highest wind (km/h), mean humidity (%), hour count and the variables with a missing hour | `src/plugin/alerts.py` |
| `get_hourly` | One city's hourly rows over its local calendar day, in metric units, for inspecting one day | `src/plugin/alerts.py` |
| `report_alerts` | Merges one call's judgement of up to 30 `examined` cities, under the task input it repeats, into `reports/<date>.json`: the cities a call examines are restated by it (an examined city with no entry has no alert), every other city's entries are kept. Atomic per call; returns the merged report so far, how many cities it examines and whether that is every city; the first call of a run replaces any file an earlier run left | `src/plugin/alerts.py` |

### Tasks

| Task | Does | Runner |
|---|---|---|
| `daily-alert` | Finds every city whose local day on a date crossed the given thresholds and submits each one's values in the requested units | `agent`, with `get_daily` and `report_alerts`; reference `pin_alerts` |

The tools return metric data only. Converting to the requested units, rounding
and applying the rules is the agent's work. People use the plugin by chatting:
the chat agent turns a request such as "which cities had a high of at least
95 °F or more than half an inch of rain on 7 July 2026?" into the task's input
(the date, `all` or `any`, the units and one to six conditions like
`max_temp >= 95`), and the page shows the submitted report with its verdict.

### The task, step by step

Before the agent starts, NanoAgent runs `pin_alerts` with the task input in a
scratch workspace the agent never sees and pins its output: every city's day
in metric units and the alerts the shared rules give. The agent then has
`get_daily`, `report_alerts` and `submit`, and six steps:

1. `get_daily` once with all 30 cities (about 1,600 tokens of aggregates).
2. to 4. `report_alerts` three times, ten cities each, in the order the
   instructions list them. Each call repeats the task input, names the cities
   it `examined`, and gives every alert city its `values` (one per variable
   the conditions use, in the requested units, rounded half up to one
   decimal) and `matched` conditions, and every `unavailable` city the
   variables it has a missing hour for. The call merges that judgement into
   `reports/<date>.json` and answers with the merged report so far, how many
   cities are examined and whether that is every city.
5. `submit` with the report the last call returned, as it is.
6. The final answer.

Ten cities per step keeps each reply, reasoning included, inside the hosted
model's output cap; the five tool calls fit the hosted agent's six steps.

### The verdict

`verifier/pin.py` judges the submitted report against the pinned reference:
the alerts are recomputed from the pinned metric aggregates with the shared
rules (`verifier/rules.py`, a byte-for-byte copy of `src/plugin/rules.py`,
which a test enforces), never taken from the pin's own list. The verdict is
valid only when the submitted report is for the task's input, is exactly the
report this run saved (the file carries the run marker the plugin process
wrote at its start, so a file an earlier run left is never inherited), that
file examines all 30 cities, and the submitted alerts and unavailable cities
are exactly the expected ones, value for value. The score is the F1 of the
submitted entries against the expected ones, scaled by the share of cities
examined, so a wrong value, a missing or extra city and an unexamined city
all cost.

Every tool call is checked too: `verify_input` refuses a date outside the
snapshot, a malformed condition, an alert whose values are not exactly the
needed variables or whose matched conditions are not the task's; and
`verify_output` checks the aggregates are well formed, the pin agrees with its
own aggregates, and a report call's returned report holds that call's
judgement of every city it examined.

### The data

`src/plugin/data/` holds a pinned snapshot: hourly temperature, relative
humidity, precipitation and wind speed for 30 cities from 2026-07-01 to
2026-09-30, fetched from Open-Meteo's archive in UTC and converted to each
city's local calendar day with its IANA time zone, so a day has 23 hours
when clocks go forward (Santiago on 2026-09-06, Auckland on 2026-09-27) and
25 when they go back. `provenance.json` records when and how it was fetched;
`src/plugin/tools_dev/fetch_snapshot.py` fetches it again and
`src/plugin/tools_dev/build_cases.py` rebuilds the scripted cases under
`tests/cases/`.

## Layout

```text
.
├── nanoagent.yaml   # manifest (a slimconfig ManifestConfig): id, runtime, permissions, tools, tasks
├── contract.py      # each tool's and task's typed input and output: slimconfig Output classes
├── README.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .gitignore
├── .env             # needed environment variable names (none here)
├── run.sh           # checks this environment can run the plugin and that the snapshot is in place
├── src/plugin/      # the MCP server (__main__.py, its IMPLEMENTATIONS table), the tools, the rules, the snapshot
├── tests/           # unit tests, and scripted cases under tests/cases/ for NanoAgent's task runner
├── ui/              # how the page draws the submitted report: renderers.js and renderers.css
├── product/         # the product page a catalog shows: product.yaml and icon.svg
└── verifier/        # deterministic verifier package: one module per tool, the task's verdict in pin.py
```

`contract.py` types every value: the server builds each call's input into its
class and checks its output against its own, and NanoAgent and the verifier
get the same classes. A failing tool raises, and the MCP server returns an
error result.

`verifier/` is deterministic: it imports only the standard library and
`contract`, reads only the run's workspace, and every arithmetic step is exact
(`Decimal` and `Fraction`), so a comparison never sees a binary float.

## Develop and run

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

NanoAgent passes the folder the plugin may write to as `NANOAGENT_WORKSPACE`;
the report goes to `reports/<date>.json` in it. Publishing checks every plugin
rule: push the plugin to a public GitHub repository and run
`rush publish <server> <github-url>`, or submit the URL on the marketplace.

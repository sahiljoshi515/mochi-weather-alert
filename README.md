# Weather Alert

A `nanoagent.plugin/v1` plugin, created by `nanoagent plugin new` with the
structure of NanoAgent's reference plugin, `plugins/arxiv-source`. Its task,
`daily-alert`, asks an agent which of 30 world cities had a day that crossed a
few weather thresholds, in the units the user chose, and to report each city's
values exactly. The verifier recomputes everything from a pinned snapshot and
demands an exact match of the city set, every value and every matched rule.

## Tools

| Tool | Does | Code |
|---|---|---|
| `pin_alerts` | Every city's local day in metric units (the exact ingredients of each value) plus the alerts and unavailable cities the rules give; the task's pinned reference | `src/plugin/alerts.py` |
| `list_cities` | The 30 cities, each with its country, time zone and how many hours its local calendar day has on the date (23, 24 or 25); optional, since the task's instructions name the cities | `src/plugin/alerts.py` |
| `get_daily` | Up to 30 cities' local-day aggregates on a date, so one call covers every city: highest and lowest temperature (°C), precipitation total (mm), highest wind (km/h), mean humidity (%), hour count and the variables with a missing hour | `src/plugin/alerts.py` |
| `get_hourly` | One city's hourly rows over its local calendar day, in metric units; for inspecting one day (about 700 tokens), not the normal path | `src/plugin/alerts.py` |
| `report_alerts` | Merges one call's judgement of up to 30 `examined` cities into `weather-alert/<date>.json`: the cities a call examines are restated by it (an examined city with no entry has no alert), every other city's entries are kept; atomic per call, returns the call's own counts, and the first call of a run replaces any file an earlier run left | `src/plugin/alerts.py` |

The tools return metric data only. Converting to the requested units, rounding
and applying the rules is the agent's work. The outputs are compact: `list_cities`
is about 800 tokens and `get_daily` with all 30 cities about 1,600, so an agent
reads the whole day in two calls; `get_hourly` is for inspecting one city's day.

### Limits

Mochi's hosted runner allows an agent 6 steps per run, 2,048 output tokens per
step and a $0.10 budget, and members cannot raise them. The task is designed to
finish in 4 steps: `list_cities`, `get_daily` with every city, `report_alerts`,
then the answer. A report naming all 30 cities under six rules on five variables
is about 1,900 tokens, close to the per-step output cap; typical rule sets are far
smaller.

### The task

The input is a date, `match` (`all` or `any`), the units (`C`/`F`, `kmh`/`mph`/`ms`,
`mm`/`in`) and 1 to 6 conditions. A condition is `"<variable> <op> <number>"`:
variables `max_temp`, `min_temp`, `rain`, `wind`, `humidity`; operators `>=`,
`>`, `<=`, `<`; a number with at most 2 decimals, in the requested units
(humidity in %). Two conditions that mean the same are refused.

The agent reports with `report_alerts`, which merges by city: each call names
the cities it `examined` (1 to 30) with their alerts and unavailable entries,
replaces those cities' earlier entries and keeps every other city's. The final
file must have examined all 30 cities. The instructions prescribe one
`get_daily` with all 30 cities, then three `report_alerts` calls of 10 cities
each, then the answer: 4 tool calls of the 6 steps the hosted runner allows.

Each run is marked: the server writes `weather-alert/run.json` with a random
token when it starts, every report it writes carries that token, and a report
file found in the workspace without it (an earlier run's, or an older version's
shape) is replaced by the run's first call rather than merged into. A model
may issue several `report_alerts` calls in one turn, which NanoAgent runs at
once: each merge is atomic (a lock in the process, a temporary file and one
rename), the counts a call returns are its own entries, and its output check
only asks that the file holds that call's judgement.

### Rules

The grammar, the arithmetic and the evaluation live in one module,
`verifier/rules.py`, which `src/plugin/rules.py` copies byte for byte (a test
checks that): the reference tool and the verifier apply the same code.

- **The day** is each city's local calendar day in its IANA time zone: 23 hourly
  rows on a day clocks go forward, 25 when they go back, else 24. `max_temp` and
  `min_temp` are the highest and lowest hourly temperature, `rain` the sum of the
  hourly precipitation, `wind` the highest hourly wind speed, `humidity` the
  arithmetic mean of the hourly relative humidity over the hours present.
- **Conversions** are exact, in `Decimal`/`Fraction` arithmetic:
  °F = °C × 9/5 + 32, mph = km/h ÷ 1.609344, m/s = km/h ÷ 3.6, in = mm ÷ 25.4.
- **Comparisons** test the exact, unrounded converted value against the
  threshold. A city at exactly the threshold satisfies `>=` and `<=`, never `>`
  or `<`.
- **Reported values** are rounded to 1 decimal, half up (`ROUND_HALF_UP`, ties
  away from zero), in the requested units: 24.95 → 25.0, 71.25 → 71.3. The
  rounded value is never what is compared: 40.2 km/h is 24.979 mph, reported as
  25.0 but not `wind >= 25`.
- **Matching**: with `all` a city is an alert when every condition holds; with
  `any` when at least one does. An alert carries `values` (one entry per
  variable the conditions use) and `matched` (the conditions that hold, as
  given).
- **Missing data**: a city with a missing hour for a variable a condition uses
  (`max_temp`/`min_temp` come from `temperature_2m`, `rain` from
  `precipitation`, `wind` from `wind_speed_10m`, `humidity` from
  `relative_humidity_2m`) is listed under `unavailable` with those variables
  and is never an alert. A missing hour is never counted as 0.

### The snapshot

`src/plugin/data/` holds the pinned data, fetched once by
`src/plugin/tools_dev/fetch_snapshot.py` from the Open-Meteo historical weather
API (`archive-api.open-meteo.com`, ERA5-based, CC BY 4.0) for 30 cities and
2026-07-01 to 2026-09-30:

- `cities.json`: name, country, latitude, longitude and the IANA time zone the
  API resolved for the coordinates.
- `hourly.csv.gz`: `city`, local `time` with its UTC offset
  (`2026-09-06T01:00-03:00`), `temperature_2m` (°C), `relative_humidity_2m`
  (%), `precipitation` (mm), `wind_speed_10m` (km/h); a missing value is an
  empty field (the fetched snapshot has none).
- `provenance.json`: the request templates, the fetch time, each city's
  `utc_offset_seconds` as the API returned it, the row counts, and the days
  with other than 24 hours.

The API's `timezone=auto` series applies one fixed UTC offset to the whole
range and does not shift at a daylight-saving change, so its days always have
24 rows. The fetch script therefore also fetches the same hours in UTC (one day
wider on each side), checks that the auto series equals the UTC series shifted
by the API's offset, and converts the UTC series to wall-clock time in each
city's zone with `zoneinfo`. That gives the real local days: Santiago's
2026-09-06 and Auckland's 2026-09-27 have 23 hours (clocks went forward at
midnight and 02:00). No city in the snapshot has a 25-hour day in the range.
The plugin's tools and the verifier never touch the network.

### Verifier

`verifier/pin.py` recomputes the expected alerts from the pin's **metric**
aggregates (never from the pin's own alert list) with the shared rules, then
reads the merged report, which must carry this run's marker (a leftover of an
earlier run or of an older version is refused as "not a report of this run"):
every one of the pinned cities must be in its
`examined` list (an unexamined city or a name that is not a city makes the
verdict invalid, with the score scaled by the share of the pinned cities among
the pinned and the examined ones), and the set of alert cities, each
city's values and matched conditions, and the unavailable cities with their
variables must all be exactly equal. The score is the F1 of the report's
entries (alert and unavailable cities, an entry counting only when it is
exactly right) against the expected ones; the verdict is valid only at an
exact match. Details give the expected report, the unexamined cities, and the
missing, extra and mismatched entries. `verify_input` refuses malformed or
duplicate conditions, bad dates and dates outside the snapshot, more than 30
cities per `get_daily` call, and a `report_alerts` call that reports a city it
did not examine; `verify_output` of `report_alerts` checks the merged file
is this run's and holds that call's judgement of every city it examined, with
the counts being the call's own, and that of `pin_alerts` checks the pin agrees
with its own aggregates.

### Limits

Mochi's hosted runner allows an agent 6 steps per run, a reply of 2,048 tokens
per step (its hidden reasoning included) and a $0.10 budget, and members cannot
raise them. Converting and judging all 30 cities inside one reply overflowed
that cap in hosted runs, which ended with no report. So the report is made in
three `report_alerts` calls of 10 examined cities each, and the whole task takes
4 tool calls: `get_daily` with every city, the three reports, then the answer.
The instructions also tell the agent to keep each step brief, since a reply
that reasons too long before its tool call is cut off and ends the run.

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
├── .env             # environment variable NAMEs this plugin needs (none here)
├── run.sh           # exits 0 when this environment can run the plugin
├── src/plugin/      # the MCP server (__main__.py, its IMPLEMENTATIONS), the tools, the rules and the snapshot
│   ├── data/        # the pinned snapshot: cities.json, hourly.csv.gz, provenance.json
│   └── tools_dev/   # fetch_snapshot.py (refetch the snapshot), build_cases.py (the harness cases)
├── tests/           # unit tests; tests/cases/ holds the scripted harness cases
├── ui/              # the plugin's page: app.js, app.css, rules.js (the grammar), snapshot.js (the data note)
├── product/         # the product page: product.yaml and icon.svg
└── verifier/        # deterministic verifier package, one module per tool, plus rules.py
```

`contract.py` types every value: the server builds each call's input into its
class and checks its output against its own, and NanoAgent and the verifier get
the same classes. `verifier/` imports only the standard library and `contract`.

## Develop and run

```bash
uv sync --extra dev
uv run pytest
```

From the NanoAgent repository root:

```bash
.venv/bin/nanoagent plugin inspect plugins/weather-alert
```

The page cannot be opened without a host, so check it two ways before shipping:
`node --check ui/*.js` for syntax, and the lab's page check, which evaluates every
`ui/*.js` module together with the served kit under Node and catches what a
syntax check misses (duplicate exports, bad imports, a module that crashes at
load); every line must say OK:

```bash
bash ~/mochi-dev/harness/page_check.sh .
```

`uv run pytest` runs the same check when that script is present, and always
imports `ui/grammar.js` under Node to compare the page's rule grammar with the
Python one.

To refetch the snapshot (network) and rebuild the harness cases from it:

```bash
uv run python src/plugin/tools_dev/fetch_snapshot.py
uv run python src/plugin/tools_dev/build_cases.py
```

The scripted cases in `tests/cases/` run the task as the hosted service does:
`gold_chunked` and `gold_dst` take the prescribed path (`get_daily` with all 30
cities, three `report_alerts` calls of 10 examined cities, the answer), `gold`
reports in one call, `batched_daily` reads the cities in two `get_daily` calls
after `list_cities`, `corrected` fixes an earlier call's wrong value and bogus
alert in a later call, `overlapping_calls` restates five cities in two calls,
`unexamined` stops after two of the three calls and is refused,
`stale_old_version` and `stale_previous_run` plant a leftover report that the
first call replaces, and `stale_no_calls` leaves an earlier run's report alone,
which the verifier refuses. The wrong cases each break the gold report in one
way.

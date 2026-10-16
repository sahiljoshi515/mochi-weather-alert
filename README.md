# Weather Alert

A `nanoagent.plugin/v1` plugin for NanoAgent 0.5, with the structure of the
reference plugin, [arxiv-source](https://github.com/zeyuyang8/arxiv-source).
Its task, `daily-alert`, asks an agent which of 30 world cities had, or will
have, a day that crossed a few weather thresholds: any date from 1940-01-01 to
about six months ahead, in the units the person chose. The agent must report
each city's values exactly. The data comes live from
[Open-Meteo](https://open-meteo.com):

- the ECMWF archive for the past;
- a published ECMWF forecast run for the next two weeks;
- ECMWF's seasonal outlook after that.

The answer key pins the very data the agent reads, and the verifier recomputes
every alert from it. The verifier checks 21 constraints, and any one that fails
fails the run.

## Tools

| Tool | Does | Code |
|---|---|---|
| `pin_alerts` | The answer key. It reads the clock once and applies the date rules (below) at that reading and 10 minutes before it. It pins the source they choose, or both when they differ: every city's local day with its raw readings, a sha256 of the data, and the alerts the rules give | `src/plugin/alerts.py`, `src/plugin/sources.py` |
| `get_daily` | Up to 30 cities' local days for a task, from the data the rules choose 10 minutes behind the clock (so it is always one the answer key pinned). Per city it returns the readings count, the source variables with a missing reading, and `values`: each variable the conditions use, in the task's units, exact to 9 decimals (truncated, never rounded). It writes a receipt of the data it read to `reports/<date>.source.json` | `src/plugin/alerts.py` |
| `report_alerts` | Merges one call's judgement of up to 30 `examined` cities into `reports/<date>.json`. A city the call examines is restated by it, and every other city's entries are kept. It returns the merged report so far, naming the data it judges, with the number of cities examined and whether that is every city | `src/plugin/alerts.py` |
| `check_date` | Today's UTC date from the clock, and which data a daily-alert task on a date would use, with each kind's date range. It reads no network | `src/plugin/alerts.py` |

### Tasks

| Task | Does | Runner |
|---|---|---|
| `daily-alert` | Finds every city whose local day on the date crossed the thresholds and submits each one's values in the requested units, with the data they come from | `agent`, with `get_daily` and `report_alerts`; reference `pin_alerts` |
| `check-date` | Tells today's UTC date and which data a date would use. The chat model is never told the date, so it calls this first when a person names a day relative to today ("tomorrow") | `tool` (`check_date`) |

The tools give the data, and the agent does the judging:
- compare each value with its threshold exactly;
- round half up;
- apply `all` or `any`;
- keep cities with a missing reading out;
- report every city.

Unit conversion is the tool's job. A hosted model's reply, reasoning included, is capped at about 2,000 tokens, and converting 30 cities by hand did not fit.

### The task, step by step

Before the agent starts, NanoAgent runs `pin_alerts` with the task input, in a
scratch workspace the agent never sees, and pins its output. The agent then
has `get_daily`, `report_alerts` and `submit`, and six steps:

1. `get_daily` once, with the task input and all 30 cities.
2. `report_alerts` twice, fifteen cities each, in the order the instructions
   list them.
3. `submit` with the report the last call returned, as it is.
4. The final answer.

Fifteen cities per step keeps each reply inside the hosted model's
2,048-token cap. The four tool calls leave a step to spare. Each Open-Meteo
request covers all 30 cities and takes about a second, so a whole run fits the
site's 180-second limit.

## The data

Today (T) is the clock's UTC date. Every source is named; Open-Meteo's
`best_match` is never used, because it switches sources silently.

| Date | Data | Open-Meteo request |
|---|---|---|
| 1940-01-01 to 2017-01-01 | ERA5 reanalysis, hourly | `archive-api…/v1/archive?models=era5` |
| 2017-01-02 to T−2 | ECMWF IFS archive (analysis), hourly | `archive-api…/v1/archive?models=ecmwf_ifs` |
| T−1 to T+13 | One ECMWF IFS forecast run, hourly | `single-runs-api…/v1/forecast?models=ecmwf_ifs&run=…` |
| T+14 to the end of the 6th month ahead (the 5th before the 7th of a month) | ECMWF SEAS5 seasonal outlook, ensemble mean, 6-hourly | `seasonal-api…/v1/seasonal?models=ecmwf_seas5_ensemble_mean` |
| Earlier or later | Refused, with today's date and each range | |

How the sources and windows are chosen:

- **Which forecast run.** The run is the newest 00/06/12/18 UTC run that covers every city's whole local day and counts as published, 9 hours after its start. If Open-Meteo has not published it completely, the tool falls back to the next newest, at most three. The archive is final from T−2. Yesterday and today come from the runs issued before them, which are fixed by the date alone.
- **Why T+13.** No published run covers all 30 cities' local days further ahead: Honolulu's day ends at 10:00 UTC the next day, and a run reaches 360 hours. So days from T+14 come from the seasonal outlook.
- **The local day.** A local day runs from local midnight to local midnight in the city's IANA time zone, so it lasts 23 hours when clocks go forward and 25 when they go back. The zones come from the pinned `tzdata` package, so the host's own database never matters.
- **Which readings count.** Temperature, humidity and wind are readings at the stamps inside the day. Rain is a total over the step ending at each stamp, so the day's rain sums the stamps after its start through its end, as Open-Meteo's own `precipitation_sum` does. A stamp that is absent, or a null reading, makes its variable missing and the city unavailable. On 1940-01-01, for example, ERA5 has no hours from 1939.

How the answer key and the agent are kept on the same data:

- **Clocks.** They run in separate processes, seconds to minutes apart. The
  answer key applies the rules at two clock readings, now and 10 minutes
  earlier. The agent's tools use their own clock minus 10 minutes, which falls
  between the two as long as the agent reads within 10 minutes. The agent's
  tools fetch once per date per run.
- **Receipt.** `get_daily` records what it read in a receipt: the source, the
  clock, the request, every reading, and a sha256 of the data's canonical
  form. The verifier requires the receipt's source to be a pinned one and its
  sha256 to equal the pin's.
- **Upstream changes.** A forecast run never changes. If the archive or the
  outlook changed between the two fetches, the verdict is invalid and says
  "the data changed upstream; run again".
- **Clock skew.** A clock more than 5 minutes off Open-Meteo's is refused,
  because it would put a date in the wrong kind of data.

Weather data by [Open-Meteo.com](https://open-meteo.com), CC BY 4.0.

## Constraints

`verifier/task.py` judges the submitted report against 21 constraints, in
order. Each appears in the verdict's `details.constraints` as `{id,
constraint, ok}`, with `expected` and `actual` where they say something and a
plain `message` when it fails. A constraint whose prerequisite failed reads
`ok: null` ("not checked").

| # | id | Holds when |
|---|---|---|
| 1 | `answer-key` | The answer key pinned data for the task's date |
| 2 | `task-input` | The date is a calendar day from 1940-01-01, with 1 to 6 distinct well-formed conditions |
| 3 | `answer-key-data` | Each pinned source is well formed: every city's local day (it starts and ends at a local midnight, or up to two hours after one when clocks jump, 20 to 28 hours apart, at offsets near its solar time), sorted on-grid stamps, plausible readings, its sha256, and its own alerts re-derived from its readings |
| 4 | `answer-key-rules` | Each pinned source is the one the date rules choose at its clock reading, including any skipped runs |
| 5 | `run-marker` | The plugin marked this run in the workspace |
| 6 | `agent-read` | `get_daily` wrote this run's receipt for the date, and it follows the rules and matches its own hash |
| 7 | `agent-source` | The source the agent read is one the answer key pinned |
| 8 | `agent-data` | The agent's data is the pinned data, reading for reading (sha256) |
| 9 | `report-saved` | `report_alerts` saved this run's report for this task, on that data |
| 10 | `submitted-task` | The submitted report is for the task input |
| 11 | `submitted-shape` | The submitted report is well formed |
| 12 | `submitted-as-saved` | The submitted report equals the saved one |
| 13 | `names-data` | The submitted report names the data the agent read |
| 14 | `all-examined` | All 30 cities were examined |
| 15 | `alerts-complete` | Every city whose day meets the rules is reported as an alert |
| 16 | `no-extra-alerts` | No city is reported as an alert that is not one |
| 17 | `values-exact` | Every alert's values are exact: the task's units, rounded half up to 1 decimal |
| 18 | `matched-exact` | Every alert's matched conditions are exactly those that hold |
| 19 | `unavailable-complete` | Every city missing a reading a condition needs is reported unavailable |
| 20 | `no-extra-unavailable` | No city that has its readings is reported unavailable |
| 21 | `unavailable-exact` | Every unavailable city names exactly the variables it lacks |

How it is scored:

- **Valid** only when all 21 hold.
- **Score 0** when any of constraints 1 to 13 fails, because those guard the data and the report's integrity.
- **Otherwise** the score is the F1 of the reported alert and unavailable cities, scaled by the share of cities examined. So the score is 1.0 exactly when the verdict is valid.
- **Messages** name the case, e.g. "Extra alert Athens: Athens is no alert (no condition holds): max_temp 94.999000000 is not >= 95".

The verifier recomputes every alert from the pinned raw readings with exact
`Decimal`/`Fraction` arithmetic and integer calendar arithmetic (a verifier may
not import `datetime`). `verifier/rules.py` is a byte-for-byte copy of
`src/plugin/rules.py`, and a test enforces this.

Every tool call is checked too:

- **`verify_input`** refuses a malformed date or condition, an unknown or repeated city, and an alert whose values are not exactly the needed variables or whose matched conditions are not the task's.
- **`verify_output`** checks four things:
  - the answer key, by hand, as constraints 3 and 4 do;
  - that `get_daily`'s rows equal the rules applied to its receipt's readings;
  - that a report call's file and returned report name the data read and hold that call's judgement;
  - that `check_date`'s answer follows from its clock reading.

## Limitations

- **Model data, not station observations.** These are reanalysis, analysis and forecast values on Open-Meteo's grid; the result card names which.
- **Forecast reach.** The forecast covers T−1 to T+13, and later days use the seasonal outlook. That outlook gives a 6-hourly ensemble mean, so its highs and lows understate extremes and rain is spread out.
- **Yesterday and today** come from the forecast runs issued before them. The archive becomes final at T−2.
- **The 2017 seam.** ERA5 (to 2017-01-01) and the IFS archive (from 2017-01-02) can differ by a few degrees at the same place.
- **"Today" is UTC.** In the evening in the Americas that is already tomorrow locally; `check-date` says which day it is.
- **Run again.** A run fails, with "run again", if the archive or outlook changes between the answer key's fetch and the agent's, if the agent reads more than 10 minutes after the answer key, or if a forecast run is published late inside that window.
- **Free-tier limits.** Open-Meteo's free tier allows about 10,000 calls a day per address. A run makes at most 90 (each city in a request counts), so about 110 runs a day.

## Layout

```text
.
├── nanoagent.yaml   # manifest: id, runtime, permissions (Open-Meteo's three hosts), tools, tasks
├── contract.py      # each tool's and task's typed input and output: slimconfig Output classes
├── README.md
├── LICENSE
├── pyproject.toml   # tzdata is pinned here: the zones every local day is cut by
├── uv.lock
├── .gitignore
├── .env             # needed environment variable names (none here)
├── run.sh           # checks this environment can run the plugin
├── src/plugin/      # the MCP server (__main__.py), the tools (alerts.py), the date rules and checks
│                    #   (rules.py), Open-Meteo (openmeteo.py), the sources (sources.py), local days (days.py),
│                    #   and tools_dev/ (fixtures, cases and the live date matrix)
├── tests/           # offline tests (recorded Open-Meteo answers in tests/fixtures/), an independent oracle,
│                    #   and scripted cases under tests/cases/ for NanoAgent's task runner
├── ui/              # how the page draws the outputs: renderers.js and renderers.css
├── product/         # the product page a catalog shows: product.yaml and icon.svg
└── verifier/        # deterministic verifier: one module per tool, the task's 21 constraints in task.py
```

## Develop and run

```bash
uv sync --extra dev
uv run pytest                          # offline: recorded answers, frozen clocks
NANOAGENT_NETWORK=1 uv run pytest      # also the live tests against Open-Meteo
uv run ruff check .
```

NanoAgent passes the folder the plugin may write to as `NANOAGENT_WORKSPACE`.
The report goes to `reports/<date>.json`, the receipt to
`reports/<date>.source.json`, and every tool call leaves one short line in
`reports/calls.log`, so a run that went wrong can be read back from the
member's folder.

### Reproduce the checks without a model

The scripted cases under `tests/cases/` run the task exactly as the host does,
with a scripted agent instead of a model and no LLM anywhere. Run them with
NanoAgent's task runner:

```bash
python ~/mochi-dev/harness/scripted_task.py . tests/cases/gold.json --sandbox seatbelt
uv run python src/plugin/tools_dev/run_matrix.py      # live dates relative to today, gold and wrong reports
```

`src/plugin/tools_dev/record_fixtures.py` records the Open-Meteo answers the
offline tests replay. `src/plugin/tools_dev/build_cases.py` rebuilds the
scripted cases.

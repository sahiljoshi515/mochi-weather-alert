# Weather Alert

A `nanoagent.plugin/v1` plugin for NanoAgent 0.5, with the structure of the
reference plugin, [arxiv-source](https://github.com/zeyuyang8/arxiv-source).
Its task, `daily-alert`, gives an agent a date and 1 to 6 weather thresholds,
such as `max_temp >= 95` or `rain > 0.5`. The agent must find which of 30 world
cities crossed them on that local day and report each one's values exactly, in
the units asked for. Any date from 1940-01-01 to about six months ahead works:
the past from the archive, the next two weeks from a forecast run, later days
from the seasonal outlook, all live from [Open-Meteo](https://open-meteo.com).
The answer key pins the very data the agent reads, and a rule-based verifier
recomputes every alert from it: 21 constraints, every one of which must hold.

## Tools

| Tool | Does | Code |
|---|---|---|
| `pin_alerts` | The answer key. It reads the clock once and applies the date rules at that reading and 10 minutes before it, pinning the source each chooses (one, or two when they differ): every city's local day with its raw readings, a sha256 of the data, the alerts the rules give, and the runs already in the workspace | `src/plugin/alerts.py`, `src/plugin/sources.py` |
| `get_daily` | Up to 30 cities' local days for a task, from the data the rules choose 10 minutes behind the clock (always data the answer key pinned). Per city: the readings count, the source variables with a missing reading, and `values`, each variable the conditions use in the task's units, exact to 9 decimals (truncated, never rounded). It writes a receipt of the data it read to `reports/<run>/<date>.source.json` | `src/plugin/alerts.py` |
| `report_alerts` | Merges one call's judgement of up to 30 `examined` cities into `reports/<run>/<date>.json` (a city it examines is restated by it; every other city's entries are kept) and returns the merged report, its run and data, how many cities are examined and whether that is all 30 | `src/plugin/alerts.py` |
| `check_date` | Today's UTC date from the clock, and which data a daily-alert task on a date would use, with each kind's date range. It reads no network | `src/plugin/alerts.py` |

### Tasks

| Task | Does | Runner |
|---|---|---|
| `daily-alert` | Finds every city whose local day on the date crossed the thresholds and submits each one's values in the requested units, with the data they come from | `agent`, with `get_daily` and `report_alerts`; reference `pin_alerts` |
| `check-date` | Tells today's UTC date and which data a date would use, refusing a date exactly when a daily-alert task on it would be refused. The chat model is never told the date, so it calls this first for a day relative to today ("tomorrow") | `tool` (`check_date`) |

## The task

Before the agent starts, NanoAgent runs `pin_alerts` with the task input in a
scratch workspace the agent never sees, and pins its output. The agent then has
`get_daily`, `report_alerts` and `submit`, and six steps:

1. `get_daily` once, with the task input and all 30 cities.
2. `report_alerts` twice, fifteen cities each, in the order the instructions list them.
3. `submit` with the report the last call returned, as it is.
4. The final answer.

The tools give the data and convert units; the agent does the judging:
compare each value with its threshold exactly; with `all`, a city is an alert
when every condition holds, with `any` when at least one does; report each value rounded
to 1 decimal, ties away from zero (2.25 → 2.3, −2.25 → −2.3); put a city with a
missing reading under `unavailable`, never as an alert; and examine every city.
Converting units is the tool's job because the hosted model's reply, reasoning
included, is capped at 2,048 tokens: converting 30 cities by hand did not fit,
and fifteen cities per step does. The four tool calls leave a step to spare,
and each Open-Meteo request (all 30 cities at once) takes about a second, so a
run fits the site's 180-second limit.

## Ground truth

The ground truth is the pinned Open-Meteo data, never a stored answer. The
verifier re-derives every city's local day, value and alert from the pinned
raw readings with exact `Decimal`/`Fraction` arithmetic and integer calendar
arithmetic (a verifier may not import `datetime`), and checks that the pinned
alerts agree. T is the clock's UTC date. Every source is named; Open-Meteo's
`best_match` is never used, because it switches sources silently.

| Date | Data | Open-Meteo request |
|---|---|---|
| 1940-01-01 to 2017-01-01 | ERA5 reanalysis, hourly | `archive-api…/v1/archive?models=era5` |
| 2017-01-02 to T−2 | ECMWF IFS archive (analysis), hourly | `archive-api…/v1/archive?models=ecmwf_ifs` |
| T−1 to T+13 | One ECMWF IFS forecast run, hourly | `single-runs-api…/v1/forecast?models=ecmwf_ifs&run=…` |
| T+14 to the end of the 6th month ahead (the 5th before the 7th of a month) | ECMWF SEAS5 seasonal outlook, ensemble mean, 6-hourly | `seasonal-api…/v1/seasonal?models=ecmwf_seas5_ensemble_mean` |
| Earlier or later | Refused, with today's date and each range | |

- **Which forecast run.** The newest 00/06/12/18 UTC run that counts as published (9 hours after its start), starts by (D−1) 10:00 UTC and reaches (D+1) 11:00 UTC (00 and 12 UTC runs reach 360 hours, 06 and 18 UTC runs 144). That fixed window bounds every city's day in every season, from Auckland's start at (D−1) 11:00 UTC (+13) to Honolulu's end at (D+1) 10:00 UTC (−10), with an hour to spare at the start because a run's first hour has no precipitation reading. If Open-Meteo has not published a run completely, the tool falls back to the next newest, at most three. No published run covers every city's day past T+13, so later days come from the outlook.
- **The local day** runs from local midnight to local midnight in the city's IANA zone: usually 24 hourly readings, 23 or 25 when clocks change, and 22 to 26 on some 1940s days (Singapore 1942-02-16, Jakarta 1942-03-23). When clocks jump at midnight, the day starts at the jump. The zones come from the pinned `tzdata` package, never the host's database.
- **Which readings count.** Temperature, humidity and wind are readings at the stamps inside the day. Rain is a total over the step ending at each stamp, so the day's rain sums the stamps after its start through its end, as Open-Meteo's `precipitation_sum` does. An absent stamp or a null reading makes the variable missing and the city unavailable (on 1940-01-01 ERA5 has no 1939 hours, so cities east of UTC are).
- **One clock, two readings.** The answer key and the agent run in separate processes, seconds to minutes apart. The answer key applies the rules now and 10 minutes earlier and accepts a date only when both allow it; the agent's tools read their own clock minus 10 minutes, which falls between the two while the agent reads within 10 minutes. A date that came into range in the last 10 minutes (the outlook reaches a month further on the 7th) is refused with the first time it works, and `check-date` answers the same.
- **Runs.** Each task call's agent is one run with its own folder, `reports/<run>/`: its marker (`run.json`: the run's token and the UTC second it began), its receipt, its report and its call log. Two task calls at once in one workspace never touch each other's files, and the submitted report names its run. A report an earlier task left never passes: the answer key records the runs already in the workspace, and a run's marker may not be older than the answer key's last fetch.
- **The receipt.** `get_daily` records what it read: the source, the clock, when Open-Meteo answered, the request, every reading, and their sha256. The verifier checks every field before any arithmetic, re-derives the request from the rules, and requires the readings to match the receipt's own hash and the pin's.
- **Upstream changes.** A forecast run never changes and an archived day is final, so receipt data that differs from the pinned data of the same source was altered: invalid, with no rerun. Only the seasonal outlook can be updated between the two fetches; that verdict says "run again" and sets `details.rerun`.
- **Clock skew.** A computer clock more than 5 minutes off Open-Meteo's is refused, since it would put a date in the wrong kind of data.

Weather data by [Open-Meteo.com](https://open-meteo.com), CC BY 4.0.

## Constraints

`verifier/task.py` judges the submitted report against 21 constraints, in
order. Every entry of the verdict's `details.constraints` has the same keys:
`{id, constraint, ok, expected, actual, message}`. `ok` is `true` (holds),
`false` (fails) or `null` (not checked, because a check it needs did not hold).
`expected` and `actual` are set where they say something (for the alert checks,
only the cities concerned) and are `null` otherwise. `message` says why a check
failed or what it waited on (`"Not checked: agent-read did not hold."`), and is
`""` when it holds. `<run>` below stands for the run's 16-hex-digit token.

| # | id | What it checks | Example failure message |
|---|---|---|---|
| 1 | `answer-key` | The answer key pinned data for this date | No answer key for 2026-07-07: pin_alerts never fetched the day. |
| 2 | `task-input` | The task is a valid date with 1 to 6 conditions | 2026-02-30 is not a calendar day |
| 3 | `answer-key-data` | The answer key's data is well formed: each city's local day, its readings and their hash | The pinned ECMWF IFS archive (analysis), hourly does not match its own sha256 |
| 4 | `answer-key-rules` | The answer key used the data the date rules choose | The pinned ECMWF IFS archive (analysis), hourly breaks the date rules: at 2026-07-06T00:00Z the rules choose ECMWF IFS forecast issued 2026-07-05 12:00 UTC, hourly, not ECMWF IFS archive (analysis), hourly |
| 5 | `this-run` | The report names this task's own run, begun after the answer key | Run a1b2c3d4e5f60718 began before this task (it was in the workspace when the answer key was pinned): submit the report this task's report_alerts returned |
| 6 | `agent-read` | The agent read the date's data with get_daily | The receipt reports/\<run\>/2026-07-07.source.json was altered after get_daily: its readings no longer match its own sha256 |
| 7 | `agent-source` | The agent read a source the answer key pinned | The agent read the ECMWF IFS forecast issued 2026-10-15 12:00 UTC, hourly; the answer key pinned the ECMWF IFS forecast issued 2026-10-15 00:00 UTC, hourly and the ECMWF IFS forecast issued 2026-10-15 06:00 UTC, hourly. The agent read 365 min after the answer key, more than the 10 min allowed: run again. |
| 8 | `agent-data` | The agent's readings equal the answer key's | The data the agent read differs from the pinned data of the same request (Athens temperature_2m at 2026-10-30T06:00Z is 14.3 in the pinned data and 14.8 in the receipt). The ECMWF SEAS5 seasonal outlook (ensemble mean), 6-hourly can be updated upstream between two fetches: run again. |
| 9 | `report-saved` | report_alerts saved the run's report for this task and data | No report at reports/\<run\>/2026-07-07.json: call report_alerts for every city before submitting |
| 10 | `submitted-task` | The submitted report is for this task | The submitted report is for another task: match 'all' instead of 'any'. Submit the report the last report_alerts call returned, as it is. |
| 11 | `submitted-shape` | The submitted report is well formed | The submitted report is malformed: 'Atlantis' is not one of the 30 cities (names are spelled as the task lists them) |
| 12 | `submitted-as-saved` | The submitted report equals the saved report | The submitted report is not the saved report reports/\<run\>/2026-07-07.json: it leaves out the saved alert for Auckland. Submit the report the last report_alerts call returned, as it is. |
| 13 | `names-data` | The submitted report names the data the agent read | The submitted report names other data than the agent read: model 'era5' instead of 'ecmwf_ifs'; label 'ERA5 reanalysis (ECMWF), hourly' instead of 'ECMWF IFS archive (analysis), hourly'. |
| 14 | `all-examined` | All 30 cities were examined | 15 of the 30 cities were never examined (Mexico City, Moscow, Mumbai and 12 more): report_alerts must judge every city. |
| 15 | `alerts-complete` | Every city that meets the rules is reported as an alert | Athens is an alert but is not reported: max_temp 33.3 meets max_temp >= 33.3, so report it with max_temp 33.3. |
| 16 | `no-extra-alerts` | No city is reported as an alert without meeting the rules | Athens is reported as an alert but is not one: max_temp 91.94 is not >= 95; humidity 34.291666666 is not >= 85; rain 0 is not > 0.5; wind 8.202099737 is not >= 20 (with match any, at least one condition must hold). |
| 17 | `values-exact` | Each alert's values are right: the task's units, 1 decimal, ties away from zero | Perth humidity is reported as 83.2, but get_daily gave 83.25, which is 83.3 to 1 decimal (ties away from zero). Also wrong: Toronto. |
| 18 | `matched-exact` | Each alert lists exactly the conditions that hold | Mumbai's matched conditions are wrong: rain > 0.5 holds (rain 2.181102362) but is not listed. |
| 19 | `unavailable-complete` | Every city missing a needed reading is reported unavailable | Athens must be reported unavailable (it lacks max_temp, rain): it has no temperature_2m reading at 1939-12-31T22:00Z. |
| 20 | `no-extra-unavailable` | No city with all its needed readings is reported unavailable | Athens is reported unavailable but has every reading of max_temp, rain, wind, humidity. |
| 21 | `unavailable-exact` | Each unavailable city names exactly the variables it lacks | Athens lacks max_temp, rain, but the report lists max_temp. |

How it is scored:

- **Valid** only when all 21 hold, and then the score is 1.0.
- **Score 0** when any of 1 to 13 fails: they guard the data and the report's integrity.
- **Otherwise** the score is the F1 of the reported alert and unavailable cities (an entry counts only when it is exactly right), scaled by the share of cities examined, and at most 0.99.
- **The message** names the first failing constraint, why it fails, and how many more fail, e.g. `all-examined: 15 of the 30 cities were never examined (Mexico City, Moscow, Mumbai and 12 more): report_alerts must judge every city (+1 more)`. A valid verdict says `All 21 checks hold: 12 of the 30 cities are alerts and 0 are unavailable, judged on the ECMWF IFS archive (analysis), hourly.`
- **`details`** also holds `rerun` (true when running again can pass), `source` and `today` (the data judged), `expected` (the answer key's alert and unavailable cities), `f1`, and, when the agent's data differs from the pin's, `agent_data_check` (what the report is worth on the agent's own data).

A value in a message is shown as `get_daily` gives it, beside its rounded report. Rounding ties away from zero is symmetric, so rounding `get_daily`'s value (truncated toward zero at 9 decimals) gives the same report as rounding the exact value, negatives included, and with 9 decimals the truncation never changes a comparison with a threshold of at most 2 decimals. That rests on a bound the verifier enforces: every reading has at most 2 decimals (Open-Meteo gives 1, 0 for humidity, and 2 for a forecast run's precipitation, such as 0.03 mm), so a converted value differs from such a threshold by 0 or by at least 6×10⁻⁹. Data with more decimals is refused, by the answer key or as an altered receipt.

The `check-date` task has its own verdict (`verifier/dates.py`), re-derived from the clock reading its answer carries: `clock` (the answer carries a UTC clock reading), `today`, `date` (with its weekday), `ranges` (each kind of data's date range), `kind` (the data the date uses, refused in a date's first 10 minutes in range) and `message`. A failure reads, e.g., `ranges: The answer says forecast_last '2026-10-31', expected '2026-10-29'`.

Every tool call is checked as well. `verify_input` refuses a malformed date or condition, an unknown or repeated city, and an alert whose values are not exactly the needed variables or whose matched conditions are not the task's. `verify_output` re-checks the answer key as constraints 3 and 4 do, holds `get_daily`'s rows to the rules applied to its receipt, holds each report call's file and returned report to that call's judgement, and re-derives `check_date`'s answer from its clock. `verifier/rules.py` is a byte-for-byte copy of `src/plugin/rules.py`, and a test enforces it.

## Limitations

- **Model data, not station observations.** Reanalysis, analysis and forecast values on Open-Meteo's grid; the result card names which.
- **The outlook understates extremes.** It is a 6-hourly ensemble mean, so its highs and lows are damped and its rain is spread out.
- **The 2017 seam.** ERA5 (to 2017-01-01) and the IFS archive (from 2017-01-02) can differ by a few degrees at the same place.
- **"Today" is UTC.** In the evening in the Americas that is already tomorrow locally; `check-date` says which day it is.
- **Run again.** A run fails with "run again" and `details.rerun` if the seasonal outlook changes between the two fetches, if the agent reads more than 10 minutes after the answer key, or if a forecast run is published, or not yet complete for the agent, in between. Were Open-Meteo to revise an archived day within those minutes, the verdict would call the receipt altered.
- **Two identical task calls at once.** Each run is judged on its own files, but the verdict cannot tell which of two concurrent runs served which call: a call that submitted the other's report, for the same task input, would pass. That needs the other run's token, which no agent is shown, and the report is right either way. A host-given task-call id would close it.
- **Pruning can fail a concurrent call.** NanoAgent's web host stages a task call by copying the whole workspace, and the copy fails if a file vanishes meanwhile. So old run folders are pruned rarely: only when more than 200 exist, only those over 24 hours old, at most once per process, each moved out of the workspace before it is deleted. Where `TMPDIR` is on another volume than the workspace (bwrap's `/tmp`), a write's temporary file sits beside its target and can collide the same way (the call log notes `temp_beside`). Only the host can close this, by tolerating files that vanish during its copy.
- **Free-tier limits.** Open-Meteo's free tier allows about 10,000 location-calls a day per address (each city in a request counts as one). A run usually makes 60 (one source) or 90 (two). When forecast runs are not yet published, the answer key's two readings and the agent's read each try up to three runs (270 location-calls), and each request is tried up to 3 times on 429 or 5xx (810 at worst): about 110 to 160 typical runs a day.

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
│                    #   a fuzz of a run's files (test_tamper.py), and scripted cases under tests/cases/ for
│                    #   NanoAgent's task runner
├── ui/              # how the page draws the outputs and the verdict's checks: renderers.js and renderers.css
├── product/         # the product page a catalog shows: product.yaml and icon.svg
└── verifier/        # deterministic verifier: one module per tool, the task's 21 constraints in task.py
```

## Develop and run

```bash
uv sync --extra dev
uv run pytest                          # offline: recorded Open-Meteo answers, frozen clock
NANOAGENT_NETWORK=1 uv run pytest      # also the live tests against Open-Meteo
uv run ruff check .
```

NanoAgent passes the folder the plugin may write to as `NANOAGENT_WORKSPACE`.
Each run writes its own folder, `reports/<run>/`, with the report
(`<date>.json`), the receipt (`<date>.source.json`) and one line per tool call
(`calls.log`), so a run that went wrong can be read back from the member's
folder. `src/plugin/tools_dev/record_fixtures.py` records the Open-Meteo
answers the offline tests replay.

### Reproduce the verification without an LLM

Each case under `tests/cases/` scripts the agent: `get_daily`, a python step
that judges its values from the task's instructions alone
(`tests/scripted_judge.py`), two `report_alerts` calls and `submit`, or the
same run broken in one way. Each states the verdict it expects (`expect`):
gold cases hold all 21 constraints, every other case fails a named one. No
model runs anywhere.

Offline, through the real tools and verifier, with the recorded Open-Meteo
answers and the clock frozen at 2026-10-16 04:00 UTC:

```bash
uv run pytest tests/test_cases.py -v
uv run python src/plugin/tools_dev/build_cases.py   # rebuilds tests/cases/, checking each verdict first
```

Live, through NanoAgent's own task runner (the course harness in
`~/mochi-dev/harness`, run with the Python NanoAgent 0.5 is installed in). Each
run writes its `verdict.json` under `~/mochi-dev/runs/weather-alert/`:

```bash
NANOAGENT_PYTHON=~/mochi-dev/rushlab/nanoagent/.venv/bin/python
$NANOAGENT_PYTHON ~/mochi-dev/harness/scripted_task.py . tests/cases/gold.json --sandbox seatbelt   # 60 location-calls
$NANOAGENT_PYTHON ~/mochi-dev/harness/run_cases.py . tests/cases --jobs 2   # all 32 cases: about 1,650 location-calls
uv run python src/plugin/tools_dev/run_matrix.py   # gold and broken runs on dates relative to today
```

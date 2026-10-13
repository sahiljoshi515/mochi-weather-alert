// The weather-alert page: pick a day, your units and a few threshold rules, find the cities whose
// day crossed them, and see the checked list. The chat with the plugin's agent lives in a drawer.

import { announce, appShell, banner, card, chipRow, chipsInput, dateInput, errorCard, explainError, h, howItWorks, icon, listFiles, parseToolOutput, readJSON, runButton, segmented, skeleton, taskRun, writeJSON } from "./kit/components.js";
import { ERROR_RULES, GRAMMAR, MAX_CONDITIONS, checkSavedResult, conditionProblem, dayProblem, failedCallNote, formatDay, resultCard, resultFromRun, savedChipLabel } from "./rules.js";
import { SNAPSHOT } from "./snapshot.js";

const TASK = "daily-alert";
const SAVED = "weather-alert-checks/";
const RECENT = 8;

const STEPS = [
  { id: "pin", label: "Preparing the answer key" },
  { id: "read", label: "Agent reading the weather" },
  { id: "apply", label: "Agent applying the rules" },
  { id: "check", label: "Checking the list" },
];

// Ready-made rule sets, each with the units its numbers are in.
const PRESETS = [
  { label: "Hot day", match: "any", units: { temperature: "F", wind: "mph", rain: "in" }, rules: ["max_temp >= 95"] },
  { label: "Wet and windy", match: "all", units: { temperature: "C", wind: "kmh", rain: "mm" }, rules: ["rain >= 10", "wind >= 40"] },
  { label: "Cold night", match: "any", units: { temperature: "C", wind: "ms", rain: "mm" }, rules: ["min_temp <= 5"] },
  { label: "Muggy", match: "all", units: { temperature: "C", wind: "kmh", rain: "mm" }, rules: ["humidity >= 80", "max_temp >= 28"] },
];

export default async function page({ plugin }) {
  // These cannot be recovered from on the page, and the message reaches a reader, so it says what
  // is wrong without naming the task or its arguments.
  const task = plugin.tasks.find((one) => one.id === TASK);
  if (!task) throw new Error("This version of Weather Alert no longer offers the daily check this page is built around.");
  const fields = task.inputSchema?.properties ?? {};
  if (!fields.date || !fields.conditions || !fields.match) throw new Error("This version of Weather Alert no longer takes a day and rules, which this page asks for.");

  let shown = null; // the result the result card shows
  let progress = null; // what the current run has told the page so far

  // ----- Chat -----

  const suggestionsFor = (date) => [
    `Which city had the hottest day on ${formatDay(date, { weekday: false })}?`,
    `How much rain fell in Mumbai on ${formatDay(date, { weekday: false })}?`,
    "Which cities are in the data?",
  ];
  const shell = appShell({
    plugin,
    chat: {
      intro: h("div", { class: "chat-intro small" }, h("img", { src: plugin.product.icon, alt: "" }), h("p", { class: "muted", text: "Ask about the weather in one of the 30 cities on a day in the data." })),
      note: "The agent reads the pinned data. Answers here are not checked; the list on the page is.",
      placeholder: "Ask about a city or a day",
      suggestions: suggestionsFor(SNAPSHOT.end),
      prepare: (text) => (shown ? `(I'm looking at ${formatDay(shown.date, { weekday: false })}.)\n\n${text}` : text),
    },
  });

  // ----- The request -----

  const shortDayChips = SNAPSHOT.shortDays.map((day) => ({ label: `${day.city} clocks forward · ${formatDay(day.date, { weekday: false, year: false })}`, value: day.date }));
  const picker = dateInput({
    label: "Day",
    value: SNAPSHOT.end,
    min: SNAPSHOT.start,
    max: SNAPSHOT.end,
    chips: [{ label: "First day", value: SNAPSHOT.start }, { label: "Last day", value: SNAPSHOT.end }, ...shortDayChips],
    caption: "Each city's own calendar day, in its time zone.",
    check: (value) => dayProblem(value, SNAPSHOT),
    onChange: (value) => {
      shell.drawer.chat.setSuggestions(suggestionsFor(picker.valid() ? value : SNAPSHOT.end));
      refreshButton();
    },
  });

  const match = segmented({ label: "Match", showLabel: true, options: [{ value: "all", label: "All rules" }, { value: "any", label: "Any rule" }] });
  const temperature = segmented({ label: "Temperature unit", showLabel: true, options: [{ value: "C", label: "°C" }, { value: "F", label: "°F" }] });
  const wind = segmented({ label: "Wind unit", showLabel: true, options: [{ value: "kmh", label: "km/h" }, { value: "mph", label: "mph" }, { value: "ms", label: "m/s" }] });
  const rain = segmented({ label: "Rain unit", showLabel: true, options: [{ value: "mm", label: "mm" }, { value: "in", label: "in" }] });
  const rules = chipsInput({
    label: "Rules",
    values: ["max_temp >= 30"],
    placeholder: "Add a rule, like rain > 5",
    hint: `${GRAMMAR} Numbers are in the units chosen above; humidity is a percentage. Up to ${MAX_CONDITIONS} rules.`,
    validate: (value, values) => conditionProblem(value, values),
    onChange: () => refreshButton(),
  });
  const presets = chipRow({
    label: "Ready-made rules",
    items: PRESETS.map((preset) => ({ label: preset.label, value: preset.label, title: preset.rules.join(", ") })),
    onPick: (item) => {
      const preset = PRESETS.find((one) => one.label === item.value);
      rules.set(preset.rules);
      match.set(preset.match);
      temperature.set(preset.units.temperature);
      wind.set(preset.units.wind);
      rain.set(preset.units.rain);
      refreshButton();
    },
  });

  const recent = chipRow({ label: "Your recent checks", items: [], scroll: true, onPick: (item) => showSaved(item.value) });
  const recentRow = h("div", { class: "recent", hidden: true }, h("span", { class: "recent-label", text: "Your recent checks" }), recent.element);
  const recentProblem = h("p", { class: "small-text error-text", hidden: true });

  const run = taskRun({ task: TASK, title: "Finding the alerts", steps: STEPS, onEvent: track, onFinish: finished });
  run.element.tabIndex = -1; // so starting a run can put the keyboard in the progress card (`start`)
  const go = runButton({
    label: "Find the alerts",
    run,
    block: true,
    onClick: () => start(request()),
    note: "Takes a minute or two: the agent reads every city's day and applies the rules, then the list is checked. Keep this page open.",
  });
  const refreshButton = () => go.setBlocked(!picker.valid() ? "Pick a day in the data to start." : rules.values().length ? null : "Add at least one rule to start.");
  run.subscribe((busy) => {
    picker.setDisabled(busy);
    rules.setDisabled(busy);
    presets.setDisabled(busy);
    recent.setDisabled(busy);
    for (const control of [match, temperature, wind, rain]) control.setDisabled(busy);
  });

  const request = () => ({
    date: picker.value(),
    match: match.value(),
    temperature_unit: temperature.value(),
    wind_unit: wind.value(),
    rain_unit: rain.value(),
    conditions: rules.values(),
  });
  const apply = (saved) => {
    picker.set(saved.date);
    match.set(saved.match);
    temperature.set(saved.units.temperature);
    wind.set(saved.units.wind);
    rain.set(saved.units.rain);
    rules.set(saved.conditions);
    refreshButton();
  };

  const pickerCard = card(
    { title: "Pick a day and your rules", className: "picker-card" },
    h("div", { class: "picker-grid" }, picker.element, match.element),
    h("div", { class: "units-row" }, temperature.element, wind.element, rain.element),
    rules.element,
    h("div", { class: "presets" }, h("span", { class: "recent-label", text: "Ready-made rules" }), presets.element),
    go.element,
    recentRow,
    recentProblem,
  );

  // ----- Results -----

  // Not a live region: a whole result card is far too much for a screen reader to read out. Each
  // outcome announces one sentence instead, below.
  const result = h("div", { class: "result-area" });
  const intro = card({ className: "intro-card" }, h("p", { class: "intro-summary", text: plugin.product.summary }), howItWorks(plugin.product.steps));

  function show(...nodes) {
    intro.hidden = true;
    result.classList.remove("stale");
    result.replaceChildren(...nodes);
  }

  function start(args) {
    if (run.busy) return;
    picker.set(args.date);
    if (!picker.valid() || !args.conditions.length) return;
    progress = { request: args, cities: null, read: 0 };
    if (shown && result.firstChild) result.classList.add("stale");
    else show(h("div", { class: "card large" }, skeleton({ lines: 3, block: true })));
    recent.select(null);
    run.start(args);
    // Starting disables "Find the alerts" and replaces a "Run again" button, either of which drops
    // the keyboard to the top of the page; the progress card is where the run now is.
    run.element.focus({ preventScroll: true });
    run.element.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }

  function track(name, data, controller) {
    switch (name) {
      case "task.started":
      case "task.reference":
        controller.step("pin", { detail: "Every city's day, recomputed from the pinned data so the agent's list can be checked.", progress: "indeterminate" });
        break;
      case "task.tool":
        trackTool(data, controller);
        break;
      case "task.answer":
        controller.step("check");
        break;
    }
  }

  function trackTool(tool, controller) {
    const parsed = parseToolOutput(tool.output);
    if (tool.isError || !parsed || parsed.error) {
      controller.step("read");
      controller.note(failedCallNote(tool));
      return;
    }
    if (tool.name === "list_cities") {
      progress.cities = parsed.cities.length;
      controller.step("read", { detail: `${parsed.cities.length} cities to read`, progress: 0 });
    } else if (tool.name === "get_daily") {
      progress.read += parsed.cities.length;
      const total = progress.cities ?? SNAPSHOT.cities.length;
      controller.step("read", { detail: `${Math.min(progress.read, total)} of ${total} cities read`, progress: progress.read / total });
    } else if (tool.name === "get_hourly") {
      controller.step("read", { detail: `Looking closer at ${parsed.city}` });
    } else if (tool.name === "report_alerts") {
      const unavailable = parsed.unavailable ? `, ${parsed.unavailable} unavailable` : "";
      controller.step("apply", { detail: `Reported ${parsed.alerts === 0 ? "no alerts" : `${parsed.alerts} alert${parsed.alerts === 1 ? "" : "s"}`}${unavailable}` });
    }
  }

  async function finished(outcome) {
    const { request: args } = progress;
    // `details.expected` is the list the result card shows (the verifier's own recomputation), so a
    // verdict without it is shown as a problem rather than drawn from the agent's numbers.
    if (outcome.verdict?.details?.expected) {
      const found = resultFromRun(args, outcome);
      shown = found;
      show(resultCard(found, SNAPSHOT));
      announce(result.firstChild.getAttribute("aria-label"));
      try {
        await writeJSON(`${SAVED}${found.ran_at.replace(/[-:.]/g, "").slice(0, 15)}.json`, found);
        await loadRecent(found.ran_at);
      } catch (problem) {
        result.append(banner({ tone: "warn", title: "This check couldn't be saved to your recent checks.", body: problem.message }));
      }
      return;
    }
    shown = null;
    const message = outcome.error ?? (outcome.verdict ? `the check returned no list: ${outcome.verdict.message}` : "The run ended without checking an answer.");
    const actions = [h("button", { type: "button", class: "button small", onclick: () => start(args) }, icon("refresh", { size: 15 }), "Run again")];
    show(errorCard({ message, rules: ERROR_RULES, title: outcome.startFailed ? "Couldn't start" : undefined, actions }));
    announce(explainError(message, ERROR_RULES).text);
  }

  // ----- Recent checks (one file per run in the workspace) -----

  let saved = new Map();
  async function loadRecent(selected = null) {
    const files = (await listFiles({ prefix: SAVED, extensions: [".json"] })).reverse().slice(0, RECENT);
    const results = [];
    const unreadable = [];
    for (const file of files) {
      try {
        results.push(checkSavedResult(await readJSON(file.path), file.path));
      } catch (problem) {
        unreadable.push(problem.message);
      }
    }
    saved = new Map(results.map((one) => [one.ran_at, one]));
    recent.setItems(results.map((one) => ({ label: savedChipLabel(one), value: one.ran_at, tone: one.verdict.valid ? "ok" : "warn", title: `${formatDay(one.date)}: ${one.conditions.join(", ")}`, selected: one.ran_at === selected })));
    recentRow.hidden = results.length === 0;
    recentProblem.hidden = unreadable.length === 0;
    recentProblem.textContent = unreadable.length ? `Some saved checks couldn't be read: ${unreadable.join(" ")}` : "";
  }

  function showSaved(ranAt) {
    const found = saved.get(ranAt);
    if (!found) return;
    shown = found;
    recent.select(ranAt);
    apply(found);
    run.hide();
    const again = h("button", { type: "button", class: "button small", onclick: () => start(request()) }, icon("refresh", { size: 15 }), "Run again");
    show(resultCard(found, SNAPSHOT, { saved: true, actions: [again] }));
  }

  try {
    await loadRecent();
  } catch (problem) {
    recentProblem.hidden = false;
    recentProblem.textContent = `Your recent checks couldn't be listed: ${problem.message}`;
  }
  refreshButton();
  shell.main.append(pickerCard, run.element, result, intro);
  return shell.element;
}

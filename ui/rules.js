// The weather-alert page's own pieces: the condition grammar (the same as verifier/rules.py's, kept
// in sync by a test), the words for units and variables, the join of the agent's tool outputs with
// the verifier's expected list, the result card, the verdict wording and the error wording.

import { banner, dataTable, disclosure, h, icon } from "./kit/components.js";

export { GRAMMAR, MAX_CONDITIONS, OPS, VARIABLES, canonicalNumber, conditionProblem, parseCondition } from "./grammar.js";
import { GRAMMAR, MAX_CONDITIONS, VARIABLES, parseCondition } from "./grammar.js";

const VARIABLE_WORDS = { max_temp: "High", min_temp: "Low", rain: "Rain", wind: "Wind", humidity: "Humidity" };
const UNIT_WORDS = { C: "°C", F: "°F", kmh: "km/h", mph: "mph", ms: "m/s", mm: "mm", in: "in" };
const METRIC_UNITS = { temperature: "C", wind: "kmh", rain: "mm" };

// ----- Words -----

/** The unit a variable is given in under `units` ({temperature, wind, rain}). */
export function unitOf(variable, units) {
  if (variable === "max_temp" || variable === "min_temp") return UNIT_WORDS[units.temperature];
  if (variable === "wind") return UNIT_WORDS[units.wind];
  if (variable === "rain") return UNIT_WORDS[units.rain];
  return "%";
}

export const variableWord = (variable) => VARIABLE_WORDS[variable] ?? variable;
export const unitWord = (unit) => UNIT_WORDS[unit] ?? unit;

/** "High ≥ 95 °F" for "max_temp >= 95". */
export function ruleWords(text, units) {
  const parsed = parseCondition(text);
  if (!parsed) return text;
  const op = { ">=": "≥", ">": ">", "<=": "≤", "<": "<" }[parsed.op];
  return `${variableWord(parsed.variable)} ${op} ${parsed.number} ${unitOf(parsed.variable, units)}`;
}

/** A reported value: one decimal with a true minus sign. */
export function formatValue(value) {
  if (value === null || value === undefined) return "";
  const text = Math.abs(value).toFixed(1);
  return value < 0 ? `−${text}` : text;
}

/** "Sun, Sep 6, 2026"; `{weekday: false}` drops the day name, `{year: false}` the year. */
export function formatDay(iso, { weekday = true, year = true } = {}) {
  return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: weekday ? "short" : undefined, month: "short", day: "numeric", year: year ? "numeric" : undefined }).format(new Date(`${iso}T12:00:00Z`));
}

/** A plain-words problem with the day, or null. */
export function dayProblem(value, snapshot) {
  if (value < snapshot.start) return { message: `The data starts on ${formatDay(snapshot.start)}.`, fix: { label: `Use ${formatDay(snapshot.start, { year: false })}`, value: snapshot.start } };
  if (value > snapshot.end) return { message: `The data ends on ${formatDay(snapshot.end)}.`, fix: { label: `Use ${formatDay(snapshot.end, { year: false })}`, value: snapshot.end } };
  return null;
}

// ----- The run's data -----

/**
 * What the result card shows, from one run: the request, the verifier's expected list
 * (`details.expected`, recomputed from the pinned data), the agent's city list and metric
 * aggregates (only the calls that succeeded), the verdict and the agent's summary.
 */
export function resultFromRun(request, outcome) {
  const ok = (name) => outcome.tools.filter((tool) => tool.name === name && !tool.isError && tool.data && !tool.data.error);
  const listed = ok("list_cities").at(-1)?.data ?? null;
  const metric = {};
  for (const { data } of ok("get_daily")) for (const row of data.cities ?? []) metric[row.city] = row;
  const details = outcome.verdict?.details ?? {};
  const units = details.units ?? { temperature: request.temperature_unit, wind: request.wind_unit, rain: request.rain_unit };
  return {
    version: 1,
    date: request.date,
    match: details.match ?? request.match,
    units,
    conditions: details.conditions ?? request.conditions.map((text) => parseCondition(text)?.text ?? text),
    ran_at: new Date().toISOString(),
    verdict: outcome.verdict,
    answer: outcome.answer,
    expected: details.expected ?? null,
    cities: (listed?.cities ?? []).map((row) => ({ ...row, metric: metric[row.city] ?? null })),
  };
}

/** Check that a saved result has the shape {@link resultFromRun} writes; throws a plain message if not. */
export function checkSavedResult(saved, path) {
  const fine = saved && saved.version === 1 && typeof saved.date === "string" && saved.verdict && saved.expected && Array.isArray(saved.expected.alerts) && Array.isArray(saved.cities);
  if (!fine) throw new Error(`${path} is not a saved check this page can read.`);
  return saved;
}

/** The variables the rules use, in the canonical order. */
export function neededVariables(conditions) {
  const used = new Set(conditions.map((text) => parseCondition(text)?.variable).filter(Boolean));
  return VARIABLES.filter((variable) => used.has(variable));
}

// ----- Verdict and error wording -----

const names = (list) => list.join(", ");

function valueLines(mismatches, units) {
  return Object.entries(mismatches ?? {}).map(([city, variables]) => {
    const parts = Object.entries(variables).map(([variable, { expected, reported }]) => `${variableWord(variable).toLowerCase()} ${formatValue(expected)} ${unitOf(variable, units)}, reported ${reported === null ? "nothing" : formatValue(reported)}`);
    return `${city}: ${parts.join("; ")}`;
  });
}

/**
 * The verdict in this page's words: plain words only. The checker's own `message` and `details`
 * are a developer's wording of the list shown above the banner, so they are not repeated.
 */
export function verdictWords(verdict, alerts, units) {
  if (verdict.valid) {
    const title = alerts ? "Checked. The list matches the pinned weather data exactly." : "Checked. The data confirms that no city crossed the thresholds.";
    return banner({ tone: "ok", title });
  }
  const details = verdict.details ?? {};
  const lines = [
    details.missing_alerts?.length ? `It missed: ${names(details.missing_alerts)}` : null,
    details.extra_alerts?.length ? `It wrongly included: ${names(details.extra_alerts)}` : null,
    ...valueLines(details.value_mismatches, units).map((line) => `Wrong value for ${line}`),
    Object.keys(details.matched_mismatches ?? {}).length ? `Wrong matched rules for: ${names(Object.keys(details.matched_mismatches))}` : null,
    details.missing_unavailable?.length ? `It should have marked unavailable: ${names(details.missing_unavailable)}` : null,
    details.extra_unavailable?.length ? `It wrongly marked unavailable: ${names(details.extra_unavailable)}` : null,
    Object.keys(details.unavailable_mismatches ?? {}).length ? `Wrong unavailable variables for: ${names(Object.keys(details.unavailable_mismatches))}` : null,
  ].filter(Boolean);
  if (lines.length) {
    const score = typeof verdict.score === "number" ? ` Match score ${Math.round(verdict.score * 100)}%.` : "";
    return banner({ tone: "warn", title: "The agent's list was off.", body: `The list below is the corrected one, recomputed from the pinned data.${score}`, lines });
  }
  return banner({ tone: "bad", title: "The agent did not finish its answer, so it could not be checked.", body: "The list below comes straight from the pinned data, so you can still use it." });
}

/** Known task errors in plain words, tried before the kit's common ones. */
export const ERROR_RULES = [
  { id: "cancelled", match: (message) => message === "cancelled", text: "Stopped. Nothing new was saved.", tone: "neutral" },
  { id: "range", match: "is outside the snapshot", text: "That day isn't in the data. Pick a day from Jul 1 to Sep 30, 2026." },
  { id: "date", match: /date must be YYYY-MM-DD|is not a calendar day/, text: "That isn't a valid day. Pick another one." },
  { id: "rule", match: "is not a condition", text: `One of the rules isn't valid. ${GRAMMAR}` },
  { id: "twice", match: "is given twice", text: "Two of the rules mean the same thing. Remove one." },
  { id: "count", match: /conditions must hold/, text: `Add 1 to ${MAX_CONDITIONS} rules.` },
  { id: "input", match: "invalid input", text: "The request wasn't valid. Check the day and the rules." },
];

/** A muted progress note for a tool call that failed (the agent usually retries). */
export function failedCallNote(tool) {
  const text = String(tool.output ?? "");
  const city = text.match(/unknown city ([^;]+);/)?.[1];
  if (city) return `The agent asked for a city that isn't in the data (${city}); it retried.`;
  if (text.includes("1 to 10 distinct")) return "The agent asked for too many cities at once; it retried.";
  return "One step failed; the agent retried.";
}

// ----- Views -----

function alertTable(result, needed) {
  const byCity = Object.fromEntries(result.cities.map((row) => [row.city, row]));
  const missed = new Set(result.verdict.details?.missing_alerts ?? []);
  const rows = result.expected.alerts.map((alert) => ({ ...alert, country: byCity[alert.city]?.country ?? "", missed: missed.has(alert.city) }));
  const columns = [
    { key: "city", label: "City", primary: true, render: (row) => h("span", { class: "city-cell" }, h("strong", { text: row.city }), row.country ? h("span", { class: "muted small-text", text: row.country }) : null, row.missed ? h("span", { class: "pill tone-warn", text: "Agent missed this" }) : null) },
    ...needed.map((variable) => ({
      key: variable,
      label: `${variableWord(variable)} (${unitOf(variable, result.units)})`,
      numeric: true,
      value: (row) => row.values[variable] ?? null,
      render: (row) => (row.values[variable] === undefined ? null : h("span", { class: "numeric value" }, formatValue(row.values[variable]))),
    })),
    { key: "matched", label: "Rules met", sortable: false, value: (row) => row.matched.join(", "), render: (row) => h("span", { class: "rules-met" }, row.matched.map((text) => h("code", { class: "rule-chip", text: ruleWords(text, result.units) }))) },
  ];
  return dataTable({ columns, rows, sort: { key: "city" }, caption: "The cities that crossed the thresholds", empty: "No city crossed the thresholds." });
}

function unavailableList(result) {
  const entries = result.expected.unavailable ?? [];
  if (!entries.length) return null;
  return h(
    "div",
    { class: "unavailable" },
    h("h3", { text: `${entries.length === 1 ? "One city" : `${entries.length} cities`} could not be judged` }),
    h("p", { class: "muted small-text", text: "A missing hour for a variable a rule uses keeps a city out of the list, whatever its other values." }),
    h("ul", {}, entries.map((entry) => h("li", {}, h("strong", { text: entry.city }), ` · missing ${entry.variables.map((variable) => variableWord(variable).toLowerCase()).join(", ")}`))),
  );
}

function allCitiesTable(result) {
  const alerted = new Set(result.expected.alerts.map((alert) => alert.city));
  const rows = result.cities.map((row) => ({ ...row, ...(row.metric ?? {}), hours: row.metric?.hours ?? row.hours_available ?? null }));
  const number = (key, digits = 1) => (row) => (row[key] === null || row[key] === undefined ? null : h("span", { class: "numeric", text: row[key].toFixed(digits) }));
  return dataTable({
    columns: [
      { key: "city", label: "City", primary: true, render: (row) => h("span", { class: "city-cell" }, h("strong", { text: row.city }), h("span", { class: "muted small-text", text: row.country })) },
      { key: "hours", label: "Hours", numeric: true },
      { key: "max_temp_c", label: "High (°C)", numeric: true, render: number("max_temp_c") },
      { key: "min_temp_c", label: "Low (°C)", numeric: true, render: number("min_temp_c") },
      { key: "rain_mm", label: "Rain (mm)", numeric: true, render: number("rain_mm") },
      { key: "max_wind_kmh", label: "Wind (km/h)", numeric: true, render: number("max_wind_kmh") },
      { key: "mean_humidity_pct", label: "Humidity (%)", numeric: true, render: number("mean_humidity_pct") },
    ],
    rows,
    sort: { key: "city" },
    filterPlaceholder: "Find a city",
    rowClass: (row) => (alerted.has(row.city) ? "highlight" : ""),
    caption: "Every city's day in metric units, as the agent read it",
    empty: "No city matches.",
  });
}

/**
 * The result card of one check, from a run or a saved result.
 * @param {object} result What {@link resultFromRun} returns.
 * @param {object} snapshot The page's SNAPSHOT (the data note).
 * @param {{saved?: boolean, actions?: Node[]}} [options] `saved` adds "Saved on …".
 */
export function resultCard(result, snapshot, { saved = false, actions = [] } = {}) {
  const alerts = result.expected.alerts;
  const total = result.cities.length || snapshot.cities.length;
  const needed = neededVariables(result.conditions);
  const headline = alerts.length
    ? `${alerts.length === total ? "All" : `${alerts.length} of ${total}`} ${alerts.length === 1 ? "city" : "cities"} crossed your thresholds`
    : `None of the ${total} cities crossed your thresholds`;
  const rules = result.conditions.map((text) => ruleWords(text, result.units));
  const matchWords = result.conditions.length === 1 ? "" : result.match === "all" ? " (all of them)" : " (any of them)";
  const read = result.cities.filter((row) => row.metric).length;
  const parts = [
    h(
      "header",
      { class: "result-head" },
      h("div", { class: "result-titles" }, h("h2", { class: "result-headline", text: headline }), h("p", { class: "muted result-day" }, icon("calendar", { size: 16 }), formatDay(result.date)), h("p", { class: "result-rules" }, rules.map((text) => h("code", { class: "rule-chip", text })), h("span", { class: "muted small-text", text: matchWords }))),
      actions.length ? h("div", { class: "card-actions" }, actions) : null,
    ),
    saved ? h("p", { class: "muted small-text" }, `Saved on ${new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(result.ran_at))}. Nothing new was run.`) : null,
    verdictWords(result.verdict, alerts.length, result.units),
    alerts.length ? alertTable(result, needed).element : null,
    unavailableList(result),
    read ? disclosure({ summary: `See all ${read} cities the agent read`, hint: "Metric units" }, allCitiesTable(result).element) : banner({ tone: "neutral", title: "The agent's own readings from this run are incomplete.", body: "The list above comes from the pinned data; the other cities' readings aren't shown.", live: false }),
    result.answer ? disclosure({ summary: "The agent's summary" }, h("p", { class: "prose", text: result.answer })) : null,
    disclosure({ summary: "About this data" }, dataNote(snapshot)),
  ];
  return h("section", { class: "card large result-card", "aria-label": headline }, parts);
}

/** The data note: where the snapshot comes from and what a day means. */
export function dataNote(snapshot) {
  const short = snapshot.shortDays.map((day) => `${day.city} on ${formatDay(day.date, { weekday: false })} (${day.hours} hours)`);
  return h(
    "ul",
    { class: "about-list" },
    h("li", { text: `Hourly weather for ${snapshot.cities.length} cities from ${formatDay(snapshot.start, { weekday: false })} to ${formatDay(snapshot.end, { weekday: false })}, from the ${snapshot.source} (ERA5-based), fetched once on ${formatDay(snapshot.fetchedAt.slice(0, 10), { weekday: false })} and pinned. Nothing is fetched during a run.` }),
    h("li", { text: "A city's day is its own calendar day in its time zone. High and low are the hourly extremes, rain the hourly total, wind the highest hourly speed, humidity the mean of the hourly values." }),
    h("li", { text: short.length ? `Days with 23 hours, when clocks went forward: ${short.join("; ")}.` : "Every day in the data has 24 hours." }),
    h("li", { text: "Conversions are exact (°F = °C × 9/5 + 32; mph = km/h ÷ 1.609344; m/s = km/h ÷ 3.6; in = mm ÷ 25.4); rules are tested on the unrounded value; values are shown rounded half up to one decimal." }),
  );
}

/** A saved check as a chip label: "Sep 6 · 3 alerts" and a check or a warning. */
export function savedChipLabel(result) {
  const count = result.expected.alerts.length;
  return [
    h("span", { text: `${formatDay(result.date, { weekday: false, year: false })} · ${count ? `${count} alert${count === 1 ? "" : "s"}` : "no alerts"}` }),
    icon(result.verdict.valid ? "check" : "alert", { size: 14, label: result.verdict.valid ? "checked" : "the agent's list was off" }),
  ];
}

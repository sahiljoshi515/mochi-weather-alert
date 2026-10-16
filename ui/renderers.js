// How this plugin's page draws its tasks' outputs: the daily-alert report (an AlertReportOutput) with
// the rules asked for, one row per alert city with its values in the requested units and the rules it
// met, the cities with a missing reading and the data the values come from; and the date check (a
// DateOutput) with each kind of data's date range. Each ends with the verdict's checks.
import { dataTable, disclosure, h, icon, plural, statusPill } from "./kit/components.js";

const VARIABLES = ["max_temp", "min_temp", "rain", "wind", "humidity"];
const LABEL = { max_temp: "High", min_temp: "Low", rain: "Rain", wind: "Wind", humidity: "Humidity" };
const UNIT = { C: "°C", F: "°F", kmh: "km/h", mph: "mph", ms: "m/s", mm: "mm", in: "in" };
const OP = { ">=": "≥", "<=": "≤", ">": ">", "<": "<" };
const CONDITION = /^\s*(max_temp|min_temp|rain|wind|humidity)\s*(>=|<=|>|<)\s*(\S+)\s*$/;
const DAY = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const KIND = { archive: "Archive", forecast: "Forecast run", outlook: "Seasonal outlook" };

/** The unit a variable's values carry under the task's choices. */
function unitOf(task, variable) {
  if (variable === "humidity") return "%";
  if (variable === "rain") return UNIT[task.rain_unit] ?? task.rain_unit;
  if (variable === "wind") return UNIT[task.wind_unit] ?? task.wind_unit;
  return UNIT[task.temperature_unit] ?? task.temperature_unit;
}

/** "High ≥ 101.3 °F", or the condition as given when it does not parse. */
function ruleText(task, text) {
  const match = CONDITION.exec(text);
  if (!match) return text;
  const [, variable, op, number] = match;
  return `${LABEL[variable]} ${OP[op]} ${number} ${unitOf(task, variable)}`;
}

/** The variables the conditions use, in the canonical order. */
function neededVariables(task) {
  const used = new Set(task.conditions.map((text) => CONDITION.exec(text)?.[1]).filter(Boolean));
  return VARIABLES.filter((variable) => used.has(variable));
}

const pill = (text, tone = "info") => h("span", { class: `pill tone-${tone}`, text });
const number = (value) => (typeof value === "number" ? value.toFixed(1) : "—");

/** The day as words, or the date as given. */
function dayText(date) {
  const parsed = new Date(`${date}T12:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? date : DAY.format(parsed);
}

function rulesLine(task) {
  const joiner = task.match === "all" ? "all of" : "any of";
  return h(
    "div",
    { class: "alert-rules" },
    h("span", { class: "muted small-text", text: `${joiner}:` }),
    task.conditions.map((text) => pill(ruleText(task, text))),
  );
}

function alertsTable(task, alerts) {
  const needed = neededVariables(task);
  const columns = [
    { key: "city", label: "City", primary: true },
    ...needed.map((variable) => ({
      key: variable,
      label: `${LABEL[variable]} (${unitOf(task, variable)})`,
      numeric: true,
      value: (row) => row.values[variable] ?? null,
      render: (row) => number(row.values[variable]),
    })),
    {
      key: "matched",
      label: "Rules met",
      sortable: false,
      value: (row) => row.matched.join(", "),
      render: (row) => h("div", { class: "alert-matched" }, row.matched.map((text) => pill(ruleText(task, text), "ok"))),
    },
  ];
  return dataTable({ caption: "Alert cities", columns, rows: alerts, limit: 30, empty: "No city crossed the thresholds." }).element;
}

function unavailableList(unavailable) {
  return h(
    "div",
    { class: "alert-unavailable" },
    h("p", { class: "small-text" }, icon("alert", { size: 14 }), ` ${plural(unavailable.length, "city", "cities")} missing a reading, never an alert:`),
    h(
      "ul",
      { class: "alert-unavailable-list" },
      unavailable.map((entry) => h("li", {}, h("strong", { text: entry.city }), ` — no complete day for ${entry.variables.map((variable) => (LABEL[variable] ?? variable).toLowerCase()).join(", ")}`)),
    ),
  );
}

/** Where the values come from, with Open-Meteo's attribution. */
function sourceLine(data) {
  if (!data || !data.label) return null;
  return h(
    "p",
    { class: "alert-source small-text" },
    icon("info", { size: 14 }),
    ` Data: ${data.label}. `,
    h("a", { href: "https://open-meteo.com", target: "_blank", rel: "noopener noreferrer", text: "Weather data by Open-Meteo.com" }),
    " (CC BY 4.0)",
  );
}

/** A check's expected or actual value as short text: lists joined, mappings as "key value" pairs. */
function valueText(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? value.map(valueText).join(", ") : "none";
  if (typeof value === "object") {
    const pairs = Object.entries(value);
    return pairs.length ? pairs.map(([key, item]) => `${key} ${valueText(item)}`).join("; ") : "none";
  }
  return String(value);
}

const clipped = (text, most = 240) => (text.length > most ? `${text.slice(0, most - 1)}…` : text);
const MARK = { ok: ["check", "holds"], bad: ["error", "fails"], skip: ["minus", "not checked"] };

/** The verdict's checks, in order: ✓ holds; ✗ fails, with why and what was expected against what was
 *  found; – not checked, with the earlier check it needed. */
function checksList(verdict) {
  const constraints = Array.isArray(verdict?.details?.constraints) ? verdict.details.constraints : [];
  if (!constraints.length) return null;
  const failed = constraints.filter((entry) => entry.ok === false).length;
  const skipped = constraints.filter((entry) => entry.ok !== true && entry.ok !== false).length;
  const items = constraints.map((entry) => {
    const state = entry.ok === true ? "ok" : entry.ok === false ? "bad" : "skip";
    const [mark, word] = MARK[state];
    const values = entry.ok === false && entry.expected != null && entry.actual != null;
    return h(
      "li",
      { class: `check check-${state}` },
      h("div", { class: "check-head" }, icon(mark, { size: 14, label: word }), h("span", { text: entry.constraint }), h("code", { class: "check-id", text: entry.id })),
      entry.ok !== true && entry.message ? h("p", { class: "check-message small-text", text: entry.message }) : null,
      values
        ? h(
            "p",
            { class: "check-values small-text" },
            h("span", { class: "muted", text: "Expected: " }),
            clipped(valueText(entry.expected)),
            h("span", { class: "muted", text: " · Actual: " }),
            clipped(valueText(entry.actual)),
          )
        : null,
    );
  });
  const hint = [failed ? `${failed} failed` : "", skipped ? `${skipped} not checked` : ""].filter(Boolean).join(", ") || "all hold";
  return disclosure(
    { summary: `Checks: ${constraints.length - failed - skipped} of ${constraints.length} hold`, open: failed > 0, hint },
    h("ol", { class: "checks" }, items),
  );
}

function alertReport({ task, data, alerts, unavailable }, { verdict } = {}) {
  return h(
    "div",
    { class: "alert-report" },
    h(
      "p",
      { class: "alert-summary" },
      icon(alerts.length ? "alert" : "check", { size: 16 }),
      alerts.length ? ` ${plural(alerts.length, "city", "cities")} crossed the thresholds on ${dayText(task.date)}` : ` No city crossed the thresholds on ${dayText(task.date)}`,
    ),
    rulesLine(task),
    sourceLine(data),
    alerts.length ? alertsTable(task, alerts) : null,
    unavailable.length ? unavailableList(unavailable) : null,
    checksList(verdict),
  );
}

function dateCheck(answer, { verdict } = {}) {
  const ranges = [
    ["archive", answer.archive_first, answer.archive_last],
    ["forecast", answer.forecast_first, answer.forecast_last],
    ["outlook", answer.outlook_first, answer.outlook_last],
  ];
  return h(
    "div",
    { class: "date-check" },
    h("p", { class: "alert-summary" }, icon("calendar", { size: 16 }), ` ${answer.message}`),
    h(
      "dl",
      { class: "date-ranges" },
      ranges.map(([kind, first, last]) =>
        h(
          "div",
          { class: kind === answer.kind ? "date-range chosen" : "date-range" },
          h("dt", {}, KIND[kind], kind === answer.kind ? statusPill(dayText(answer.date), "info") : null),
          h("dd", { text: `${first} to ${last}` }),
        ),
      ),
    ),
    h("p", { class: "muted small-text", text: `Clock ${answer.clock} UTC; today is ${answer.today}.` }),
    checksList(verdict),
  );
}

export default {
  AlertReportOutput: (value, context) => alertReport(value, context),
  DateOutput: (value, context) => dateCheck(value, context),
};

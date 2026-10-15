// How this plugin's page draws the daily-alert task's output, an AlertReportOutput: the rules the
// person asked for, then one row per alert city with its values in the requested units and the
// rules it met, and the cities whose day had a missing hour for a needed variable.
import { dataTable, h, icon, plural } from "./kit/components.js";

const VARIABLES = ["max_temp", "min_temp", "rain", "wind", "humidity"];
const LABEL = { max_temp: "High", min_temp: "Low", rain: "Rain", wind: "Wind", humidity: "Humidity" };
const UNIT = { C: "°C", F: "°F", kmh: "km/h", mph: "mph", ms: "m/s", mm: "mm", in: "in" };
const OP = { ">=": "≥", "<=": "≤", ">": ">", "<": "<" };
const CONDITION = /^\s*(max_temp|min_temp|rain|wind|humidity)\s*(>=|<=|>|<)\s*(\S+)\s*$/;
const DAY = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

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

function unavailableList(task, unavailable) {
  return h(
    "div",
    { class: "alert-unavailable" },
    h("p", { class: "small-text" }, icon("alert", { size: 14 }), ` ${plural(unavailable.length, "city", "cities")} with a missing hour, never an alert:`),
    h(
      "ul",
      { class: "alert-unavailable-list" },
      unavailable.map((entry) => h("li", {}, h("strong", { text: entry.city }), ` — no complete day for ${entry.variables.map((variable) => (LABEL[variable] ?? variable).toLowerCase()).join(", ")}`)),
    ),
  );
}

export default {
  AlertReportOutput: ({ task, alerts, unavailable }) =>
    h(
      "div",
      { class: "alert-report" },
      h(
        "p",
        { class: "alert-summary" },
        icon(alerts.length ? "alert" : "check", { size: 16 }),
        alerts.length ? ` ${plural(alerts.length, "city", "cities")} crossed the thresholds on ${dayText(task.date)}` : ` No city crossed the thresholds on ${dayText(task.date)}`,
      ),
      rulesLine(task),
      alerts.length ? alertsTable(task, alerts) : null,
      unavailable.length ? unavailableList(task, unavailable) : null,
    ),
};

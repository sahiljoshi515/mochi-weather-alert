// The condition grammar, exactly verifier/rules.py's, with no DOM and no kit import so a test can
// run it under node and compare it with the Python grammar. The page imports it through rules.js.

export const VARIABLES = Object.freeze(["max_temp", "min_temp", "rain", "wind", "humidity"]);
export const OPS = Object.freeze([">=", ">", "<=", "<"]);
export const MAX_CONDITIONS = 6;
// Exactly verifier/rules.py's CONDITION: a variable, an operator, a number with at most 2 decimals.
const CONDITION = /^\s*(max_temp|min_temp|rain|wind|humidity)\s*(>=|<=|>|<)\s*([+-]?(?:\d+(?:\.\d{1,2})?|\.\d{1,2}))\s*$/;
export const GRAMMAR = "A rule is a variable (max_temp, min_temp, rain, wind or humidity), then >=, >, <= or <, then a number with at most 2 decimals, like max_temp >= 95.";

/** A threshold as its shortest plain text, as Python's Decimal.normalize() writes it: 95.00 → 95, .5 → 0.5, -0 → 0. */
export function canonicalNumber(text) {
  let rest = text.trim();
  const negative = rest.startsWith("-");
  rest = rest.replace(/^[+-]/, "");
  let [whole, fraction = ""] = rest.split(".");
  whole = whole.replace(/^0+/, "") || "0";
  fraction = fraction.replace(/0+$/, "");
  if (whole === "0" && fraction === "") return "0";
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

/** The condition `text` means, `{variable, op, number, text}` with `text` canonical, or null. */
export function parseCondition(text) {
  const found = typeof text === "string" ? text.match(CONDITION) : null;
  if (!found) return null;
  const [, variable, op, number] = found;
  const canonical = canonicalNumber(number);
  return { variable, op, number: canonical, text: `${variable} ${op} ${canonical}` };
}

/** A plain-words reason `text` cannot join the rules `existing`, or null. */
export function conditionProblem(text, existing = []) {
  const parsed = parseCondition(text);
  if (!parsed) return GRAMMAR;
  const twin = existing.find((other) => parseCondition(other)?.text === parsed.text);
  if (twin) return twin === text ? "That rule is already in the list." : `That rule means the same as ${twin}.`;
  if (existing.length >= MAX_CONDITIONS) return `Up to ${MAX_CONDITIONS} rules.`;
  return null;
}

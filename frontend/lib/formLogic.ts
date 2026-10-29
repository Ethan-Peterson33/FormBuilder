/**
 * Conditional logic engine.
 *
 * Pure functions only - no React. The same rules are implemented in
 * `backend/services/form_logic.py` so the server can verify submissions
 * against the same visibility rules the browser applied.
 */

import type {
  Condition,
  ConditionOperator,
  ConditionValue,
  FieldConfig,
  FieldLogic,
  FieldType,
  FieldValue,
  FormValues,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Operator metadata (drives the editor UI)
// ---------------------------------------------------------------------------

export const OPERATORS_BY_FIELD_TYPE: Record<FieldType, ConditionOperator[]> = {
  text: [
    "equals", "not_equals", "contains", "not_contains", "starts_with",
    "ends_with", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty",
  ],
  textarea: [
    "equals", "not_equals", "contains", "not_contains", "starts_with",
    "ends_with", "is_empty", "is_not_empty",
  ],
  select: ["equals", "not_equals", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
  radio: ["equals", "not_equals", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
  number: [
    "equals", "not_equals", "greater_than", "greater_than_or_equal",
    "less_than", "less_than_or_equal", "is_empty", "is_not_empty",
  ],
  date: [
    "equals", "not_equals", "before", "after", "on_or_before", "on_or_after",
    "is_empty", "is_not_empty",
  ],
  checkbox: ["is_checked", "is_not_checked"],
};

export const OPERATOR_LABELS: Record<ConditionOperator, string> = {
  equals: "is equal to",
  not_equals: "is not equal to",
  is_empty: "is empty",
  is_not_empty: "is not empty",
  contains: "contains",
  not_contains: "does not contain",
  starts_with: "starts with",
  ends_with: "ends with",
  is_one_of: "is one of",
  is_not_one_of: "is not one of",
  greater_than: "is greater than",
  greater_than_or_equal: "is greater than or equal to",
  less_than: "is less than",
  less_than_or_equal: "is less than or equal to",
  before: "is before",
  after: "is after",
  on_or_before: "is on or before",
  on_or_after: "is on or after",
  is_checked: "is checked",
  is_not_checked: "is not checked",
};

/** Operators that need no comparison value. */
export const VALUELESS_OPERATORS: ReadonlySet<ConditionOperator> = new Set([
  "is_empty",
  "is_not_empty",
  "is_checked",
  "is_not_checked",
]);

/** Operators whose value is a list of strings. */
export const LIST_OPERATORS: ReadonlySet<ConditionOperator> = new Set(["is_one_of", "is_not_one_of"]);

export function operatorNeedsValue(operator: ConditionOperator): boolean {
  return !VALUELESS_OPERATORS.has(operator);
}

export function operatorsForField(field: FieldConfig | undefined): ConditionOperator[] {
  if (!field) return [];
  return OPERATORS_BY_FIELD_TYPE[field.type] ?? [];
}

// ---------------------------------------------------------------------------
// Value helpers
// ---------------------------------------------------------------------------

export function isEmptyValue(value: FieldValue): boolean {
  if (value === undefined || value === null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (typeof value === "number") return Number.isNaN(value);
  return false; // booleans are never "empty"
}

function toNumber(value: FieldValue | ConditionValue): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** Parse an ISO-ish date (`YYYY-MM-DD` or full ISO string) to a UTC day timestamp. */
function toDateStamp(value: FieldValue | ConditionValue): number | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value.trim());
  if (!match) return null;
  const [, year, month, day] = match;
  const stamp = Date.UTC(Number(year), Number(month) - 1, Number(day));
  return Number.isNaN(stamp) ? null : stamp;
}

function toText(value: FieldValue | ConditionValue): string {
  if (value === undefined || value === null) return "";
  if (Array.isArray(value)) return value.join(",");
  return String(value);
}

function toList(value: ConditionValue | undefined): string[] {
  if (Array.isArray(value)) return value.map((v) => v.trim()).filter(Boolean);
  if (typeof value === "string") {
    return value
      .split(/[,\n]/)
      .map((v) => v.trim())
      .filter(Boolean);
  }
  if (value === undefined || value === null) return [];
  return [String(value)];
}

function normalise(text: string): string {
  return text.trim().toLowerCase();
}

// ---------------------------------------------------------------------------
// Evaluation
// ---------------------------------------------------------------------------

/**
 * Evaluate one condition against the current value of its source field.
 * `sourceType` lets "equals" behave numerically for number fields and by
 * calendar day for date fields.
 */
export function evaluateCondition(
  condition: Condition,
  actual: FieldValue,
  sourceType?: FieldType
): boolean {
  const { operator, value: expected } = condition;

  switch (operator) {
    case "is_empty":
      return isEmptyValue(actual);
    case "is_not_empty":
      return !isEmptyValue(actual);
    case "is_checked":
      return actual === true || actual === "true";
    case "is_not_checked":
      return !(actual === true || actual === "true");
  }

  // Every remaining operator is a comparison; a missing answer never matches
  // a positive comparison, but does satisfy a negative one.
  const negative = operator === "not_equals" || operator === "not_contains" || operator === "is_not_one_of";
  if (isEmptyValue(actual)) return negative;

  if (sourceType === "number" || operator.startsWith("greater") || operator.startsWith("less")) {
    const a = toNumber(actual);
    const b = toNumber(expected ?? null);
    if (a === null || b === null) {
      if (operator === "equals") return false;
      if (operator === "not_equals") return true;
      if (sourceType === "number") return false;
      // fall through to string handling for non-number sources
    } else {
      switch (operator) {
        case "equals":
          return a === b;
        case "not_equals":
          return a !== b;
        case "greater_than":
          return a > b;
        case "greater_than_or_equal":
          return a >= b;
        case "less_than":
          return a < b;
        case "less_than_or_equal":
          return a <= b;
      }
    }
  }

  if (
    sourceType === "date" ||
    operator === "before" ||
    operator === "after" ||
    operator === "on_or_before" ||
    operator === "on_or_after"
  ) {
    const a = toDateStamp(actual);
    const b = toDateStamp(expected ?? null);
    if (a === null || b === null) return operator === "not_equals";
    switch (operator) {
      case "equals":
        return a === b;
      case "not_equals":
        return a !== b;
      case "before":
        return a < b;
      case "after":
        return a > b;
      case "on_or_before":
        return a <= b;
      case "on_or_after":
        return a >= b;
    }
  }

  const actualText = normalise(toText(actual));

  switch (operator) {
    case "equals":
      return actualText === normalise(toText(expected ?? null));
    case "not_equals":
      return actualText !== normalise(toText(expected ?? null));
    case "contains":
      return actualText.includes(normalise(toText(expected ?? null)));
    case "not_contains":
      return !actualText.includes(normalise(toText(expected ?? null)));
    case "starts_with":
      return actualText.startsWith(normalise(toText(expected ?? null)));
    case "ends_with":
      return actualText.endsWith(normalise(toText(expected ?? null)));
    case "is_one_of":
      return toList(expected).some((option) => normalise(option) === actualText);
    case "is_not_one_of":
      return !toList(expected).some((option) => normalise(option) === actualText);
    default:
      return false;
  }
}

/**
 * Evaluate a field's logic block. Returns `true` when the field should be
 * visible. Fields without logic (or with no conditions) are always visible.
 *
 * `fieldTypes` maps field id -> type so comparisons use the right semantics.
 */
export function evaluateFieldLogic(
  logic: FieldLogic | undefined,
  values: FormValues,
  fieldTypes: Record<string, FieldType> = {}
): boolean {
  if (!logic || logic.conditions.length === 0) return true;

  const results = logic.conditions.map((condition) =>
    evaluateCondition(condition, values[condition.fieldId], fieldTypes[condition.fieldId])
  );

  const matched = logic.match === "any" ? results.some(Boolean) : results.every(Boolean);

  return logic.action === "hide" ? !matched : matched;
}

/**
 * Work out which fields are visible for the given answers.
 *
 * Fields are processed in order, and a field that is hidden has its value
 * treated as empty for any later conditions that depend on it. That way a
 * chain like A -> B -> C collapses correctly when A hides B.
 */
export function getVisibleFieldIds(fields: FieldConfig[], values: FormValues): Set<string> {
  const fieldTypes: Record<string, FieldType> = {};
  for (const field of fields) fieldTypes[field.id] = field.type;

  const effectiveValues: FormValues = { ...values };
  const visible = new Set<string>();

  for (const field of fields) {
    const isVisible = evaluateFieldLogic(field.logic, effectiveValues, fieldTypes);
    if (isVisible) {
      visible.add(field.id);
    } else {
      effectiveValues[field.id] = undefined;
    }
  }

  return visible;
}

export function isFieldVisible(field: FieldConfig, fields: FieldConfig[], values: FormValues): boolean {
  return getVisibleFieldIds(fields, values).has(field.id);
}

/** Fields that may be used as a condition source for the field at `index` (i.e. earlier fields). */
export function availableSourceFields(fields: FieldConfig[], index: number): FieldConfig[] {
  return fields.slice(0, Math.max(0, index));
}

/**
 * Remove answers for hidden fields so they are not submitted, and report
 * which fields were hidden.
 */
export function prepareSubmissionData(
  fields: FieldConfig[],
  values: FormValues
): { data: FormValues; hiddenFields: string[] } {
  const visible = getVisibleFieldIds(fields, values);
  const data: FormValues = {};
  const hiddenFields: string[] = [];

  for (const field of fields) {
    if (visible.has(field.id)) {
      if (!isEmptyValue(values[field.id])) data[field.id] = values[field.id];
    } else {
      hiddenFields.push(field.id);
    }
  }

  return { data, hiddenFields };
}

const OPTION_VALUE_OPERATORS: ReadonlySet<ConditionOperator> = new Set([
  "equals",
  "not_equals",
  "is_one_of",
  "is_not_one_of",
]);

/**
 * For select/radio sources, condition values must be real options. Returns
 * every offending value (empty when everything lines up).
 */
export function staleOptionValues(source: FieldConfig, condition: Condition): string[] {
  if (source.type !== "select" && source.type !== "radio") return [];
  if (!source.options || source.options.length === 0) return [];
  if (!OPTION_VALUE_OPERATORS.has(condition.operator)) return [];
  const allowed = new Set(source.options.map((option) => normalise(option)));
  const values = Array.isArray(condition.value) ? condition.value : [toText(condition.value ?? null)];
  return values.map(String).filter((value) => !allowed.has(normalise(value)));
}

export type LogicProblemCode =
  | "missing_source" // no field chosen
  | "self_reference"
  | "unknown_source" // field no longer exists
  | "source_after" // field exists but appears after this one
  | "invalid_operator" // operator does not suit the source type
  | "missing_value"
  | "stale_value"; // value is not one of the source's options

export type LogicProblem = {
  /** Field that owns the logic block. */
  fieldId: string;
  conditionIndex: number;
  code: LogicProblemCode;
  /** Short message suitable for display next to the condition. */
  message: string;
  /** For `stale_value`: the offending values. */
  values?: string[];
};

/**
 * Find every broken reference in the form's conditional logic. This is the
 * single source of truth for what "invalid" means in the builder; the
 * backend applies the same rules when saving (`services/form_rules.py`).
 */
export function findLogicProblems(fields: readonly FieldConfig[]): LogicProblem[] {
  const problems: LogicProblem[] = [];
  const byId = new Map(fields.map((f) => [f.id, f]));

  fields.forEach((field, index) => {
    if (!field.logic) return;
    const earlier = new Set(fields.slice(0, index).map((f) => f.id));

    field.logic.conditions.forEach((condition, conditionIndex) => {
      const push = (code: LogicProblemCode, message: string, values?: string[]) =>
        problems.push({ fieldId: field.id, conditionIndex, code, message, values });

      if (!condition.fieldId) {
        push("missing_source", "Choose a field to compare.");
        return;
      }
      if (condition.fieldId === field.id) {
        push("self_reference", "A field cannot depend on itself.");
        return;
      }
      const source = byId.get(condition.fieldId);
      if (!source) {
        push(
          "unknown_source",
          "This condition refers to a field that no longer exists. Choose another field or remove the condition."
        );
        return;
      }
      const sourceLabel = source.label || source.id;
      if (!earlier.has(source.id)) {
        push(
          "source_after",
          `"${sourceLabel}" now appears after this field; conditions may only use fields earlier in the form. Move this field below it, or change the condition.`
        );
        return;
      }
      if (!operatorsForField(source).includes(condition.operator)) {
        push(
          "invalid_operator",
          `"${OPERATOR_LABELS[condition.operator]}" cannot be used with a ${source.type} field. Pick another operator.`
        );
        return;
      }
      if (operatorNeedsValue(condition.operator) && isEmptyValue(toText(condition.value ?? null))) {
        push("missing_value", "Enter a value to compare against.");
        return;
      }
      const stale = staleOptionValues(source, condition);
      if (stale.length > 0) {
        const list = stale.map((v) => `"${v}"`).join(", ");
        push(
          "stale_value",
          `${list} ${stale.length === 1 ? "is" : "are"} no longer ${stale.length === 1 ? "an option" : "options"} of "${sourceLabel}". Choose a current option or remove the condition.`,
          stale
        );
      }
    });
  });

  return problems;
}

/**
 * Human-readable, field-prefixed problem list (for the builder's summary).
 * Mirrors the checks in the backend model.
 */
export function validateFormLogic(fields: FieldConfig[]): string[] {
  const byId = new Map(fields.map((f) => [f.id, f]));
  return findLogicProblems(fields).map((problem) => {
    const field = byId.get(problem.fieldId);
    const label = field?.label || problem.fieldId;
    return `"${label}" condition ${problem.conditionIndex + 1}: ${problem.message}`;
  });
}

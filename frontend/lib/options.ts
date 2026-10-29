/**
 * Helpers for field option lists. Shared by the field editor, the Manage
 * Options modal and the form builder's save validation.
 *
 * Options are compared case-insensitively because conditional logic compares
 * values case-insensitively - "Yes" and "yes" would be indistinguishable.
 */

import type { Condition, FieldConfig } from "@/lib/types";

export type OptionRename = { from: string; to: string };

export type OptionChange =
  | { type: "rename"; index: number; from: string; to: string }
  | { type: "add"; index: number }
  | { type: "remove"; index: number; value: string }
  | { type: "move"; from: number; to: number }
  /** Whole list replaced (e.g. from the Manage Options modal). `renames` lists
   *  options that were renamed during the edit so logic rules can follow. */
  | { type: "replace"; renames?: OptionRename[] };

export function normalizeOption(option: string): string {
  return option.trim().toLowerCase();
}

/** Trim and drop empty options (what gets persisted). */
export function cleanOptions(options: readonly string[] | undefined): string[] {
  return (options ?? []).map((option) => option.trim()).filter(Boolean);
}

/** Indexes of options that duplicate an earlier option (case-insensitive). */
export function findDuplicateOptionIndexes(options: readonly string[]): Set<number> {
  const seen = new Set<string>();
  const duplicates = new Set<number>();
  options.forEach((option, index) => {
    const key = normalizeOption(option);
    if (!key) return;
    if (seen.has(key)) duplicates.add(index);
    seen.add(key);
  });
  return duplicates;
}

/** Human-readable problems with a set of options (empty array when fine). */
export function validateOptions(options: readonly string[] | undefined): string[] {
  const problems: string[] = [];
  const cleaned = cleanOptions(options);
  if (cleaned.length === 0) problems.push("needs at least one option.");
  const duplicates = findDuplicateOptionIndexes(options ?? []);
  if (duplicates.size > 0) {
    const names = [...duplicates].map((i) => `"${(options ?? [])[i].trim()}"`);
    problems.push(`has duplicate options: ${[...new Set(names)].join(", ")}.`);
  }
  return problems;
}

/** Problems across a whole form's choice fields, prefixed with the field label. */
export function validateFieldOptions(fields: readonly FieldConfig[]): string[] {
  const problems: string[] = [];
  for (const field of fields) {
    if (field.type !== "select" && field.type !== "radio") continue;
    const label = field.label || field.id;
    for (const problem of validateOptions(field.options)) {
      problems.push(`"${label}" ${problem}`);
    }
  }
  return problems;
}

/** Operators whose value is compared against the source field's options. */
const OPTION_VALUE_OPERATORS = new Set<Condition["operator"]>([
  "equals",
  "not_equals",
  "is_one_of",
  "is_not_one_of",
]);

export function conditionUsesOptionValue(operator: Condition["operator"]): boolean {
  return OPTION_VALUE_OPERATORS.has(operator);
}

function renameInCondition(condition: Condition, from: string, to: string): Condition {
  if (!conditionUsesOptionValue(condition.operator)) return condition;
  const value = condition.value;
  if (typeof value === "string") {
    return normalizeOption(value) === normalizeOption(from) ? { ...condition, value: to } : condition;
  }
  if (Array.isArray(value)) {
    const next = value.map((v) => (normalizeOption(v) === normalizeOption(from) ? to : v));
    return next.some((v, i) => v !== value[i]) ? { ...condition, value: next } : condition;
  }
  return condition;
}

/**
 * When an option on `sourceFieldId` is renamed, update conditions on other
 * fields that compared against the old value so the logic keeps working.
 */
export function renameOptionInConditions(
  fields: readonly FieldConfig[],
  sourceFieldId: string,
  from: string,
  to: string
): FieldConfig[] {
  if (!from.trim() || from === to) return [...fields];
  return fields.map((field) => {
    if (!field.logic || field.id === sourceFieldId) return field;
    let changed = false;
    const conditions = field.logic.conditions.map((condition) => {
      if (condition.fieldId !== sourceFieldId) return condition;
      const next = renameInCondition(condition, from, to);
      if (next !== condition) changed = true;
      return next;
    });
    return changed ? { ...field, logic: { ...field.logic, conditions } } : field;
  });
}

/** Strip empty options from choice fields before saving. */
export function sanitizeFieldsForSave(fields: readonly FieldConfig[]): FieldConfig[] {
  return fields.map((field) =>
    field.type === "select" || field.type === "radio"
      ? { ...field, options: cleanOptions(field.options) }
      : field
  );
}

/** Apply a batch of renames (in order) to the rules that depend on a field. */
export function applyOptionRenames(
  fields: readonly FieldConfig[],
  sourceFieldId: string,
  renames: readonly OptionRename[]
): FieldConfig[] {
  return renames.reduce<FieldConfig[]>(
    (current, rename) => renameOptionInConditions(current, sourceFieldId, rename.from, rename.to),
    [...fields]
  );
}

/**
 * Fold a rename event into an accumulated rename batch. Chains collapse
 * (Yes -> Ye -> Yep becomes Yes -> Yep) and renaming back to the original
 * removes the entry. When `originals` is given, only renames that start from
 * one of those options are tracked (typing into a brand-new row is not a
 * rename of anything).
 */
export function accumulateRename(
  renames: readonly OptionRename[],
  from: string,
  to: string,
  originals?: readonly string[]
): OptionRename[] {
  if (!from.trim() || !to.trim() || from === to) return [...renames];
  const existing = renames.findIndex((r) => r.to === from);
  if (existing >= 0) {
    const origin = renames[existing].from;
    const next = renames.filter((_, i) => i !== existing);
    return normalizeOption(origin) === normalizeOption(to) ? next : [...next, { from: origin, to }];
  }
  if (originals && !originals.some((o) => normalizeOption(o) === normalizeOption(from))) {
    return [...renames];
  }
  return [...renames, { from, to }];
}

/** Normalised option value -> the option as written + dependent field labels. */
export type OptionUsage = Map<string, { option: string; fields: string[] }>;

/**
 * Which options of `sourceFieldId` are referenced by rules on other fields.
 * Dependent field labels are unique and in form order.
 */
export function optionUsage(fields: readonly FieldConfig[], sourceFieldId: string): OptionUsage {
  const usage: OptionUsage = new Map();
  for (const field of fields) {
    if (field.id === sourceFieldId || !field.logic) continue;
    const label = field.label || field.id;
    for (const condition of field.logic.conditions) {
      if (condition.fieldId !== sourceFieldId || !conditionUsesOptionValue(condition.operator)) continue;
      const values = Array.isArray(condition.value)
        ? condition.value
        : condition.value === undefined || condition.value === null
          ? []
          : [String(condition.value)];
      for (const value of values) {
        const text = String(value).trim();
        const key = normalizeOption(text);
        if (!key) continue;
        const entry = usage.get(key) ?? { option: text, fields: [] };
        if (!entry.fields.includes(label)) entry.fields.push(label);
        usage.set(key, entry);
      }
    }
  }
  return usage;
}

/** Fields whose logic references `sourceFieldId` (any operator). */
export function fieldsReferencing(
  fields: readonly FieldConfig[],
  sourceFieldId: string
): FieldConfig[] {
  return fields.filter(
    (field) =>
      field.id !== sourceFieldId &&
      field.logic?.conditions.some((condition) => condition.fieldId === sourceFieldId)
  );
}

export type MissingUsedOption = { option: string; fields: string[] };

/**
 * Options that rules depend on but that would disappear if the field's
 * options became `nextOptions` (after applying `renames`).
 */
export function missingUsedOptions(
  usage: OptionUsage,
  nextOptions: readonly string[],
  renames: readonly OptionRename[] = []
): MissingUsedOption[] {
  const present = new Set(cleanOptions(nextOptions).map(normalizeOption));
  const renamedAway = new Map(renames.map((r) => [normalizeOption(r.from), normalizeOption(r.to)]));
  const missing: MissingUsedOption[] = [];
  for (const [key, { option, fields }] of usage) {
    const renamedTo = renamedAway.get(key);
    if (present.has(key) || (renamedTo && present.has(renamedTo))) continue;
    missing.push({ option, fields });
  }
  return missing;
}

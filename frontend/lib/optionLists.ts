/**
 * Draft/edit helpers for reusable option lists. Shared by the Manage Options
 * modal (inside the form builder) and the Option Library page.
 */

import { cleanOptions, normalizeOption, validateOptions } from "@/lib/options";
import type { FieldConfig, FormConfig, OptionList, OptionListInput } from "@/lib/types";

export type ListDraft = { name: string; description: string; options: string[] };

export function emptyDraft(options: string[] = []): ListDraft {
  return { name: "", description: "", options: [...options] };
}

export function draftFromList(list: OptionList): ListDraft {
  return { name: list.name, description: list.description ?? "", options: [...list.options] };
}

export function sameOptions(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

/** True when the draft differs from the stored list. */
export function isDraftDirty(draft: ListDraft, list: OptionList): boolean {
  return (
    draft.name.trim() !== list.name ||
    draft.description.trim() !== (list.description ?? "") ||
    !sameOptions(cleanOptions(draft.options), list.options)
  );
}

/** Problems that prevent saving a draft (empty array when it can be saved). */
export function draftProblems(draft: ListDraft): string[] {
  return [...(draft.name.trim() ? [] : ["needs a name."]), ...validateOptions(draft.options)];
}

export function draftToInput(draft: ListDraft): OptionListInput {
  return {
    name: draft.name.trim(),
    description: draft.description.trim(),
    options: cleanOptions(draft.options),
  };
}

export function sortLists(lists: readonly OptionList[]): OptionList[] {
  return [...lists].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
}

/** Case-insensitive search over name, description and option values. */
export function searchOptionLists(lists: readonly OptionList[], query: string): OptionList[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...lists];
  return lists.filter((list) =>
    [list.name, list.description ?? "", ...list.options].some((text) =>
      text.toLowerCase().includes(needle)
    )
  );
}

export type ListUsage = { form: FormConfig; fields: FieldConfig[] };

/**
 * Fields whose options are an exact (order-insensitive, case-insensitive)
 * copy of the list. Loading a list copies its options into a field, so this
 * is the best available signal of where a list has been used.
 */
export function findListUsage(list: OptionList, forms: readonly FormConfig[]): ListUsage[] {
  const target = new Set(list.options.map(normalizeOption));
  const usage: ListUsage[] = [];
  for (const form of forms) {
    const fields = form.fields.filter((field) => {
      if ((field.type !== "select" && field.type !== "radio") || !field.options) return false;
      const mine = new Set(field.options.map(normalizeOption));
      return mine.size === target.size && [...mine].every((option) => target.has(option));
    });
    if (fields.length > 0) usage.push({ form, fields });
  }
  return usage;
}

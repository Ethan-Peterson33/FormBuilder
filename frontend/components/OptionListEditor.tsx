"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import {
  findDuplicateOptionIndexes,
  normalizeOption,
  type OptionChange,
  type OptionUsage,
} from "@/lib/options";

type OptionListEditorProps = {
  options: string[];
  onChange: (options: string[], change: OptionChange) => void;
  /** Prefix for input ids / aria labels so several editors can coexist. */
  idPrefix: string;
  /** Compact layout for inline use inside the field editor. */
  compact?: boolean;
  disabled?: boolean;
  emptyHint?: string;
  /**
   * Options referenced by conditional-logic rules elsewhere in the form.
   * Used options get a marker, and removing one asks for confirmation.
   */
  usage?: OptionUsage;
};

const inputClass =
  "w-full rounded-lg border bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:ring-4 focus:ring-slate-100 disabled:bg-slate-50 disabled:text-slate-400";

/**
 * Row-based editor for a list of option strings: add, edit, delete, reorder.
 * Every change is reported with a description so callers can react (for
 * example, propagating a rename into conditional-logic rules).
 */
export default function OptionListEditor({
  options,
  onChange,
  idPrefix,
  compact = false,
  disabled = false,
  emptyHint = "No options yet.",
  usage,
}: OptionListEditorProps) {
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const pendingFocus = useRef<number | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<{ index: number; option: string } | null>(null);
  const duplicates = findDuplicateOptionIndexes(options);

  function usedBy(option: string): string[] {
    return usage?.get(normalizeOption(option))?.fields ?? [];
  }

  /**
   * Last non-empty text seen in each row. When a user clears a row and types a
   * new value, the rename is reported as old -> new (not "" -> new) so logic
   * rules that referenced the old option can follow it.
   */
  const lastNonEmpty = useRef<string[]>([]);

  // After each committed change: fulfil pending focus requests and keep the
  // rename memory aligned with the rows (handles external replacements too).
  useEffect(() => {
    if (pendingFocus.current !== null) {
      inputRefs.current[pendingFocus.current]?.focus();
      pendingFocus.current = null;
    }
    const memo = lastNonEmpty.current;
    if (memo.length !== options.length) memo.length = options.length;
    options.forEach((option, index) => {
      if (option.trim()) memo[index] = option;
      else if (memo[index] === undefined) memo[index] = "";
    });
  }, [options]);

  function focusRow(index: number) {
    pendingFocus.current = index;
  }

  function addOption(afterIndex: number = options.length - 1) {
    const insertAt = afterIndex + 1;
    const next = [...options.slice(0, insertAt), "", ...options.slice(insertAt)];
    lastNonEmpty.current.splice(insertAt, 0, "");
    onChange(next, { type: "add", index: insertAt });
    focusRow(insertAt);
  }

  function renameOption(index: number, value: string) {
    const next = options.map((option, i) => (i === index ? value : option));
    const from = options[index] || lastNonEmpty.current[index] || "";
    if (value.trim()) lastNonEmpty.current[index] = value;
    onChange(next, { type: "rename", index, from, to: value });
  }

  function removeOption(index: number) {
    const next = options.filter((_, i) => i !== index);
    lastNonEmpty.current.splice(index, 1);
    setConfirmRemove(null);
    onChange(next, { type: "remove", index, value: options[index] });
    focusRow(Math.max(0, index - 1));
  }

  /**
   * Removing an option that rules depend on needs an explicit confirmation.
   * A row that was cleared still counts as its last non-empty value.
   */
  function requestRemove(index: number) {
    const effective = options[index].trim() ? options[index] : lastNonEmpty.current[index] || "";
    if (usedBy(effective).length > 0) {
      setConfirmRemove({ index, option: effective });
      return;
    }
    removeOption(index);
  }

  function moveOption(from: number, direction: -1 | 1) {
    const to = from + direction;
    if (to < 0 || to >= options.length) return;
    const next = [...options];
    [next[from], next[to]] = [next[to], next[from]];
    const memo = lastNonEmpty.current;
    [memo[from], memo[to]] = [memo[to], memo[from]];
    onChange(next, { type: "move", from, to });
    focusRow(to);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>, index: number) {
    if (event.key === "Enter") {
      event.preventDefault();
      addOption(index);
    } else if (event.key === "Backspace" && options[index] === "" && options.length > 1) {
      event.preventDefault();
      requestRemove(index);
    } else if (event.altKey && event.key === "ArrowUp") {
      event.preventDefault();
      moveOption(index, -1);
    } else if (event.altKey && event.key === "ArrowDown") {
      event.preventDefault();
      moveOption(index, 1);
    }
  }

  return (
    <div className={compact ? "space-y-2" : "space-y-3"}>
      {options.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 px-3 py-4 text-center text-xs text-slate-400">
          {emptyHint}
        </p>
      ) : (
        <ol className="space-y-2" aria-label="Options">
          {options.map((option, index) => {
            const isDuplicate = duplicates.has(index);
            const isEmpty = option.trim() === "";
            const confirming = confirmRemove?.index === index ? confirmRemove : null;
            const dependents = usedBy(confirming ? confirming.option : option);
            const isUsed = dependents.length > 0;
            return (
              <li key={index} className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <span className="w-5 shrink-0 text-right text-xs tabular-nums text-slate-400">
                  {index + 1}
                </span>

                <input
                  ref={(el) => {
                    inputRefs.current[index] = el;
                  }}
                  id={`${idPrefix}-option-${index}`}
                  aria-label={`Option ${index + 1}`}
                  aria-invalid={isDuplicate || undefined}
                  value={option}
                  disabled={disabled}
                  onChange={(e) => renameOption(index, e.target.value)}
                  onKeyDown={(e) => handleKeyDown(e, index)}
                  placeholder={`Option ${index + 1}`}
                  className={`${inputClass} ${
                    isDuplicate
                      ? "border-rose-300 focus:border-rose-400"
                      : "border-slate-200 focus:border-slate-400"
                  }`}
                />

                <button
                  type="button"
                  onClick={() => moveOption(index, -1)}
                  disabled={disabled || index === 0}
                  aria-label={`Move option ${index + 1} up`}
                  title="Move up"
                  className="rounded-lg px-1.5 py-1.5 text-sm text-slate-500 transition hover:bg-slate-100 disabled:opacity-30"
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => moveOption(index, 1)}
                  disabled={disabled || index === options.length - 1}
                  aria-label={`Move option ${index + 1} down`}
                  title="Move down"
                  className="rounded-lg px-1.5 py-1.5 text-sm text-slate-500 transition hover:bg-slate-100 disabled:opacity-30"
                >
                  ↓
                </button>
                <button
                  type="button"
                  onClick={() => requestRemove(index)}
                  disabled={disabled}
                  aria-label={`Remove option ${index + 1}`}
                  title={isUsed ? `Used by logic on: ${dependents.join(", ")}` : "Remove"}
                  className="rounded-lg px-1.5 py-1.5 text-sm text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30"
                >
                  ✕
                </button>

                {(isDuplicate || isEmpty) && (
                  <span className="sr-only">
                    {isDuplicate ? "Duplicate option" : "Empty option"}
                  </span>
                )}
              </div>

              {isUsed && !confirming && (
                <p className="ml-6 text-[11px] text-violet-700">
                  <span className="font-semibold uppercase tracking-wide">In logic</span> · used by{" "}
                  {dependents.map((label) => `“${label}”`).join(", ")}
                </p>
              )}

              {confirming && (
                <div
                  role="alert"
                  className="ml-6 flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
                >
                  <span>
                    “{confirming.option}” is used by conditions on{" "}
                    {dependents.map((label) => `“${label}”`).join(", ")}. Removing it will break
                    {dependents.length === 1 ? " that rule" : " those rules"} until you update them.
                  </span>
                  <button
                    type="button"
                    onClick={() => removeOption(index)}
                    className="rounded-lg bg-amber-600 px-2.5 py-1 font-medium text-white hover:bg-amber-700"
                  >
                    Remove anyway
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmRemove(null)}
                    className="rounded-lg border border-amber-300 bg-white px-2.5 py-1 font-medium text-amber-800 hover:bg-amber-100"
                  >
                    Keep
                  </button>
                </div>
              )}
              </li>
            );
          })}
        </ol>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => addOption()}
          disabled={disabled}
          className="rounded-lg border border-dashed border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 disabled:opacity-50"
        >
          + Add option
        </button>

        {duplicates.size > 0 ? (
          <span className="text-xs text-rose-600">Duplicate options are highlighted.</span>
        ) : (
          !compact && (
            <span className="text-xs text-slate-400">
              Enter adds a row · Alt+↑/↓ reorders
            </span>
          )
        )}
      </div>
    </div>
  );
}

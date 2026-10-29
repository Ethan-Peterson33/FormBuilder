"use client";

import OptionListEditor from "@/components/OptionListEditor";
import type { ListDraft } from "@/lib/optionLists";
import type { OptionUsage } from "@/lib/options";

type OptionListFormProps = {
  draft: ListDraft;
  onChange: (partial: Partial<ListDraft>) => void;
  /** Prefix for input ids so several forms can coexist on a page. */
  idPrefix: string;
  /** Problems that block saving (rendered under the options). */
  problems?: string[];
  /** Options referenced by logic rules (only meaningful inside the form builder). */
  usage?: OptionUsage;
  disabled?: boolean;
  /** Rendered to the right of the "Options" heading (e.g. a "start from" shortcut). */
  optionsAction?: React.ReactNode;
  namePlaceholder?: string;
};

const inputClass =
  "mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:ring-4 focus:ring-slate-100 disabled:bg-slate-50";

/** Name + description + options editor for a reusable option list. */
export default function OptionListForm({
  draft,
  onChange,
  idPrefix,
  problems = [],
  usage,
  disabled = false,
  optionsAction,
  namePlaceholder = "e.g. Contact Methods",
}: OptionListFormProps) {
  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <label htmlFor={`${idPrefix}-name`} className="block text-sm font-medium text-slate-700">
          List name
          <input
            id={`${idPrefix}-name`}
            value={draft.name}
            disabled={disabled}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder={namePlaceholder}
            className={inputClass}
          />
        </label>

        <label htmlFor={`${idPrefix}-description`} className="block text-sm font-medium text-slate-700">
          Description
          <input
            id={`${idPrefix}-description`}
            value={draft.description}
            disabled={disabled}
            onChange={(e) => onChange({ description: e.target.value })}
            placeholder="Optional"
            className={inputClass}
          />
        </label>
      </div>

      <div>
        <div className="flex items-center justify-between">
          <span className="block text-sm font-medium text-slate-700">Options</span>
          {optionsAction}
        </div>
        <div className="mt-2">
          <OptionListEditor
            idPrefix={idPrefix}
            options={draft.options}
            onChange={(next) => onChange({ options: next })}
            usage={usage}
            disabled={disabled}
          />
        </div>
      </div>

      {problems.length > 0 && (
        <p className="text-xs text-amber-700">This list {problems.join(" ")}</p>
      )}
    </div>
  );
}

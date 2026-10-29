"use client";

<<<<<<< HEAD
import { useState } from "react";

import LogicEditor from "@/components/LogicEditor";
import ManageOptionsModal from "@/components/ManageOptionsModal";
import OptionListEditor from "@/components/OptionListEditor";
import { availableSourceFields, type LogicProblem } from "@/lib/formLogic";
import {
  fieldsReferencing,
  optionUsage,
  validateOptions,
  type OptionChange,
  type OptionRename,
} from "@/lib/options";
import type { FieldConfig, FieldType } from "@/lib/types";
=======
import {
  FieldConfig,
  FieldType,
} from "../components/FormRender";
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71

type FieldEditorProps = {
  field: FieldConfig;
  index: number;
<<<<<<< HEAD
  /** Every field in the form, in order. Used to offer earlier fields as logic sources. */
  allFields: FieldConfig[];
  onChange: (index: number, updates: Partial<FieldConfig>) => void;
  /**
   * Called when this field's options change. Receives a description of the
   * change so the builder can, for example, keep logic rules in step with a
   * renamed option. Falls back to `onChange` when omitted.
   */
  onOptionsChange?: (index: number, options: string[], change: OptionChange) => void;
  onRemove: (index: number) => void;
  onMove?: (index: number, direction: -1 | 1) => void;
  /** Logic problems detected for this field (see `findLogicProblems`). */
  problems?: LogicProblem[];
=======
  onChange: (
    index: number,
    updates: Partial<FieldConfig>
  ) => void;
  onRemove: (index: number) => void;
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
};

export default function FieldEditor({
  field,
  index,
<<<<<<< HEAD
  allFields,
  onChange,
  onOptionsChange,
  onRemove,
  onMove,
  problems = [],
}: FieldEditorProps) {
  const [manageOpen, setManageOpen] = useState(false);
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const isChoiceField = field.type === "select" || field.type === "radio";

  // Other fields whose rules depend on this field / on its options.
  const dependents = fieldsReferencing(allFields, field.id);
  const usage = isChoiceField ? optionUsage(allFields, field.id) : undefined;
  const optionProblems = isChoiceField ? validateOptions(field.options) : [];
  const problemCount = problems.length + optionProblems.length;

  function changeOptions(options: string[], change: OptionChange) {
    if (onOptionsChange) {
      onOptionsChange(index, options, change);
    } else {
      onChange(index, { options });
    }
  }

  function applyFromModal(options: string[], renames: OptionRename[]) {
    changeOptions(options, { type: "replace", renames });
  }

  function requestRemove() {
    if (dependents.length > 0) {
      setConfirmingRemove(true);
      return;
    }
    onRemove(index);
  }

=======
  onChange,
  onRemove,
}: FieldEditorProps) {
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
  const inputClass =
    "mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:ring-4 focus:ring-slate-100";

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Field {index + 1}
          </p>

<<<<<<< HEAD
          <h4 className="mt-1 flex flex-wrap items-center gap-2 font-semibold text-slate-900">
            {field.label || "Untitled Field"}
            {problemCount > 0 && (
              <span
                role="status"
                className="whitespace-nowrap rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rose-700"
              >
                {problemCount} {problemCount === 1 ? "problem" : "problems"}
              </span>
            )}
            {field.logic && field.logic.conditions.length > 0 && (
              <span
                title={`${field.logic.action === "hide" ? "Hidden" : "Shown"} when ${field.logic.match} of ${field.logic.conditions.length} ${field.logic.conditions.length === 1 ? "condition matches" : "conditions match"}`}
                className="whitespace-nowrap rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700"
              >
                conditional
              </span>
            )}
          </h4>
        </div>

        <div className="flex items-center gap-1">
          {onMove && (
            <>
              <button
                type="button"
                onClick={() => onMove(index, -1)}
                disabled={index === 0}
                aria-label="Move field up"
                title="Move up"
                className="rounded-lg px-2 py-1.5 text-sm text-slate-500 transition hover:bg-slate-100 disabled:opacity-30"
              >
                ↑
              </button>
              <button
                type="button"
                onClick={() => onMove(index, 1)}
                disabled={index === allFields.length - 1}
                aria-label="Move field down"
                title="Move down"
                className="rounded-lg px-2 py-1.5 text-sm text-slate-500 transition hover:bg-slate-100 disabled:opacity-30"
              >
                ↓
              </button>
            </>
          )}

          <button
            type="button"
            onClick={requestRemove}
            title={
              dependents.length > 0
                ? `Used by logic on: ${dependents.map((f) => f.label || f.id).join(", ")}`
                : undefined
            }
            className="rounded-lg px-3 py-1.5 text-sm font-medium text-rose-600 transition hover:bg-rose-50"
          >
            Remove
          </button>
        </div>
      </div>

      {confirmingRemove && (
        <div
          role="alert"
          className="mb-5 flex flex-wrap items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          <span className="flex-1">
            <strong>{field.label || "This field"}</strong> is used by conditional logic on{" "}
            {dependents.map((f) => `“${f.label || f.id}”`).join(", ")}. Removing it will also
            remove {dependents.length === 1 ? "that condition" : "those conditions"}, so{" "}
            {dependents.length === 1 ? "that field" : "those fields"} will always be shown.
          </span>
          <button
            type="button"
            onClick={() => {
              setConfirmingRemove(false);
              onRemove(index);
            }}
            className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700"
          >
            Remove field and its rules
          </button>
          <button
            type="button"
            onClick={() => setConfirmingRemove(false)}
            className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100"
          >
            Cancel
          </button>
        </div>
      )}

      {dependents.length > 0 && !confirmingRemove && (
        <p className="mb-5 text-xs text-violet-700">
          <span className="font-semibold uppercase tracking-wide">Used in logic</span> · conditions
          on {dependents.map((f) => `“${f.label || f.id}”`).join(", ")} depend on this field.
        </p>
      )}

      <div className="space-y-5">
        <div>
          <label htmlFor={`${field.id}-label`} className="block text-sm font-medium text-slate-700">
=======
          <h4 className="mt-1 font-semibold text-slate-900">
            {field.label || "Untitled Field"}
          </h4>
        </div>

        <button
          type="button"
          onClick={() => onRemove(index)}
          className="rounded-lg px-3 py-1.5 text-sm font-medium text-rose-600 transition hover:bg-rose-50"
        >
          Remove
        </button>
      </div>

      <div className="space-y-5">
        <div>
          <label className="block text-sm font-medium text-slate-700">
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
            Label
          </label>

          <input
<<<<<<< HEAD
            id={`${field.id}-label`}
=======
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
            value={field.label}
            onChange={(e) =>
              onChange(index, {
                label: e.target.value,
              })
            }
            placeholder="First Name"
            className={inputClass}
          />
        </div>

        <div>
<<<<<<< HEAD
          <label htmlFor={`${field.id}-type`} className="block text-sm font-medium text-slate-700">
=======
          <label className="block text-sm font-medium text-slate-700">
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
            Field Type
          </label>

          <select
<<<<<<< HEAD
            id={`${field.id}-type`}
=======
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
            value={field.type}
            onChange={(e) =>
              onChange(index, {
                type: e.target.value as FieldType,
              })
            }
            className={inputClass}
          >
            <option value="text">Text</option>
            <option value="textarea">Text Area</option>
            <option value="number">Number</option>
            <option value="date">Date</option>
            <option value="select">Dropdown</option>
<<<<<<< HEAD
            <option value="radio">Radio Buttons</option>
            <option value="checkbox">Checkbox (Yes/No)</option>
          </select>
        </div>

        {!["date", "select", "radio", "checkbox"].includes(field.type) && (
          <div>
            <label htmlFor={`${field.id}-placeholder`} className="block text-sm font-medium text-slate-700">
=======
          </select>
        </div>

        {field.type !== "date" && (
          <div>
            <label className="block text-sm font-medium text-slate-700">
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
              Placeholder
            </label>

            <input
<<<<<<< HEAD
              id={`${field.id}-placeholder`}
=======
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
              value={field.placeholder ?? ""}
              onChange={(e) =>
                onChange(index, {
                  placeholder: e.target.value,
                })
              }
              placeholder="Enter a value"
              className={inputClass}
            />
          </div>
        )}

<<<<<<< HEAD
        {isChoiceField && (
          <div>
            <div className="flex items-start justify-between gap-3">
              <div>
                <span className="block text-sm font-medium text-slate-700">Options</span>
                <p className="mt-1 text-xs text-slate-400">
                  Each option is a row. Press Enter to add the next one.
                </p>
              </div>

              <button
                type="button"
                onClick={() => setManageOpen(true)}
                className="shrink-0 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
              >
                Manage options…
              </button>
            </div>

            <div className="mt-3">
              <OptionListEditor
                idPrefix={field.id}
                options={field.options ?? []}
                onChange={changeOptions}
                usage={usage}
                compact
                emptyHint="No options yet. Add one below or load a saved list."
              />
            </div>

            {optionProblems.length > 0 && (
              <p className="mt-2 text-xs text-rose-700">
                <span aria-hidden>⚠ </span>This field {optionProblems.join(" ")}
              </p>
            )}

            {manageOpen && (
              <ManageOptionsModal
                fieldLabel={field.label || "Untitled Field"}
                options={field.options ?? []}
                usage={usage}
                onApply={applyFromModal}
                onClose={() => setManageOpen(false)}
              />
            )}
=======
        {field.type === "select" && (
          <div>
            <label className="block text-sm font-medium text-slate-700">
              Options
            </label>

            <p className="mt-1 text-xs text-slate-400">
              Enter one option per line.
            </p>

            <textarea
              rows={5}
              value={field.options?.join("\n") ?? ""}
              onChange={(e) =>
                onChange(index, {
                  options: e.target.value
                    .split("\n")
                    .map((option) => option.trim())
                    .filter(Boolean),
                })
              }
              placeholder={"Email\nPhone\nText"}
              className={`${inputClass} resize-y`}
            />
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
          </div>
        )}

        <label className="flex items-center gap-3">
          <input
            type="checkbox"
            checked={field.required ?? false}
            onChange={(e) =>
              onChange(index, {
                required: e.target.checked,
              })
            }
            className="h-4 w-4 rounded border-slate-300"
          />

          <span className="text-sm font-medium text-slate-700">
            Required field
          </span>
        </label>

<<<<<<< HEAD
        <LogicEditor
          field={field}
          sourceFields={availableSourceFields(allFields, index)}
          allFields={allFields}
          problems={problems}
          onChange={(logic) => onChange(index, { logic })}
        />

=======
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
        <div className="rounded-xl bg-slate-50 px-4 py-3">
          <p className="text-xs leading-5 text-slate-500">
            Changes here update this field&apos;s configuration object in React
            state. The live preview then re-renders from the updated
<<<<<<< HEAD
            configuration - try answering the preview to see conditional fields
            appear.
=======
            configuration.
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71
          </p>
        </div>
      </div>
    </div>
  );
}
"use client";

import {
  FieldConfig,
  FieldType,
} from "../components/FormRender";

type FieldEditorProps = {
  field: FieldConfig;
  index: number;
  onChange: (
    index: number,
    updates: Partial<FieldConfig>
  ) => void;
  onRemove: (index: number) => void;
};

export default function FieldEditor({
  field,
  index,
  onChange,
  onRemove,
}: FieldEditorProps) {
  const inputClass =
    "mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:ring-4 focus:ring-slate-100";

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Field {index + 1}
          </p>

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
            Label
          </label>

          <input
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
          <label className="block text-sm font-medium text-slate-700">
            Field Type
          </label>

          <select
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
          </select>
        </div>

        {field.type !== "date" && (
          <div>
            <label className="block text-sm font-medium text-slate-700">
              Placeholder
            </label>

            <input
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

        <div className="rounded-xl bg-slate-50 px-4 py-3">
          <p className="text-xs leading-5 text-slate-500">
            Changes here update this field&apos;s configuration object in React
            state. The live preview then re-renders from the updated
            configuration.
          </p>
        </div>
      </div>
    </div>
  );
}
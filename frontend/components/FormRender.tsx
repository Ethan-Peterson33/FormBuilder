"use client";

import { useMemo, useState, type FormEvent } from "react";

import { getVisibleFieldIds, prepareSubmissionData } from "@/lib/formLogic";
import type { FieldConfig, FieldType, FieldValue, FormConfig, FormValues } from "@/lib/types";

export type { FieldConfig, FieldType, FormConfig };

/** What the renderer hands back when the user submits. */
export type FormSubmitPayload = {
  /** Answers for visible fields only (hidden fields are stripped). */
  data: FormValues;
  /** Ids of fields hidden by conditional logic at submit time. */
  hiddenFields: string[];
};

type FormRendererProps = {
  config: FormConfig;
  /** Called with cleaned answers when the form is submitted. May return a promise. */
  onSubmit?: (payload: FormSubmitPayload) => void | Promise<void>;
  /** Initial answers (e.g. when re-opening a draft). */
  initialValues?: FormValues;
  submitLabel?: string;
  /** Disable inputs and the submit button (e.g. while saving). */
  disabled?: boolean;
  /** When true the form renders without a submit button (builder preview). */
  preview?: boolean;
};

const inputClass =
  "mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:ring-4 focus:ring-slate-100 disabled:bg-slate-50 disabled:text-slate-400";

export default function FormRenderer({
  config,
  onSubmit,
  initialValues,
  submitLabel = "Submit",
  disabled = false,
  preview = false,
}: FormRendererProps) {
  const [values, setValues] = useState<FormValues>(initialValues ?? {});
  const [submitting, setSubmitting] = useState(false);

  const visibleIds = useMemo(
    () => getVisibleFieldIds(config.fields, values),
    [config.fields, values]
  );

  const hiddenCount = config.fields.length - visibleIds.size;

  function setValue(fieldId: string, value: FieldValue) {
    setValues((current) => ({ ...current, [fieldId]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!onSubmit || submitting) return;

    const payload = prepareSubmissionData(config.fields, values);
    try {
      setSubmitting(true);
      await onSubmit(payload);
    } finally {
      setSubmitting(false);
    }
  }

  const isDisabled = disabled || submitting;

  function renderField(field: FieldConfig) {
    const value = values[field.id];

    switch (field.type) {
      case "text":
        return (
          <input
            id={field.id}
            name={field.id}
            className={inputClass}
            type="text"
            placeholder={field.placeholder}
            required={field.required}
            disabled={isDisabled}
            value={typeof value === "string" ? value : ""}
            onChange={(e) => setValue(field.id, e.target.value)}
          />
        );

      case "textarea":
        return (
          <textarea
            id={field.id}
            name={field.id}
            className={`${inputClass} resize-y`}
            placeholder={field.placeholder}
            required={field.required}
            disabled={isDisabled}
            rows={3}
            value={typeof value === "string" ? value : ""}
            onChange={(e) => setValue(field.id, e.target.value)}
          />
        );

      case "number":
        return (
          <input
            id={field.id}
            name={field.id}
            className={inputClass}
            type="number"
            step="any"
            placeholder={field.placeholder}
            required={field.required}
            disabled={isDisabled}
            value={typeof value === "number" || typeof value === "string" ? value : ""}
            onChange={(e) => {
              const raw = e.target.value;
              const parsed = e.target.valueAsNumber;
              setValue(field.id, raw === "" ? "" : Number.isNaN(parsed) ? raw : parsed);
            }}
          />
        );

      case "date":
        return (
          <input
            id={field.id}
            name={field.id}
            className={inputClass}
            type="date"
            required={field.required}
            disabled={isDisabled}
            value={typeof value === "string" ? value : ""}
            onChange={(e) => setValue(field.id, e.target.value)}
          />
        );

      case "select":
        return (
          <select
            id={field.id}
            name={field.id}
            className={inputClass}
            required={field.required}
            disabled={isDisabled}
            value={typeof value === "string" ? value : ""}
            onChange={(e) => setValue(field.id, e.target.value)}
          >
            <option value="">Select an option</option>

            {field.options?.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        );

      case "radio":
        return (
          <div
            className="mt-2 space-y-2"
            role="radiogroup"
            aria-labelledby={`${field.id}-label`}
            aria-required={field.required}
          >
            {field.options?.map((option) => (
              <label key={option} className="flex items-center gap-2 text-sm text-slate-800">
                <input
                  type="radio"
                  name={field.id}
                  value={option}
                  required={field.required}
                  disabled={isDisabled}
                  checked={value === option}
                  onChange={() => setValue(field.id, option)}
                  className="h-4 w-4 border-slate-300"
                />
                {option}
              </label>
            ))}
          </div>
        );

      case "checkbox":
        return (
          <input
            id={field.id}
            name={field.id}
            type="checkbox"
            required={field.required}
            disabled={isDisabled}
            checked={value === true}
            onChange={(e) => setValue(field.id, e.target.checked)}
            className="mt-2 h-4 w-4 rounded border-slate-300"
          />
        );

      default:
        return <p>Unknown field type: {field.type}</p>;
    }
  }

  if (config.fields.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-slate-400">This form has no fields yet.</p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5" noValidate={false}>
      {config.fields.map((field) => {
        if (!visibleIds.has(field.id)) return null;

        const hasLogic = Boolean(field.logic?.conditions.length);

        return (
          <div key={field.id} data-field-id={field.id}>
            <label
              id={`${field.id}-label`}
              htmlFor={field.type === "radio" ? undefined : field.id}
              className="flex items-center gap-2 text-sm font-medium text-slate-800"
            >
              <span>
                {field.label}
                {field.required && <span className="text-rose-500"> *</span>}
              </span>

              {preview && hasLogic && (
                <span
                  title="Shown because its conditions are met"
                  className="rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700"
                >
                  conditional
                </span>
              )}
            </label>

            {renderField(field)}
          </div>
        );
      })}

      {preview && hiddenCount > 0 && (
        <p className="text-xs text-slate-400">
          {hiddenCount} {hiddenCount === 1 ? "field is" : "fields are"} hidden by conditional
          logic. Answer the questions above to reveal them.
        </p>
      )}

      {!preview && (
        <div className="pt-2">
          <button
            type="submit"
            disabled={isDisabled}
            className="rounded-xl bg-slate-950 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {submitting ? "Submitting…" : submitLabel}
          </button>
        </div>
      )}
    </form>
  );
}

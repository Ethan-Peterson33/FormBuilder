"use client";

import { useMemo, useState } from "react";
import Link from "next/link";

import FormRenderer from "@/components/FormRender";
import FieldEditor from "@/components/FieldEditor";
import { findLogicProblems, validateFormLogic } from "@/lib/formLogic";
import {
  applyOptionRenames,
  renameOptionInConditions,
  sanitizeFieldsForSave,
  validateFieldOptions,
  type OptionChange,
} from "@/lib/options";
import type { ApiError, FieldConfig, FormConfig } from "@/lib/types";

type SaveState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "saved"; form: FormConfig }
  | { status: "error"; message: string };

/** Drop any conditions on `field` that point at `removedFieldId`. */
function stripConditionsFor(field: FieldConfig, removedFieldId: string | undefined): FieldConfig {
  if (!removedFieldId || !field.logic) return field;
  const conditions = field.logic.conditions.filter((c) => c.fieldId !== removedFieldId);
  if (conditions.length === field.logic.conditions.length) return field;
  return { ...field, logic: { ...field.logic, conditions } };
}

type FormBuilderProps = {
  /** When provided the builder edits this form (PUT) instead of creating a new one (POST). */
  initialForm?: FormConfig;
};

export default function FormBuilder({ initialForm }: FormBuilderProps) {
  const [formId, setFormId] = useState<string | null>(initialForm?.id ?? null);
  const [name, setName] = useState(initialForm?.name ?? "");
  const [description, setDescription] = useState(initialForm?.description ?? "");
  const [fields, setFields] = useState<FieldConfig[]>(initialForm?.fields ?? []);
  const [saveState, setSaveState] = useState<SaveState>({ status: "idle" });
  const [previewKey, setPreviewKey] = useState(0);

  const logicProblems = useMemo(
    () => [...validateFieldOptions(fields), ...validateFormLogic(fields)],
    [fields]
  );
  const structuredProblems = useMemo(() => findLogicProblems(fields), [fields]);

  const formConfig: FormConfig = {
    id: formId ?? "",
    name,
    description,
    fields,
  };

  const isEditing = formId !== null;
  const canSave =
    name.trim().length > 0 && logicProblems.length === 0 && saveState.status !== "saving";

  function addField() {
    const newField: FieldConfig = {
      id: `field-${crypto.randomUUID()}`,
      type: "text",
      label: "New Field",
      required: false,
      placeholder: "",
    };

    setFields((currentFields) => [...currentFields, newField]);
    setSaveState({ status: "idle" });
  }

  function updateField(index: number, updates: Partial<FieldConfig>) {
    setFields((currentFields) =>
      currentFields.map((field, fieldIndex) => {
        if (fieldIndex !== index) return field;
        const next = { ...field, ...updates };
        // Switching to a choice type: give the user a row to type into.
        if (
          updates.type &&
          (updates.type === "select" || updates.type === "radio") &&
          !(next.options && next.options.length > 0)
        ) {
          next.options = [""];
        }
        return next;
      })
    );
    setSaveState({ status: "idle" });
  }

  /**
   * Options changed on one field. Besides storing them, keep conditional
   * logic on later fields in step when an option is renamed, so a rule like
   * "hasPet equals Yes" follows the option to its new spelling.
   */
  function updateFieldOptions(index: number, options: string[], change: OptionChange) {
    setFields((currentFields) => {
      const target = currentFields[index];
      if (!target) return currentFields;
      let next = currentFields.map((field, fieldIndex) =>
        fieldIndex === index ? { ...field, options } : field
      );
      if (change.type === "rename" && change.from.trim() && change.to.trim()) {
        next = renameOptionInConditions(next, target.id, change.from, change.to);
      } else if (change.type === "replace" && change.renames?.length) {
        next = applyOptionRenames(next, target.id, change.renames);
      }
      return next;
    });
    setSaveState({ status: "idle" });
  }

  function removeField(index: number) {
    setFields((currentFields) => {
      const removed = currentFields[index];
      return currentFields
        .filter((_, fieldIndex) => fieldIndex !== index)
        .map((field) => stripConditionsFor(field, removed?.id));
    });
    setSaveState({ status: "idle" });
  }

  function moveField(index: number, direction: -1 | 1) {
    setFields((currentFields) => {
      const target = index + direction;
      if (target < 0 || target >= currentFields.length) return currentFields;
      const next = [...currentFields];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setSaveState({ status: "idle" });
  }

  async function saveForm() {
    if (!canSave) return;
    setSaveState({ status: "saving" });

    const url = isEditing ? `/api/forms/${encodeURIComponent(formId)}` : "/api/forms";
    const method = isEditing ? "PUT" : "POST";

    try {
      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...formConfig, fields: sanitizeFieldsForSave(fields) }),
      });

      const payload = (await response.json()) as FormConfig | ApiError;

      if (!response.ok) {
        const message =
          "error" in payload ? payload.error : `Failed to save form (${response.status})`;
        setSaveState({ status: "error", message });
        return;
      }

      const saved = payload as FormConfig;
      setFormId(saved.id);
      setFields(saved.fields);
      setSaveState({ status: "saved", form: saved });
    } catch (error) {
      console.error("Save failed", error);
      setSaveState({
        status: "error",
        message: "Could not reach the server. Check that the backend is running and try again.",
      });
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-2">
      {/* Editor */}
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-slate-950">Form Settings</h3>
            <p className="mt-1 text-sm text-slate-500">Configure your form and its fields.</p>
          </div>

          {isEditing && (
            <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
              Editing {formId}
            </span>
          )}
        </div>

        <div className="space-y-6">
          {/* Form Name */}
          <div>
            <label htmlFor="form-name" className="block text-sm font-medium text-slate-800">
              Form Name
            </label>

            <input
              id="form-name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setSaveState({ status: "idle" });
              }}
              placeholder="Resident Inspection"
              className="mt-2 w-full rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-slate-400 focus:ring-4 focus:ring-slate-100"
            />
          </div>

          {/* Description */}
          <div>
            <label htmlFor="form-description" className="block text-sm font-medium text-slate-800">
              Description
            </label>

            <textarea
              id="form-description"
              value={description}
              onChange={(e) => {
                setDescription(e.target.value);
                setSaveState({ status: "idle" });
              }}
              placeholder="Describe what this form is used for"
              rows={3}
              className="mt-2 w-full resize-y rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-slate-400 focus:ring-4 focus:ring-slate-100"
            />
          </div>

          {/* Fields */}
          <div className="border-t border-slate-100 pt-6">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h4 className="font-medium text-slate-900">Fields</h4>

                <p className="text-sm text-slate-500">
                  {fields.length} {fields.length === 1 ? "field" : "fields"}
                </p>
              </div>

              <button
                type="button"
                onClick={addField}
                className="rounded-xl bg-slate-950 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800"
              >
                + Add Field
              </button>
            </div>

            {fields.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 px-6 py-10 text-center">
                <p className="text-sm text-slate-500">No fields yet. Add your first field.</p>
              </div>
            ) : (
              <div className="space-y-4">
                {fields.map((field, index) => (
                  <FieldEditor
                    key={field.id}
                    field={field}
                    index={index}
                    allFields={fields}
                    onChange={updateField}
                    onOptionsChange={updateFieldOptions}
                    problems={structuredProblems.filter((p) => p.fieldId === field.id)}
                    onRemove={removeField}
                    onMove={moveField}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Save */}
        <div className="mt-6 space-y-3 border-t border-slate-100 pt-6">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={saveForm}
              disabled={!canSave}
              className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {saveState.status === "saving"
                ? "Saving…"
                : isEditing
                  ? "Save Changes"
                  : "Save Form"}
            </button>

            {!name.trim() && (
              <span className="text-xs text-slate-400">Enter a form name to enable saving.</span>
            )}
          </div>

          {logicProblems.length > 0 && (
            <div
              role="alert"
              className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
            >
              <p className="font-medium">Fix these before saving:</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
                {logicProblems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            </div>
          )}

          {saveState.status === "saved" && (
            <div
              role="status"
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"
            >
              <span>
                Saved <strong>{saveState.form.name}</strong> as{" "}
                <code className="rounded bg-white px-1.5 py-0.5 text-xs">{saveState.form.id}</code>
              </span>

              <span className="flex gap-3">
                <Link href={`/forms/${saveState.form.id}`} className="font-medium underline">
                  View form
                </Link>
                <Link href="/forms" className="font-medium underline">
                  All forms
                </Link>
              </span>
            </div>
          )}

          {saveState.status === "error" && (
            <div
              role="alert"
              className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
            >
              {saveState.message}
            </div>
          )}
        </div>
      </section>

      {/* Live Preview */}
      <section>
        <div className="mb-4 flex items-center justify-between">
          <p className="text-sm font-medium text-slate-500">Live Preview</p>

          <button
            type="button"
            onClick={() => setPreviewKey((k) => k + 1)}
            className="text-xs font-medium text-slate-500 underline-offset-2 hover:text-slate-900 hover:underline"
          >
            Reset answers
          </button>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          {name ? (
            <>
              <div className="mb-6">
                <h3 className="text-2xl font-semibold text-slate-950">{name}</h3>

                {description && <p className="mt-2 text-sm text-slate-500">{description}</p>}
              </div>

              <FormRenderer key={previewKey} config={formConfig} preview />
            </>
          ) : (
            <div className="py-16 text-center text-sm text-slate-400">
              Enter a form name to start previewing.
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

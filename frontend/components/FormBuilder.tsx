"use client";

import { useState } from "react";

import FormRenderer, {
  type FieldConfig,
  type FormConfig,
} from "../components/FormRender";

import FieldEditor from "@/components/FieldEditor";


export default function FormBuilder() {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [fields, setFields] = useState<FieldConfig[]>([]);


  const formConfig: FormConfig = {
    id: "",
    name,
    description,
    fields,
  };


  function addField() {
    const newField: FieldConfig = {
      id: `field-${crypto.randomUUID()}`,
      type: "text",
      label: "New Field",
      required: false,
      placeholder: "",
    };

    setFields((currentFields) => [
      ...currentFields,
      newField,
    ]);
  }


  function updateField(
    index: number,
    updates: Partial<FieldConfig>
  ) {
    setFields((currentFields) =>
      currentFields.map((field, fieldIndex) =>
        fieldIndex === index
          ? { ...field, ...updates }
          : field
      )
    );
  }


  function removeField(index: number) {
    setFields((currentFields) =>
      currentFields.filter(
        (_, fieldIndex) => fieldIndex !== index
      )
    );
  }


async function saveForm() {
  const response = await fetch("/api/forms", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(formConfig),
  });

  console.log("SAVE STATUS:", response.status);

  const text = await response.text();

  console.log("SAVE RESPONSE:", text);

  if (!response.ok) {
    throw new Error(`Failed to save form: ${response.status}`);
  }

  const savedForm = JSON.parse(text);

  console.log("Saved:", savedForm);
}

  return (
    <div className="grid gap-8 lg:grid-cols-2">

      {/* Editor */}
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">

        <div className="mb-6">
          <h3 className="text-lg font-semibold text-slate-950">
            Form Settings
          </h3>

          <p className="mt-1 text-sm text-slate-500">
            Configure your form and its fields.
          </p>
        </div>


        <div className="space-y-6">

          {/* Form Name */}
          <div>
   
            <label className="block text-sm font-medium text-slate-800">
              Form Name
            </label>

            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Resident Inspection"
              className="mt-2 w-full rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-slate-400 focus:ring-4 focus:ring-slate-100"
            />
          </div>


          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-slate-800">
              Description
            </label>

            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe what this form is used for"
              rows={3}
              className="mt-2 w-full resize-y rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-slate-400 focus:ring-4 focus:ring-slate-100"
            />
          </div>


          {/* Fields */}
          <div className="border-t border-slate-100 pt-6">

            <div className="mb-4 flex items-center justify-between">
              <div>
                <h4 className="font-medium text-slate-900">
                  Fields
                </h4>

                <p className="text-sm text-slate-500">
                  {fields.length}{" "}
                  {fields.length === 1 ? "field" : "fields"}
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
                <p className="text-sm text-slate-500">
                  No fields yet. Add your first field.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {fields.map((field, index) => (
                  <FieldEditor
                    key={field.id}
                    field={field}
                    index={index}
                    onChange={updateField}
                    onRemove={removeField}
                  />
                ))}
              </div>
            )}

          </div>
      
        </div>
                  <button
                    type="button"
                    onClick={saveForm}
                    className="mt-6 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white"
                  >
                    Save Form
          </button>
      </section>


      {/* Live Preview */}
      <section>

        <div className="mb-4">
          <p className="text-sm font-medium text-slate-500">
            Live Preview
          </p>
        </div>


        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">

          {name ? (
            <>
              <div className="mb-6">
                <h3 className="text-2xl font-semibold text-slate-950">
                  {name}
                </h3>

                {description && (
                  <p className="mt-2 text-sm text-slate-500">
                    {description}
                  </p>
                )}
              </div>

              <FormRenderer config={formConfig} />
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
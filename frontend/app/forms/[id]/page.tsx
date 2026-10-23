"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import ProcessGuide from "@/components/ProcessGuide";

import FormRenderer, {
  FormConfig,
}
 from "../../../components/FormRender";


type FieldConfig = {
  id: string;
  type: string;
  label: string;
  required?: boolean;
  placeholder?: string;
  options?: string[];
};





export default function FormPage() {

    const params = useParams();
    const id = params.id;

const [form, setForm] = useState<FormConfig | null>(null);
const [loading, setLoading] = useState(true);
const [error, setError] = useState<string | null>(null);

    useEffect(() => {
  async function loadForm() {
    console.log("Loading form ID:", id);

    const response = await fetch(`/api/forms/${id}`);

    console.log("Response status:", response.status);

    const data = await response.json();

    console.log("Form response:", data);

    if (!response.ok) {
      setError(data.error || "Failed to load form");
      setLoading(false);
      return;
    }

    setForm(data);
    setLoading(false);
  }

  loadForm();
}, [id]);

                if (loading) {
                return (
                    <div className="space-y-4">
                    <div className="h-8 w-48 animate-pulse rounded bg-slate-200" />

                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                        {[1, 2, 3].map((item) => (
                        <div
                            key={item}
                            className="h-40 animate-pulse rounded-xl border border-slate-200 bg-white"
                        />
                        ))}
                    </div>
                    </div>
                );
                }

            if (error) {
            return <p>Error: {error}</p>;
            }

            if (!form) {
            return <p>Form not found.</p>;
            }

return (
  <div className="mx-auto max-w-3xl">
    <div className="mb-8">
      <p className="text-sm font-medium text-slate-500">
        Form Preview
      </p>

      <h2 className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">
        {form.name}
      </h2>

      {form.description && (
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          {form.description}
        </p>
      )}
    </div>
<ProcessGuide
  title="Rendering a form from configuration"
  description="The actual inputs on this page were not written specifically for this form. They are generated from JSON configuration."
  updated="today"
  steps={[
    "The form ID is taken from the URL.",
    "Next.js requests that form configuration from FastAPI.",
    "The configuration is passed into FormRenderer.",
    "FormRenderer examines each field type and chooses the correct React control.",
  ]}
/>
    <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
      <FormRenderer config={form} />
    </div>
  </div>
);

    }
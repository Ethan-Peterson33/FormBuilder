import FormBuilder from "@/components/FormBuilder";
import ProcessGuide from "@/components/ProcessGuide";
import type { FormConfig } from "@/lib/types";
import { getBackendUrl } from "@/lib/server/backend";
import Link from "next/link";

type PageProps = {
  searchParams: Promise<{ id?: string }>;
};

async function loadForm(id: string): Promise<FormConfig | null> {
  try {
    const response = await fetch(`${getBackendUrl()}/forms/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as FormConfig;
  } catch (error) {
    console.error("Failed to load form for editing", error);
    return null;
  }
}

export default async function FormBuilderPage({ searchParams }: PageProps) {
  const { id } = await searchParams;
  const existingForm = id ? await loadForm(id) : null;
  const isEditing = Boolean(existingForm);

  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm font-medium text-slate-500">Forms</p>

        <h2 className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">
          {isEditing ? `Edit: ${existingForm!.name}` : "Form Builder"}
        </h2>

        {id && !existingForm && (
          <div
            role="alert"
            className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
          >
            Form <code>{id}</code> could not be loaded. You are creating a new form instead.{" "}
            <Link href="/forms" className="font-medium underline">
              Back to forms
            </Link>
          </div>
        )}

        <ProcessGuide
          title="Building configuration instead of HTML"
          description="The builder does not directly create the final form markup. It constructs a FormConfig object that the same FormRenderer can immediately preview."
          updated="today"
          steps={[
            "Form name, description, and fields are stored in React state.",
            "That state is assembled into a FormConfig object.",
            "The FormConfig is passed to FormRenderer for the live preview.",
            "Save Form sends the configuration to /api/forms, which Next.js proxies to FastAPI for persistence in forms.json.",
          ]}
        />

        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Build a dynamic form, configure its fields, and preview the result in real time.
        </p>
      </div>

      <FormBuilder key={existingForm?.id ?? "new"} initialForm={existingForm ?? undefined} />
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import ProcessGuide from "@/components/ProcessGuide";

type FormConfig = {
  id: string;
  name: string;
  description?: string;
};

export default function FormsPage() {
    const [forms, setForms] = useState<FormConfig[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function loadForms() {
            const response = await fetch("/api/forms");
            const data = await response.json();

            setForms(data);
            setLoading(false);
        }

        loadForms();
    }, []);

if (loading) {
  return <p>Loading forms...</p>;
}


return (
  <div className="space-y-8">

    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h2 className="text-3xl font-semibold tracking-tight text-slate-950">
          Forms
        </h2>

        <p className="mt-2 text-sm text-slate-500">
          Build, manage, and preview your dynamic forms.
        </p>
      </div>

      <Link
        href="/forms/builder"
        className="inline-flex items-center justify-center rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800"
      >
        + New Form
      </Link>
    </div>
<ProcessGuide
  title="Loading saved form definitions"
  description="This screen demonstrates how the frontend can display forms without knowing their structure ahead of time."
  updated="today"
  steps={[
    "The browser requests /api/forms from the Next.js application.",
    "The Next.js server proxies that request to the private FastAPI service.",
    "FastAPI reads the form definitions from forms.json.",
    "React receives the returned array and creates one card for each saved form.",
  ]}
/>
    {forms.length === 0 ? (
      <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
        <h3 className="text-lg font-semibold text-slate-900">
          No forms yet
        </h3>

        <p className="mt-2 text-sm text-slate-500">
          Create your first form to get started.
        </p>

        <Link
          href="/forms/builder"
          className="mt-6 inline-flex rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-800"
        >
          Create Form
        </Link>
      </div>
    ) : (
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {forms.map((form) => (
          <div
            key={form.id}
            className="group flex min-h-52 flex-col rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition duration-200 hover:-translate-y-1 hover:border-slate-300 hover:shadow-lg"
          >
            <div className="flex-1">
              <div className="mb-4 flex items-start justify-between">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-100 text-sm font-semibold text-slate-700">
                  
                </div>

                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
                  Active
                </span>
              </div>

              <h3 className="text-lg font-semibold text-slate-950">
                {form.name}
              </h3>

              {form.description && (
                <p className="mt-2 line-clamp-3 text-sm leading-6 text-slate-500">
                  {form.description}
                </p>
              )}
            </div>

            <div className="mt-6 flex items-center justify-between border-t border-slate-100 pt-4">
              <span className="text-xs text-slate-400">
                {form.id}
              </span>

              <Link
                href={`/forms/${form.id}`}
                className="text-sm font-medium text-slate-900 transition group-hover:translate-x-0.5"
              >
                View form →
              </Link>
            </div>
          </div>
        ))}
      </div>
    )}
  </div>
);
}
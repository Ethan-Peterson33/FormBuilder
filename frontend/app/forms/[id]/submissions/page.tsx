"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import SubmissionsTable from "@/components/SubmissionsTable";
import type { ApiError, FormConfig, Submission, SubmissionList } from "@/lib/types";

export default function FormSubmissionsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [form, setForm] = useState<FormConfig | null>(null);
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [formResponse, submissionsResponse] = await Promise.all([
          fetch(`/api/forms/${encodeURIComponent(id)}`),
          fetch(`/api/forms/${encodeURIComponent(id)}/submissions`),
        ]);

        const formData = (await formResponse.json()) as FormConfig | ApiError;
        if (!formResponse.ok) {
          setError((formData as ApiError).error || "Failed to load form");
          return;
        }

        const submissionData = (await submissionsResponse.json()) as SubmissionList | ApiError;
        if (!submissionsResponse.ok) {
          setError((submissionData as ApiError).error || "Failed to load submissions");
          return;
        }

        setForm(formData as FormConfig);
        setSubmissions((submissionData as SubmissionList).items);
      } catch (err) {
        console.error(err);
        setError("Could not reach the server.");
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [id]);

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-64 animate-pulse rounded bg-slate-200" />
        <div className="h-48 animate-pulse rounded-2xl border border-slate-200 bg-white" />
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
        {error}{" "}
        <Link href="/forms" className="font-medium underline">
          Back to forms
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-medium text-slate-500">
            <Link href="/forms" className="hover:text-slate-900">
              Forms
            </Link>{" "}
            / {form?.name}
          </p>

          <h2 className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">Submissions</h2>

          <p className="mt-2 text-sm text-slate-500">
            {submissions.length} {submissions.length === 1 ? "response" : "responses"} recorded for{" "}
            <strong>{form?.name}</strong>.
          </p>
        </div>

        <Link
          href={`/forms/${encodeURIComponent(id)}`}
          className="inline-flex shrink-0 items-center justify-center rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800"
        >
          Open form
        </Link>
      </div>

      <SubmissionsTable submissions={submissions} form={form} />
    </div>
  );
}

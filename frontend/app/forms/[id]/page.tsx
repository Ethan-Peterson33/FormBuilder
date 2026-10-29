"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import ProcessGuide from "@/components/ProcessGuide";
import FormRenderer, { type FormSubmitPayload } from "@/components/FormRender";
import type {
  ApiError,
  FormConfig,
  Submission,
  SubmissionCreate,
  SubmissionValidationError,
} from "@/lib/types";

type SubmitState =
  | { status: "idle" }
  | { status: "submitting" }
  | { status: "success"; submission: Submission }
  | { status: "error"; message: string; fieldErrors?: Record<string, string> };

export default function FormPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [form, setForm] = useState<FormConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitState, setSubmitState] = useState<SubmitState>({ status: "idle" });
  const [renderKey, setRenderKey] = useState(0);

  useEffect(() => {
    async function loadForm() {
      try {
        const response = await fetch(`/api/forms/${encodeURIComponent(id)}`);
        const data = (await response.json()) as FormConfig | ApiError;

        if (!response.ok) {
          setError((data as ApiError).error || "Failed to load form");
          return;
        }

        setForm(data as FormConfig);
      } catch (err) {
        console.error(err);
        setError("Could not reach the server.");
      } finally {
        setLoading(false);
      }
    }

    loadForm();
  }, [id]);

  const handleSubmit = useCallback(
    async (payload: FormSubmitPayload) => {
      setSubmitState({ status: "submitting" });

      const body: SubmissionCreate = {
        data: payload.data,
        source: "web",
        metadata: {
          pageUrl: typeof window !== "undefined" ? window.location.pathname : undefined,
          clientHiddenFields: payload.hiddenFields,
        },
      };

      try {
        const response = await fetch(`/api/forms/${encodeURIComponent(id)}/submissions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        const result = (await response.json()) as Submission | SubmissionValidationError;

        if (!response.ok) {
          const failure = result as SubmissionValidationError;
          setSubmitState({
            status: "error",
            message: failure.error || `Submission failed (${response.status})`,
            fieldErrors: failure.details?.errors,
          });
          return;
        }

        setSubmitState({ status: "success", submission: result as Submission });
      } catch (err) {
        console.error(err);
        setSubmitState({
          status: "error",
          message: "Could not reach the server. Your answers were not saved.",
        });
      }
    },
    [id]
  );

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-48 animate-pulse rounded bg-slate-200" />
        <div className="h-64 animate-pulse rounded-2xl border border-slate-200 bg-white" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
      >
        {error}{" "}
        <Link href="/forms" className="font-medium underline">
          Back to forms
        </Link>
      </div>
    );
  }

  if (!form) {
    return <p>Form not found.</p>;
  }

  const fieldLabel = (fieldId: string) =>
    form.fields.find((field) => field.id === fieldId)?.label ?? fieldId;

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-medium text-slate-500">Form</p>

          <h2 className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">{form.name}</h2>

          {form.description && (
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">{form.description}</p>
          )}
        </div>

        <div className="flex shrink-0 gap-2">
          <Link
            href={`/forms/${encodeURIComponent(form.id)}/submissions`}
            className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
          >
            Submissions
          </Link>
          <Link
            href={`/forms/builder?id=${encodeURIComponent(form.id)}`}
            className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
          >
            Edit
          </Link>
        </div>
      </div>

      <ProcessGuide
        title="Rendering a form from configuration"
        description="The inputs on this page were not written specifically for this form. They are generated from JSON configuration, and conditional fields appear as you answer."
        updated="today"
        steps={[
          "The form ID is taken from the URL.",
          "Next.js requests that form configuration from FastAPI.",
          "FormRenderer builds each input and evaluates conditional logic on every change.",
          "On submit, visible answers are posted to /api/forms/{id}/submissions and stored with metadata.",
        ]}
      />

      <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        {submitState.status === "success" ? (
          <div role="status" className="text-center">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
              ✓
            </div>

            <h3 className="mt-4 text-xl font-semibold text-slate-950">Thank you!</h3>

            <p className="mt-2 text-sm text-slate-500">
              Your response was recorded at{" "}
              {new Date(submitState.submission.metadata.submittedAt).toLocaleString()}.
            </p>

            <p className="mt-1 text-xs text-slate-400">
              Reference: <code>{submitState.submission.id}</code>
            </p>

            <div className="mt-6 flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={() => {
                  setSubmitState({ status: "idle" });
                  setRenderKey((k) => k + 1);
                }}
                className="rounded-xl bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
              >
                Submit another response
              </button>

              <Link
                href={`/forms/${encodeURIComponent(form.id)}/submissions`}
                className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                View submissions
              </Link>
            </div>
          </div>
        ) : (
          <>
            {submitState.status === "error" && (
              <div
                role="alert"
                className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
              >
                <p className="font-medium">{submitState.message}</p>
                {submitState.fieldErrors && (
                  <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
                    {Object.entries(submitState.fieldErrors).map(([fieldId, message]) => (
                      <li key={fieldId}>
                        <strong>{fieldLabel(fieldId)}</strong> {message}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <FormRenderer
              key={renderKey}
              config={form}
              onSubmit={handleSubmit}
              disabled={submitState.status === "submitting"}
            />
          </>
        )}
      </div>
    </div>
  );
}

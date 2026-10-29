"use client";

import { useMemo, useState } from "react";

import type { FieldConfig, FieldValue, FormConfig, Submission } from "@/lib/types";

type SubmissionsTableProps = {
  submissions: Submission[];
  /** When provided, columns follow the form's field order and use labels. */
  form?: FormConfig | null;
};

export function formatValue(value: FieldValue, field?: FieldConfig): string {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (field?.type === "date" && typeof value === "string") {
    const [y, m, d] = value.split("-");
    if (y && m && d) return `${m}/${d}/${y}`;
  }
  return String(value);
}

export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/** Client-side search across every answer and the metadata. */
export function filterSubmissions(submissions: Submission[], query: string): Submission[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return submissions;
  return submissions.filter((submission) => {
    const haystack = [
      submission.id,
      submission.formName,
      submission.metadata.submittedBy ?? "",
      submission.metadata.submittedAt,
      ...Object.values(submission.data).map((v) => (v === null || v === undefined ? "" : String(v))),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}

export default function SubmissionsTable({ submissions, form }: SubmissionsTableProps) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const columns = useMemo<FieldConfig[]>(() => {
    if (form) return form.fields;
    // Without a form definition, derive columns from the data keys seen.
    const seen = new Map<string, FieldConfig>();
    for (const submission of submissions) {
      for (const key of Object.keys(submission.data)) {
        if (!seen.has(key)) seen.set(key, { id: key, type: "text", label: key });
      }
    }
    return [...seen.values()];
  }, [form, submissions]);

  const filtered = useMemo(() => filterSubmissions(submissions, query), [submissions, query]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter submissions…"
          aria-label="Filter submissions"
          className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm shadow-sm outline-none transition focus:border-slate-400 focus:ring-4 focus:ring-slate-100 sm:max-w-xs"
        />

        <p className="text-sm text-slate-500">
          Showing {filtered.length} of {submissions.length}
        </p>
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center text-sm text-slate-500">
          {submissions.length === 0 ? "No submissions yet." : "No submissions match your filter."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="px-4 py-3">
                  Submitted
                </th>
                {!form && (
                  <th scope="col" className="px-4 py-3">
                    Form
                  </th>
                )}
                {columns.map((column) => (
                  <th
                    key={column.id}
                    scope="col"
                    title={column.label || column.id}
                    className="max-w-[14rem] truncate px-4 py-3"
                  >
                    {column.label || column.id}
                  </th>
                ))}
                <th scope="col" className="px-4 py-3">
                  <span className="sr-only">Details</span>
                </th>
              </tr>
            </thead>

            <tbody className="divide-y divide-slate-100">
              {filtered.map((submission) => {
                const isOpen = expanded === submission.id;
                const hidden = new Set(submission.metadata.hiddenFields);
                return (
                  <SubmissionRow
                    key={submission.id}
                    submission={submission}
                    columns={columns}
                    showForm={!form}
                    hidden={hidden}
                    isOpen={isOpen}
                    onToggle={() => setExpanded(isOpen ? null : submission.id)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SubmissionRow({
  submission,
  columns,
  showForm,
  hidden,
  isOpen,
  onToggle,
}: {
  submission: Submission;
  columns: FieldConfig[];
  showForm: boolean;
  hidden: Set<string>;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const span = columns.length + 2 + (showForm ? 1 : 0);
  const { submittedAt, submittedBy, source, userAgent, ipAddress, hiddenFields, ...extras } =
    submission.metadata;

  return (
    <>
      <tr className="align-top hover:bg-slate-50/60">
        <td className="whitespace-nowrap px-4 py-3 text-slate-700">
          {formatTimestamp(submittedAt)}
          {submittedBy && <div className="text-xs text-slate-400">{submittedBy}</div>}
        </td>

        {showForm && <td className="px-4 py-3 text-slate-700">{submission.formName}</td>}

        {columns.map((column) => (
          <td key={column.id} className="max-w-xs truncate px-4 py-3 text-slate-700">
            {hidden.has(column.id) ? (
              <span className="text-xs italic text-slate-400" title="Hidden by conditional logic">
                hidden
              </span>
            ) : (
              formatValue(submission.data[column.id], column)
            )}
          </td>
        ))}

        <td className="whitespace-nowrap px-4 py-3 text-right">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={isOpen}
            className="text-xs font-medium text-slate-500 hover:text-slate-900"
          >
            {isOpen ? "Hide details" : "Details"}
          </button>
        </td>
      </tr>

      {isOpen && (
        <tr className="bg-slate-50/60">
          <td colSpan={span} className="px-4 py-4">
            <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
              <Meta label="Submission ID" value={submission.id} mono />
              <Meta label="Form ID" value={submission.formId} mono />
              <Meta label="Source" value={source} />
              <Meta label="IP address" value={ipAddress ?? "—"} />
              <Meta label="User agent" value={userAgent ?? "—"} />
              <Meta
                label="Hidden fields"
                value={hiddenFields.length ? hiddenFields.join(", ") : "none"}
              />
              {Object.entries(extras).map(([key, value]) => (
                <Meta
                  key={key}
                  label={key}
                  value={typeof value === "string" ? value : JSON.stringify(value)}
                />
              ))}
            </dl>
          </td>
        </tr>
      )}
    </>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="font-medium uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className={`mt-0.5 break-all text-slate-700 ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

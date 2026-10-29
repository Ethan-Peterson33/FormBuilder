"use client";

import { useEffect, useState } from "react";

import SubmissionsTable from "@/components/SubmissionsTable";
import type { ApiError, Submission, SubmissionList } from "@/lib/types";

export default function AllSubmissionsPage() {
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const response = await fetch("/api/submissions");
        const data = (await response.json()) as SubmissionList | ApiError;
        if (!response.ok) {
          setError((data as ApiError).error || "Failed to load submissions");
          return;
        }
        setSubmissions((data as SubmissionList).items);
      } catch (err) {
        console.error(err);
        setError("Could not reach the server.");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return <p className="text-sm text-slate-500">Loading submissions...</p>;
  }

  if (error) {
    return (
      <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-3xl font-semibold tracking-tight text-slate-950">All Submissions</h2>
        <p className="mt-2 text-sm text-slate-500">
          Every response collected across all forms, newest first.
        </p>
      </div>

      <SubmissionsTable submissions={submissions} />
    </div>
  );
}

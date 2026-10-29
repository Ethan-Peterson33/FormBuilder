import Link from "next/link";

import { getBackendUrl } from "@/lib/server/backend";
import type { FormConfig } from "@/lib/types";

type Stats = {
  forms: number;
  fields: number;
  withLogic: number;
  submissions: number | null;
  optionLists: number | null;
  backendOnline: boolean;
};

/**
 * Server component: gather a few headline numbers for the dashboard. Any
 * failure degrades to "offline" rather than breaking the page.
 */
async function loadStats(): Promise<Stats> {
  const base = getBackendUrl();
  try {
    const [formsResponse, submissionsResponse, listsResponse] = await Promise.all([
      fetch(`${base}/forms`, { cache: "no-store" }),
      fetch(`${base}/submissions?limit=0`, { cache: "no-store" }).catch(() => null),
      fetch(`${base}/option-lists`, { cache: "no-store" }).catch(() => null),
    ]);

    if (!formsResponse.ok) {
      return { forms: 0, fields: 0, withLogic: 0, submissions: null, optionLists: null, backendOnline: false };
    }

    const forms = (await formsResponse.json()) as FormConfig[];
    const fields = forms.reduce((total, form) => total + (form.fields?.length ?? 0), 0);
    const withLogic = forms.filter((form) =>
      form.fields?.some((field) => "logic" in field && field.logic)
    ).length;

    let submissions: number | null = null;
    if (submissionsResponse?.ok) {
      const payload = (await submissionsResponse.json()) as { total?: number };
      submissions = typeof payload.total === "number" ? payload.total : null;
    }

    let optionLists: number | null = null;
    if (listsResponse?.ok) {
      const payload = (await listsResponse.json()) as unknown[];
      optionLists = Array.isArray(payload) ? payload.length : null;
    }

    return { forms: forms.length, fields, withLogic, submissions, optionLists, backendOnline: true };
  } catch {
    return { forms: 0, fields: 0, withLogic: 0, submissions: null, optionLists: null, backendOnline: false };
  }
}

const features = [
  {
    title: "Forms",
    eyebrow: "Browse & fill",
    description:
      "See every saved form, open it to fill it in, and review the submissions collected so far.",
    href: "/forms",
    cta: "Go to forms",
    accent: "from-sky-500 to-blue-600",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-6 w-6">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 6h10M8 12h10M8 18h6M4 6h.01M4 12h.01M4 18h.01" />
      </svg>
    ),
  },
  {
    title: "Form Builder",
    eyebrow: "Design",
    description:
      "Compose fields, set validation, and add conditional logic so questions appear only when earlier answers call for them.",
    href: "/forms/builder",
    cta: "Open the builder",
    accent: "from-violet-500 to-fuchsia-600",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-6 w-6">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
      </svg>
    ),
  },
  {
    title: "Option Library",
    eyebrow: "Reuse",
    description:
      "Keep named lists of choices - contact methods, ratings, regions - and load them into any dropdown or radio field.",
    href: "/forms/option-lists",
    cta: "Manage option lists",
    accent: "from-emerald-500 to-teal-600",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-6 w-6">
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h10" />
        <circle cx="19" cy="18" r="2" />
      </svg>
    ),
  },
];

const steps = [
  {
    title: "Build",
    text: "Add fields in the builder. Choose a type, mark it required, and optionally attach logic that depends on earlier answers.",
  },
  {
    title: "Save",
    text: "The form configuration is stored as JSON through the API, ready to be rendered anywhere.",
  },
  {
    title: "Collect",
    text: "Share the form. Each submission is saved with metadata such as who submitted it and when.",
  },
];

export default async function HomePage() {
  const stats = await loadStats();

  return (
    <main className="flex-1 bg-slate-50 text-slate-900">
      {/* Hero */}
      <section className="relative overflow-hidden border-b border-slate-200 bg-white">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,_rgba(59,130,246,0.12),_transparent_55%),radial-gradient(ellipse_at_bottom_left,_rgba(168,85,247,0.10),_transparent_55%)]"
        />

        <div className="relative mx-auto flex max-w-6xl flex-col gap-10 px-6 py-20 lg:flex-row lg:items-center lg:py-28">
          <div className="max-w-2xl">
            <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 shadow-sm">
              <span
                className={`h-2 w-2 rounded-full ${stats.backendOnline ? "bg-emerald-500" : "bg-rose-500"}`}
              />
              {stats.backendOnline ? "Form service online" : "Form service offline"}
            </span>

            <h1 className="mt-6 text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl">
              Build forms once.
              <br />
              <span className="bg-gradient-to-r from-blue-600 to-violet-600 bg-clip-text text-transparent">
                Collect answers everywhere.
              </span>
            </h1>

            <p className="mt-6 text-lg leading-8 text-slate-600">
              Form Studio is the internal tool for designing dynamic forms, adding conditional
              logic, and capturing submissions — all driven by configuration, not code.
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/forms/builder"
                className="inline-flex items-center justify-center rounded-xl bg-slate-950 px-5 py-3 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800"
              >
                Create a form
              </Link>

              <Link
                href="/forms"
                className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-5 py-3 text-sm font-medium text-slate-900 shadow-sm transition hover:border-slate-300 hover:bg-slate-50"
              >
                Browse forms
              </Link>
            </div>
          </div>

          {/* Stats */}
          <dl className="grid flex-1 grid-cols-2 gap-4 lg:max-w-sm">
            <StatCard label="Saved forms" value={stats.forms} />
            <StatCard label="Total fields" value={stats.fields} />
            <StatCard
              label="Option lists"
              value={stats.optionLists ?? "—"}
              hint={stats.optionLists === null ? "Not available" : undefined}
            />
            <StatCard
              label="Submissions"
              value={stats.submissions ?? "—"}
              hint={stats.submissions === null ? "Not available" : undefined}
            />
          </dl>
        </div>
      </section>

      {/* Primary destinations */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <div className="mb-8">
          <p className="text-sm font-medium text-slate-500">Where do you want to go?</p>
          <h2 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">
            Pick a workspace
          </h2>
        </div>

        <div className="grid gap-6 md:grid-cols-3">
          {features.map((feature) => (
            <Link
              key={feature.href}
              href={feature.href}
              className="group relative flex flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white p-8 shadow-sm transition duration-200 hover:-translate-y-1 hover:border-slate-300 hover:shadow-xl"
            >
              <div
                className={`inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br ${feature.accent} text-white shadow-md`}
              >
                {feature.icon}
              </div>

              <p className="mt-6 text-xs font-semibold uppercase tracking-wide text-slate-400">
                {feature.eyebrow}
              </p>

              <h3 className="mt-1 text-xl font-semibold text-slate-950">{feature.title}</h3>

              <p className="mt-3 flex-1 text-sm leading-6 text-slate-600">{feature.description}</p>

              <span className="mt-6 inline-flex items-center gap-1 text-sm font-medium text-slate-900">
                {feature.cta}
                <span aria-hidden className="transition group-hover:translate-x-1">
                  →
                </span>
              </span>
            </Link>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section className="border-t border-slate-200 bg-white">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <div className="mb-10">
            <p className="text-sm font-medium text-slate-500">How it works</p>
            <h2 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">
              From idea to submissions in three steps
            </h2>
          </div>

          <ol className="grid gap-8 md:grid-cols-3">
            {steps.map((step, index) => (
              <li key={step.title} className="relative">
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-950 text-sm font-semibold text-white">
                  {index + 1}
                </div>
                <h3 className="mt-4 text-lg font-semibold text-slate-950">{step.title}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600">{step.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <footer className="border-t border-slate-200">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-6 py-8 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <span>Form Studio · internal tools</span>
          <span className="flex gap-4">
            <Link href="/forms" className="hover:text-slate-900">
              Forms
            </Link>
            <Link href="/forms/builder" className="hover:text-slate-900">
              Builder
            </Link>
            <Link href="/tools/pdf" className="hover:text-slate-900">
              PDF export test
            </Link>
          </span>
        </div>
      </footer>
    </main>
  );
}

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: number | string;
  hint?: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white/80 p-5 shadow-sm backdrop-blur">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-2 text-3xl font-semibold tracking-tight text-slate-950">{value}</dd>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import OptionListForm from "@/components/OptionListForm";
import {
  createOptionList,
  deleteOptionList,
  fetchOptionLists,
  updateOptionList,
} from "@/lib/optionListsApi";
import {
  draftFromList,
  draftProblems,
  draftToInput,
  emptyDraft,
  findListUsage,
  isDraftDirty,
  searchOptionLists,
  sortLists,
  type ListDraft,
} from "@/lib/optionLists";
import type { ApiError, FormConfig, OptionList } from "@/lib/types";

type OptionLibraryProps = {
  /** List to open initially (e.g. from `?id=` in the URL). */
  initialSelectedId?: string;
};

type Selection = { kind: "none" } | { kind: "list"; id: string } | { kind: "new" };

type Notice = { tone: "success" | "error"; text: string } | null;

const NEW_KEY = "new";

const primaryButton =
  "rounded-xl bg-slate-950 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300";
const secondaryButton =
  "rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50";
const saveButton =
  "rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300";
const dangerButton =
  "rounded-xl px-3 py-2 text-sm font-medium text-rose-600 transition hover:bg-rose-50 disabled:opacity-50";

function formatDate(iso: string | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export default function OptionLibrary({ initialSelectedId }: OptionLibraryProps) {
  const [lists, setLists] = useState<OptionList[]>([]);
  const [forms, setForms] = useState<FormConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<Selection>(
    initialSelectedId ? { kind: "list", id: initialSelectedId } : { kind: "none" }
  );
  const [drafts, setDrafts] = useState<Record<string, ListDraft>>({ [NEW_KEY]: emptyDraft() });
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  // -- data -------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchOptionLists(),
      // Forms are only used for the "where used" panel; failure is non-fatal.
      fetch("/api/forms", { cache: "no-store" })
        .then(async (response) => {
          const data = (await response.json()) as FormConfig[] | ApiError;
          return response.ok && Array.isArray(data) ? data : [];
        })
        .catch(() => [] as FormConfig[]),
    ])
      .then(([listResult, formResult]) => {
        if (cancelled) return;
        setLists(sortLists(listResult));
        setForms(formResult);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : "Could not load option lists.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadAttempt]);

  const reload = useCallback(() => {
    setLoading(true);
    setLoadError(null);
    setLoadAttempt((attempt) => attempt + 1);
  }, []);

  // -- derived ----------------------------------------------------------------

  const filtered = useMemo(() => searchOptionLists(lists, query), [lists, query]);

  const selectedList =
    selection.kind === "list" ? (lists.find((list) => list.id === selection.id) ?? null) : null;

  const draftKey = selection.kind === "list" ? selection.id : NEW_KEY;
  const draft: ListDraft | null =
    selection.kind === "none"
      ? null
      : (drafts[draftKey] ?? (selectedList ? draftFromList(selectedList) : drafts[NEW_KEY]));

  const dirty = selectedList && draft ? isDraftDirty(draft, selectedList) : false;
  const problems = draft ? draftProblems(draft) : [];

  const usage = useMemo(
    () => (selectedList ? findListUsage(selectedList, forms) : []),
    [selectedList, forms]
  );

  function updateDraft(partial: Partial<ListDraft>) {
    if (!draft) return;
    setDrafts((current) => ({ ...current, [draftKey]: { ...draft, ...partial } }));
    setNotice(null);
  }

  function discardDraft(key: string) {
    setDrafts((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function select(next: Selection) {
    setSelection(next);
    setNotice(null);
    setConfirmingDelete(false);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      if (next.kind === "list") url.searchParams.set("id", next.id);
      else url.searchParams.delete("id");
      window.history.replaceState(null, "", url);
    }
  }

  // -- actions ----------------------------------------------------------------

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setNotice(null);
    try {
      await action();
    } catch (error) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "Something went wrong.",
      });
    } finally {
      setBusy(false);
    }
  }

  function createList() {
    if (!draft) return;
    run(async () => {
      const created = await createOptionList(draftToInput(draft));
      setLists((current) => sortLists([...current, created]));
      setDrafts((current) => ({ ...current, [NEW_KEY]: emptyDraft() }));
      select({ kind: "list", id: created.id });
      setNotice({ tone: "success", text: `Created "${created.name}".` });
    });
  }

  function saveChanges() {
    if (!selectedList || !draft) return;
    run(async () => {
      const updated = await updateOptionList(selectedList.id, draftToInput(draft));
      setLists((current) => sortLists(current.map((list) => (list.id === updated.id ? updated : list))));
      discardDraft(updated.id);
      setNotice({
        tone: "success",
        text: `Saved "${updated.name}". Forms that already copied these options are not affected.`,
      });
    });
  }

  function duplicateList() {
    if (!selectedList) return;
    run(async () => {
      const created = await createOptionList({
        name: `${selectedList.name} (copy)`,
        description: selectedList.description ?? "",
        options: [...selectedList.options],
      });
      setLists((current) => sortLists([...current, created]));
      select({ kind: "list", id: created.id });
      setNotice({ tone: "success", text: `Duplicated as "${created.name}".` });
    });
  }

  function removeList() {
    if (!selectedList) return;
    run(async () => {
      await deleteOptionList(selectedList.id);
      setLists((current) => current.filter((list) => list.id !== selectedList.id));
      discardDraft(selectedList.id);
      setConfirmingDelete(false);
      select({ kind: "none" });
      setNotice({ tone: "success", text: `Deleted "${selectedList.name}".` });
    });
  }

  // -- render -----------------------------------------------------------------

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-64 animate-pulse rounded bg-slate-200" />
        <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
          <div className="h-64 animate-pulse rounded-2xl border border-slate-200 bg-white" />
          <div className="h-64 animate-pulse rounded-2xl border border-slate-200 bg-white" />
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div
        role="alert"
        className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
      >
        {loadError}{" "}
        <button type="button" onClick={reload} className="font-medium underline">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-3xl font-semibold tracking-tight text-slate-950">Option Library</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
            Named lists of choices you can load into any dropdown or radio field. Loading copies
            the options into the form, so editing a list here never changes existing forms.
          </p>
        </div>

        <button
          type="button"
          onClick={() => select({ kind: "new" })}
          className="inline-flex shrink-0 items-center justify-center rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800"
        >
          + New list
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
        {/* Sidebar: search + lists */}
        <aside className="space-y-3">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search lists and options…"
            aria-label="Search option lists"
            className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm shadow-sm outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:ring-4 focus:ring-slate-100"
          />

          <p className="px-1 text-xs text-slate-500">
            {query
              ? `${filtered.length} of ${lists.length} ${lists.length === 1 ? "list" : "lists"}`
              : `${lists.length} ${lists.length === 1 ? "list" : "lists"}`}
          </p>

          {lists.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
              <p className="text-sm font-medium text-slate-900">No saved lists yet</p>
              <p className="mt-1 text-xs text-slate-500">
                Create one here, or save a field&apos;s options from the form builder.
              </p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-500">
              No lists match “{query}”.
            </div>
          ) : (
            <nav aria-label="Saved option lists">
              <ul className="space-y-2">
                {filtered.map((list) => {
                  const active = selection.kind === "list" && selection.id === list.id;
                  const hasDraft = Boolean(drafts[list.id]);
                  return (
                    <li key={list.id}>
                      <button
                        type="button"
                        onClick={() => select({ kind: "list", id: list.id })}
                        aria-current={active ? "true" : undefined}
                        className={`block w-full rounded-2xl border p-4 text-left transition ${
                          active
                            ? "border-slate-900 bg-white shadow-sm"
                            : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm"
                        }`}
                      >
                        <span className="flex items-start justify-between gap-2">
                          <span className="truncate text-sm font-semibold text-slate-950">
                            {list.name}
                          </span>
                          <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">
                            {list.options.length}
                          </span>
                        </span>
                        <span className="mt-1 block truncate text-xs text-slate-500">
                          {list.options.join(" · ")}
                        </span>
                        {hasDraft && (
                          <span className="mt-1.5 inline-block rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">
                            Unsaved changes
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </nav>
          )}
        </aside>

        {/* Detail / editor */}
        <section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
          {notice && (
            <div
              role={notice.tone === "error" ? "alert" : "status"}
              className={`mb-6 rounded-xl border px-4 py-3 text-sm ${
                notice.tone === "error"
                  ? "border-rose-200 bg-rose-50 text-rose-800"
                  : "border-emerald-200 bg-emerald-50 text-emerald-800"
              }`}
            >
              {notice.text}
            </div>
          )}

          {selection.kind === "none" && (
            <div className="py-16 text-center">
              <p className="text-sm font-medium text-slate-900">
                {lists.length === 0 ? "Create your first list" : "Select a list to view or edit it"}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                Lists appear in the form builder under “Manage options…” on dropdown and radio
                fields.
              </p>
              <button
                type="button"
                onClick={() => select({ kind: "new" })}
                className={`${primaryButton} mt-6`}
              >
                + New list
              </button>
            </div>
          )}

          {selection.kind === "list" && !selectedList && (
            <div role="alert" className="text-sm text-slate-600">
              That list could not be found. It may have been deleted.{" "}
              <button
                type="button"
                onClick={() => select({ kind: "none" })}
                className="font-medium underline"
              >
                Back to the library
              </button>
            </div>
          )}

          {selection.kind === "list" && selectedList && draft && (
            <div className="space-y-6">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                    Saved list
                  </p>
                  <h3 className="mt-1 truncate text-xl font-semibold text-slate-950">
                    {selectedList.name}
                  </h3>
                  <p className="mt-1 text-xs text-slate-400">
                    <code>{selectedList.id}</code> · updated {formatDate(selectedList.updatedAt)}
                  </p>
                </div>

                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={duplicateList}
                    disabled={busy}
                    className={secondaryButton}
                  >
                    Duplicate
                  </button>
                </div>
              </div>

              <OptionListForm
                idPrefix={`library-${selectedList.id}`}
                draft={draft}
                onChange={updateDraft}
                problems={problems}
                disabled={busy}
              />

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-5">
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={saveChanges}
                    disabled={!dirty || problems.length > 0 || busy}
                    className={saveButton}
                  >
                    {busy ? "Saving…" : "Save changes"}
                  </button>

                  {dirty && (
                    <button
                      type="button"
                      onClick={() => discardDraft(selectedList.id)}
                      className={secondaryButton}
                    >
                      Discard changes
                    </button>
                  )}
                </div>

                {confirmingDelete ? (
                  <span
                    role="alert"
                    className="flex flex-wrap items-center gap-2 text-sm text-slate-600"
                  >
                    Delete “{selectedList.name}”? Forms that copied its options keep them.
                    <button
                      type="button"
                      onClick={removeList}
                      disabled={busy}
                      className="rounded-xl bg-rose-600 px-3 py-2 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-50"
                    >
                      Confirm delete
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmingDelete(false)}
                      className={secondaryButton}
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmingDelete(true)}
                    disabled={busy}
                    className={dangerButton}
                  >
                    Delete list
                  </button>
                )}
              </div>

              {/* Where used */}
              <div className="rounded-xl bg-slate-50 px-4 py-4">
                <h4 className="text-sm font-medium text-slate-900">Where these options appear</h4>
                <p className="mt-1 text-xs text-slate-500">
                  Fields whose options are an identical copy of this list. They are independent
                  copies - saving this list does not update them.
                </p>
                {usage.length === 0 ? (
                  <p className="mt-3 text-xs text-slate-400">No fields currently use these options.</p>
                ) : (
                  <ul className="mt-3 space-y-1.5 text-sm">
                    {usage.map(({ form, fields }) => (
                      <li key={form.id} className="flex flex-wrap items-baseline gap-x-2">
                        <Link
                          href={`/forms/builder?id=${encodeURIComponent(form.id)}`}
                          className="font-medium text-slate-900 underline-offset-2 hover:underline"
                        >
                          {form.name}
                        </Link>
                        <span className="text-xs text-slate-500">
                          {fields.map((field) => field.label || field.id).join(", ")}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

          {selection.kind === "new" && draft && (
            <div className="space-y-6">
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                  New list
                </p>
                <h3 className="mt-1 text-xl font-semibold text-slate-950">Create a reusable list</h3>
              </div>

              <OptionListForm
                idPrefix="library-new"
                draft={draft}
                onChange={updateDraft}
                problems={problems}
                disabled={busy}
              />

              <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-5">
                <button
                  type="button"
                  onClick={createList}
                  disabled={problems.length > 0 || busy}
                  className={saveButton}
                >
                  {busy ? "Creating…" : "Create list"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setDrafts((current) => ({ ...current, [NEW_KEY]: emptyDraft() }));
                    select({ kind: "none" });
                  }}
                  className={secondaryButton}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

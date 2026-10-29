"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";

import OptionListEditor from "@/components/OptionListEditor";
import OptionListForm from "@/components/OptionListForm";
import {
  draftFromList,
  draftProblems,
  draftToInput,
  emptyDraft,
  isDraftDirty,
  sameOptions,
  sortLists,
  type ListDraft,
} from "@/lib/optionLists";
import {
  createOptionList,
  deleteOptionList,
  fetchOptionLists,
  updateOptionList,
} from "@/lib/optionListsApi";
import {
  accumulateRename,
  cleanOptions,
  missingUsedOptions,
  validateOptions,
  type MissingUsedOption,
  type OptionChange,
  type OptionRename,
  type OptionUsage,
} from "@/lib/options";
import type { OptionList } from "@/lib/types";

type ManageOptionsModalProps = {
  /** Label of the field whose options are being managed (for the heading). */
  fieldLabel: string;
  /** The field's current options. */
  options: string[];
  /** Options referenced by logic rules on other fields (for warnings). */
  usage?: OptionUsage;
  /**
   * Replace the field's options. `renames` lists options renamed during this
   * session so the builder can keep logic rules pointing at them. The modal
   * closes afterwards.
   */
  onApply: (options: string[], renames: OptionRename[]) => void;
  onClose: () => void;
};

/** What the right-hand editor is showing. */
type Selection = { kind: "field" } | { kind: "list"; id: string } | { kind: "new" };

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

function selectionKey(selection: Selection): string {
  return selection.kind === "list" ? selection.id : selection.kind;
}

type PendingApply = { options: string[]; renames: OptionRename[]; missing: MissingUsedOption[] };

export default function ManageOptionsModal({
  fieldLabel,
  options,
  usage,
  onApply,
  onClose,
}: ManageOptionsModalProps) {
  const [lists, setLists] = useState<OptionList[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [selection, setSelection] = useState<Selection>({ kind: "field" });
  const [fieldDraft, setFieldDraft] = useState<string[]>(() => [...options]);
  const [fieldRenames, setFieldRenames] = useState<OptionRename[]>([]);
  const [pendingApply, setPendingApply] = useState<PendingApply | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ListDraft>>({
    [NEW_KEY]: emptyDraft(),
  });
  const [saveAsName, setSaveAsName] = useState("");
  const [showSaveAs, setShowSaveAs] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const dialogRef = useRef<HTMLDivElement>(null);

  // -- data -------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    fetchOptionLists()
      .then((result) => {
        if (!cancelled) setLists(result);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : "Could not load saved lists.");
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

  // -- dialog behaviour ---------------------------------------------------------

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  // -- derived state ------------------------------------------------------------

  const selectedList = useMemo(
    () => (selection.kind === "list" ? lists.find((list) => list.id === selection.id) ?? null : null),
    [lists, selection]
  );

  const currentKey = selectionKey(selection);
  const listDraft: ListDraft | null =
    selection.kind === "field"
      ? null
      : drafts[currentKey] ?? (selectedList ? draftFromList(selectedList) : drafts[NEW_KEY]);

  const listDirty =
    selection.kind === "list" && selectedList && listDraft
      ? isDraftDirty(listDraft, selectedList)
      : false;

  const fieldProblems = validateOptions(fieldDraft);
  const listProblems = listDraft ? draftProblems(listDraft) : [];

  function updateListDraft(partial: Partial<ListDraft>) {
    if (!listDraft) return;
    setDrafts((current) => ({ ...current, [currentKey]: { ...listDraft, ...partial } }));
    setNotice(null);
  }

  function select(next: Selection) {
    setSelection(next);
    setNotice(null);
    setConfirmingDelete(false);
    setShowSaveAs(false);
    setPendingApply(null);
  }

  // -- actions ------------------------------------------------------------------

  /**
   * Apply options to the field. If rules depend on options that would
   * disappear, ask first instead of silently breaking them.
   */
  function applyToField(nextOptions: readonly string[], renames: OptionRename[] = []) {
    const cleaned = cleanOptions(nextOptions);
    const missing = usage ? missingUsedOptions(usage, cleaned, renames) : [];
    if (missing.length > 0) {
      setPendingApply({ options: cleaned, renames, missing });
      return;
    }
    onApply(cleaned, renames);
    onClose();
  }

  function trackFieldChange(next: string[], change: OptionChange) {
    setFieldDraft(next);
    setNotice(null);
    setPendingApply(null);
    if (change.type === "rename") {
      setFieldRenames((current) => accumulateRename(current, change.from, change.to, options));
    }
  }

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

  function saveFieldOptionsAsList() {
    const name = saveAsName.trim();
    if (!name) {
      setNotice({ tone: "error", text: "Give the list a name first." });
      return;
    }
    run(async () => {
      const created = await createOptionList({ name, options: cleanOptions(fieldDraft) });
      setLists((current) => sortLists([...current, created]));
      setSaveAsName("");
      setShowSaveAs(false);
      setSelection({ kind: "list", id: created.id });
      setNotice({ tone: "success", text: `Saved "${created.name}" to the option library.` });
    });
  }

  function createNewList() {
    if (!listDraft) return;
    run(async () => {
      const created = await createOptionList(draftToInput(listDraft));
      setLists((current) => sortLists([...current, created]));
      setDrafts((current) => ({ ...current, [NEW_KEY]: emptyDraft() }));
      setSelection({ kind: "list", id: created.id });
      setNotice({ tone: "success", text: `Created "${created.name}".` });
    });
  }

  function saveListChanges() {
    if (!selectedList || !listDraft) return;
    run(async () => {
      const updated = await updateOptionList(selectedList.id, draftToInput(listDraft));
      setLists((current) => sortLists(current.map((list) => (list.id === updated.id ? updated : list))));
      setDrafts((current) => {
        const next = { ...current };
        delete next[updated.id];
        return next;
      });
      setNotice({
        tone: "success",
        text: `Saved "${updated.name}". Forms that already use these options are not affected.`,
      });
    });
  }

  function removeList() {
    if (!selectedList) return;
    run(async () => {
      await deleteOptionList(selectedList.id);
      setLists((current) => current.filter((list) => list.id !== selectedList.id));
      setDrafts((current) => {
        const next = { ...current };
        delete next[selectedList.id];
        return next;
      });
      setConfirmingDelete(false);
      setSelection({ kind: "field" });
      setNotice({ tone: "success", text: `Deleted "${selectedList.name}".` });
    });
  }

  // -- render -------------------------------------------------------------------

  const modal = (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="manage-options-title"
        tabIndex={-1}
        className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl outline-none"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Option library
            </p>
            <h3 id="manage-options-title" className="mt-1 text-lg font-semibold text-slate-950">
              Manage options · {fieldLabel}
            </h3>
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg px-2 py-1 text-lg leading-none text-slate-400 transition hover:bg-slate-100 hover:text-slate-900"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden md:grid-cols-[16rem_1fr]">
          {/* Sidebar */}
          <aside className="flex flex-col border-b border-slate-200 bg-slate-50 md:border-b-0 md:border-r">
            <nav aria-label="Option sources" className="flex-1 overflow-y-auto p-3">
              <SidebarItem
                active={selection.kind === "field"}
                onClick={() => select({ kind: "field" })}
                title="This field"
                subtitle={`${cleanOptions(fieldDraft).length} options`}
              />

              <p className="mt-4 mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Saved lists
              </p>

              {loading ? (
                <p className="px-3 py-2 text-xs text-slate-400">Loading…</p>
              ) : loadError ? (
                <div className="px-3 py-2 text-xs text-rose-600">
                  {loadError}{" "}
                  <button type="button" onClick={reload} className="font-medium underline">
                    Retry
                  </button>
                </div>
              ) : lists.length === 0 ? (
                <p className="px-3 py-2 text-xs text-slate-400">No saved lists yet.</p>
              ) : (
                <ul className="space-y-0.5">
                  {lists.map((list) => (
                    <li key={list.id}>
                      <SidebarItem
                        active={selection.kind === "list" && selection.id === list.id}
                        onClick={() => select({ kind: "list", id: list.id })}
                        title={list.name}
                        subtitle={`${list.options.length} options${
                          drafts[list.id] ? " · unsaved changes" : ""
                        }`}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </nav>

            <div className="space-y-2 border-t border-slate-200 p-3">
              <Link
                href="/forms/option-lists"
                className="block px-1 text-xs font-medium text-slate-500 underline-offset-2 hover:text-slate-900 hover:underline"
              >
                Open the Option Library →
              </Link>
              <button
                type="button"
                onClick={() => select({ kind: "new" })}
                className={`w-full rounded-xl border border-dashed px-3 py-2 text-sm font-medium transition ${
                  selection.kind === "new"
                    ? "border-slate-900 bg-white text-slate-900"
                    : "border-slate-300 text-slate-600 hover:border-slate-400 hover:bg-white"
                }`}
              >
                + New list
              </button>
            </div>
          </aside>

          {/* Editor */}
          <section className="min-h-0 overflow-y-auto p-6">
            {pendingApply && (
              <div
                role="alert"
                className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
              >
                <p className="font-medium">
                  These options would break conditional logic on other fields:
                </p>
                <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
                  {pendingApply.missing.map((item) => (
                    <li key={item.option}>
                      “{item.option}” is used by {item.fields.map((f) => `“${f}”`).join(", ")}
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-xs">
                  You can apply anyway and then update those rules - they will be flagged until
                  you do.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      onApply(pendingApply.options, pendingApply.renames);
                      onClose();
                    }}
                    className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700"
                  >
                    Apply anyway
                  </button>
                  <button
                    type="button"
                    onClick={() => setPendingApply(null)}
                    className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {notice && (
              <div
                role={notice.tone === "error" ? "alert" : "status"}
                className={`mb-5 rounded-xl border px-4 py-3 text-sm ${
                  notice.tone === "error"
                    ? "border-rose-200 bg-rose-50 text-rose-800"
                    : "border-emerald-200 bg-emerald-50 text-emerald-800"
                }`}
              >
                {notice.text}
              </div>
            )}

            {selection.kind === "field" && (
              <div className="space-y-5">
                <div>
                  <h4 className="font-medium text-slate-900">Options for this field</h4>
                  <p className="mt-1 text-sm text-slate-500">
                    Edit the options below, then apply them to the field. You can also save them
                    to the library to reuse in other forms.
                  </p>
                </div>

                <OptionListEditor
                  idPrefix="field-draft"
                  options={fieldDraft}
                  onChange={trackFieldChange}
                  usage={usage}
                  emptyHint="No options yet. Add one, or pick a saved list from the left."
                />

                {fieldProblems.length > 0 && (
                  <p className="text-xs text-amber-700">This field {fieldProblems.join(" ")}</p>
                )}

                <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-5">
                  <button
                    type="button"
                    onClick={() => applyToField(fieldDraft, fieldRenames)}
                    disabled={fieldProblems.length > 0 || busy}
                    className={primaryButton}
                  >
                    Apply to field
                  </button>

                  {showSaveAs ? (
                    <form
                      className="flex flex-1 flex-wrap items-center gap-2"
                      onSubmit={(event) => {
                        event.preventDefault();
                        saveFieldOptionsAsList();
                      }}
                    >
                      <input
                        aria-label="New list name"
                        autoFocus
                        value={saveAsName}
                        onChange={(e) => setSaveAsName(e.target.value)}
                        placeholder="List name, e.g. Contact Methods"
                        className="min-w-[12rem] flex-1 rounded-xl border border-slate-200 px-3.5 py-2 text-sm outline-none focus:border-slate-400 focus:ring-4 focus:ring-slate-100"
                      />
                      <button
                        type="submit"
                        disabled={busy || fieldProblems.length > 0}
                        className={saveButton}
                      >
                        {busy ? "Saving…" : "Save list"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowSaveAs(false)}
                        className={secondaryButton}
                      >
                        Cancel
                      </button>
                    </form>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setShowSaveAs(true)}
                      disabled={fieldProblems.length > 0 || busy}
                      className={secondaryButton}
                    >
                      Save as reusable list…
                    </button>
                  )}
                </div>
              </div>
            )}

            {selection.kind === "list" && selectedList && listDraft && (
              <div className="space-y-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h4 className="font-medium text-slate-900">Saved list</h4>
                    <p className="mt-1 text-sm text-slate-500">
                      Use this list in the field, or edit the list itself. Loading copies the
                      options into the field - later edits to the list never change existing forms.
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={() => applyToField(selectedList.options)}
                    disabled={busy}
                    className={primaryButton}
                  >
                    Use in field
                  </button>
                </div>

                <OptionListForm
                  idPrefix={`list-${selectedList.id}`}
                  draft={listDraft}
                  onChange={updateListDraft}
                  problems={listProblems}
                />

                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-5">
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      onClick={saveListChanges}
                      disabled={!listDirty || listProblems.length > 0 || busy}
                      className={saveButton}
                    >
                      {busy ? "Saving…" : "Save changes"}
                    </button>

                    {listDirty && (
                      <button
                        type="button"
                        onClick={() =>
                          setDrafts((current) => {
                            const next = { ...current };
                            delete next[selectedList.id];
                            return next;
                          })
                        }
                        className={secondaryButton}
                      >
                        Discard changes
                      </button>
                    )}
                  </div>

                  {confirmingDelete ? (
                    <span className="flex items-center gap-2 text-sm text-slate-600">
                      Delete this list?
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
              </div>
            )}

            {selection.kind === "list" && !selectedList && !loading && (
              <p className="text-sm text-slate-500">That list no longer exists.</p>
            )}

            {selection.kind === "new" && listDraft && (
              <div className="space-y-5">
                <div>
                  <h4 className="font-medium text-slate-900">New reusable list</h4>
                  <p className="mt-1 text-sm text-slate-500">
                    Build a named list once and load it into any dropdown or radio field.
                  </p>
                </div>

                <OptionListForm
                  idPrefix="new-list"
                  draft={listDraft}
                  onChange={updateListDraft}
                  problems={listProblems}
                  namePlaceholder="e.g. US States"
                  optionsAction={
                    cleanOptions(fieldDraft).length > 0 &&
                    !sameOptions(listDraft.options, fieldDraft) ? (
                      <button
                        type="button"
                        onClick={() => updateListDraft({ options: [...fieldDraft] })}
                        className="text-xs font-medium text-slate-500 underline-offset-2 hover:text-slate-900 hover:underline"
                      >
                        Start from this field&apos;s options
                      </button>
                    ) : null
                  }
                />

                <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-5">
                  <button
                    type="button"
                    onClick={createNewList}
                    disabled={listProblems.length > 0 || busy}
                    className={saveButton}
                  >
                    {busy ? "Creating…" : "Create list"}
                  </button>
                  <button
                    type="button"
                    onClick={() => select({ kind: "field" })}
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
    </div>
  );

  return createPortal(modal, document.body);
}

function SidebarItem({
  active,
  onClick,
  title,
  subtitle,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  subtitle: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      className={`block w-full rounded-xl px-3 py-2 text-left transition ${
        active ? "bg-white text-slate-950 shadow-sm" : "text-slate-700 hover:bg-white/70"
      }`}
    >
      <span className="block truncate text-sm font-medium">{title}</span>
      <span className="block text-xs text-slate-400">{subtitle}</span>
    </button>
  );
}

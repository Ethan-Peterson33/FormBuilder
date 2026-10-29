"use client";

import {
  LIST_OPERATORS,
  OPERATOR_LABELS,
  operatorNeedsValue,
  operatorsForField,
  staleOptionValues,
  type LogicProblem,
} from "@/lib/formLogic";
import type { Condition, ConditionOperator, FieldConfig, FieldLogic } from "@/lib/types";

type LogicEditorProps = {
  field: FieldConfig;
  /** Fields that appear before this one; the only valid condition sources. */
  sourceFields: FieldConfig[];
  /** Every field in the form - used to describe references that became invalid. */
  allFields?: FieldConfig[];
  /** Problems detected for this field's conditions (see `findLogicProblems`). */
  problems?: LogicProblem[];
  onChange: (logic: FieldLogic | undefined) => void;
};

const selectClass =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition focus:border-slate-400 focus:ring-4 focus:ring-slate-100";
const invalidClass = "border-rose-300 bg-rose-50/40 focus:border-rose-400 focus:ring-rose-100";

export const DEFAULT_LOGIC: FieldLogic = { action: "show", match: "all", conditions: [] };

function defaultCondition(source: FieldConfig | undefined): Condition {
  const operators = operatorsForField(source);
  return {
    fieldId: source?.id ?? "",
    operator: operators[0] ?? "equals",
    value: source?.type === "checkbox" ? undefined : "",
  };
}

export default function LogicEditor({
  field,
  sourceFields,
  allFields,
  problems = [],
  onChange,
}: LogicEditorProps) {
  const logic = field.logic;
  const enabled = Boolean(logic);
  const hasSources = sourceFields.length > 0;
  const problemCount = problems.length;

  function update(partial: Partial<FieldLogic>) {
    onChange({ ...(logic ?? DEFAULT_LOGIC), ...partial });
  }

  function updateCondition(index: number, partial: Partial<Condition>) {
    if (!logic) return;
    update({
      conditions: logic.conditions.map((condition, i) =>
        i === index ? { ...condition, ...partial } : condition
      ),
    });
  }

  function changeSource(index: number, fieldId: string) {
    const source = sourceFields.find((f) => f.id === fieldId);
    // Reset operator/value because they depend on the source type.
    updateCondition(index, defaultCondition(source));
  }

  function changeOperator(index: number, operator: ConditionOperator) {
    if (!logic) return;
    const current = logic.conditions[index];
    const wasList = LIST_OPERATORS.has(current.operator);
    const isList = LIST_OPERATORS.has(operator);
    updateCondition(index, {
      operator,
      value: !operatorNeedsValue(operator)
        ? undefined
        : wasList !== isList
          ? ""
          : (current.value ?? ""),
    });
  }

  function addCondition() {
    update({
      conditions: [...(logic?.conditions ?? []), defaultCondition(sourceFields[0])],
    });
  }

  function removeCondition(index: number) {
    if (!logic) return;
    update({ conditions: logic.conditions.filter((_, i) => i !== index) });
  }

  function toggle(checked: boolean) {
    if (!checked) {
      onChange(undefined);
      return;
    }
    onChange({ ...DEFAULT_LOGIC, conditions: [defaultCondition(sourceFields[0])] });
  }

  return (
    <div className="rounded-xl border border-violet-100 bg-violet-50/40 p-4">
      <label className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={enabled}
          disabled={!hasSources && !enabled}
          onChange={(e) => toggle(e.target.checked)}
          aria-label="Enable conditional logic"
          className="mt-0.5 h-4 w-4 rounded border-slate-300"
        />

        <span className="flex-1">
          <span className="flex items-center gap-2 text-sm font-medium text-slate-800">
            Conditional logic
            {problemCount > 0 && (
              <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rose-700">
                {problemCount} {problemCount === 1 ? "problem" : "problems"}
              </span>
            )}
          </span>
          <span className="block text-xs text-slate-500">
            {hasSources
              ? "Show or hide this field based on answers to earlier fields."
              : enabled
                ? "No earlier fields are available; fix or remove the conditions below."
                : "Add a field above this one to use it in a condition."}
          </span>
        </span>
      </label>

      {enabled && logic && (
        <div className="mt-4 space-y-3">
          {/* Action + match */}
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
            <select
              aria-label="Logic action"
              value={logic.action}
              onChange={(e) => update({ action: e.target.value as FieldLogic["action"] })}
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm font-medium"
            >
              <option value="show">Show</option>
              <option value="hide">Hide</option>
            </select>

            <span>this field when</span>

            <select
              aria-label="Condition match mode"
              value={logic.match}
              onChange={(e) => update({ match: e.target.value as FieldLogic["match"] })}
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm font-medium"
            >
              <option value="all">all</option>
              <option value="any">any</option>
            </select>

            <span>of the following match:</span>
          </div>

          {/* Conditions */}
          <ul className="space-y-2">
            {logic.conditions.map((condition, index) => {
              const source = sourceFields.find((f) => f.id === condition.fieldId);
              const operators = operatorsForField(source);
              const needsValue = operatorNeedsValue(condition.operator);
              const conditionProblems = problems.filter((p) => p.conditionIndex === index);
              const codes = new Set(conditionProblems.map((p) => p.code));

              // A chosen source that is no longer usable: deleted, or now later in the form.
              const staleSource = condition.fieldId && !source ? condition.fieldId : null;
              const staleSourceField = staleSource
                ? allFields?.find((f) => f.id === staleSource)
                : undefined;
              const staleSourceLabel = staleSource
                ? staleSourceField
                  ? `${staleSourceField.label || staleSourceField.id} (appears after this field)`
                  : `${staleSource} (deleted field)`
                : null;

              const staleOperator = source && !operators.includes(condition.operator);
              const staleValues = source ? staleOptionValues(source, condition) : [];

              return (
                <li
                  key={index}
                  data-testid="logic-condition"
                  data-invalid={conditionProblems.length > 0 || undefined}
                  className={`space-y-2 rounded-lg border bg-white p-3 ${
                    conditionProblems.length > 0 ? "border-rose-300" : "border-slate-200"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <select
                      aria-label={`Condition ${index + 1} field`}
                      aria-invalid={Boolean(staleSource) || codes.has("missing_source") || undefined}
                      value={condition.fieldId}
                      onChange={(e) => changeSource(index, e.target.value)}
                      className={`${selectClass} flex-1 ${staleSource ? invalidClass : ""}`}
                    >
                      <option value="">Choose a field…</option>
                      {staleSource && (
                        <option value={staleSource} disabled>
                          ⚠ {staleSourceLabel}
                        </option>
                      )}
                      {sourceFields.map((f) => (
                        <option key={f.id} value={f.id}>
                          {f.label || f.id}
                        </option>
                      ))}
                    </select>

                    <button
                      type="button"
                      onClick={() => removeCondition(index)}
                      aria-label={`Remove condition ${index + 1}`}
                      className="rounded-lg px-2 py-1.5 text-sm text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                    >
                      ✕
                    </button>
                  </div>

                  <div className="grid gap-2 sm:grid-cols-2">
                    <select
                      aria-label={`Condition ${index + 1} operator`}
                      aria-invalid={staleOperator || undefined}
                      value={condition.operator}
                      onChange={(e) => changeOperator(index, e.target.value as ConditionOperator)}
                      disabled={!source}
                      className={`${selectClass} ${staleOperator ? invalidClass : ""}`}
                    >
                      {(staleOperator || !source) && (
                        <option value={condition.operator} disabled>
                          {source
                            ? `⚠ ${OPERATOR_LABELS[condition.operator]} (not valid for ${source.type})`
                            : OPERATOR_LABELS[condition.operator]}
                        </option>
                      )}
                      {operators.map((operator) => (
                        <option key={operator} value={operator}>
                          {OPERATOR_LABELS[operator]}
                        </option>
                      ))}
                    </select>

                    {needsValue ? (
                      <ConditionValueInput
                        index={index}
                        source={source}
                        condition={condition}
                        staleValues={staleValues}
                        onChange={(value) => updateCondition(index, { value })}
                      />
                    ) : (
                      <span className="self-center text-xs text-slate-400">No value needed</span>
                    )}
                  </div>

                  {conditionProblems.length > 0 && (
                    <ul className="space-y-1" aria-label={`Condition ${index + 1} problems`}>
                      {conditionProblems.map((problem) => (
                        <li
                          key={problem.code}
                          className="flex gap-1.5 text-xs text-rose-700"
                        >
                          <span aria-hidden>⚠</span>
                          <span>{problem.message}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>

          <button
            type="button"
            onClick={addCondition}
            className="rounded-lg border border-dashed border-violet-300 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:bg-violet-50"
          >
            + Add condition
          </button>
        </div>
      )}
    </div>
  );
}

type ConditionValueInputProps = {
  index: number;
  source: FieldConfig | undefined;
  condition: Condition;
  /** Values that are no longer options of the source (shown as stale). */
  staleValues?: string[];
  onChange: (value: Condition["value"]) => void;
};

/** Picks an input that suits the source field type and operator. */
function ConditionValueInput({
  index,
  source,
  condition,
  staleValues = [],
  onChange,
}: ConditionValueInputProps) {
  const label = `Condition ${index + 1} value`;
  const isList = LIST_OPERATORS.has(condition.operator);
  const raw = condition.value;
  const hasStale = staleValues.length > 0;

  if (!source) {
    return <input aria-label={label} disabled className={selectClass} placeholder="Value" />;
  }

  if (isList) {
    const hasOptions = (source.type === "select" || source.type === "radio") && source.options?.length;
    const selected = Array.isArray(raw)
      ? raw
      : typeof raw === "string" && raw
        ? raw.split(",").map((v) => v.trim())
        : [];

    if (hasOptions) {
      const staleSelected = selected.filter((value) =>
        staleValues.some((stale) => stale.toLowerCase() === value.toLowerCase())
      );
      return (
        <fieldset
          className={`flex flex-wrap gap-2 ${hasStale ? "rounded-lg border border-rose-300 bg-rose-50/40 p-1.5" : ""}`}
          aria-label={label}
          aria-invalid={hasStale || undefined}
        >
          {staleSelected.map((value) => (
            <span
              key={`stale-${value}`}
              className="inline-flex items-center gap-1 rounded-full border border-rose-300 bg-rose-50 px-2.5 py-1 text-xs text-rose-700 line-through"
              title="No longer an option"
            >
              {value}
              <button
                type="button"
                aria-label={`Remove stale value ${value}`}
                onClick={() => onChange(selected.filter((v) => v !== value))}
                className="no-underline"
              >
                ✕
              </button>
            </span>
          ))}
          {source.options!.map((option) => {
            const checked = selected.includes(option);
            return (
              <label
                key={option}
                className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs ${
                  checked
                    ? "border-violet-400 bg-violet-100 text-violet-800"
                    : "border-slate-200 bg-white text-slate-600"
                }`}
              >
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={checked}
                  onChange={(e) =>
                    onChange(
                      e.target.checked
                        ? [...selected, option]
                        : selected.filter((v) => v !== option)
                    )
                  }
                />
                {option}
              </label>
            );
          })}
        </fieldset>
      );
    }

    return (
      <input
        aria-label={label}
        value={Array.isArray(raw) ? raw.join(", ") : typeof raw === "string" ? raw : ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Comma-separated values"
        className={selectClass}
      />
    );
  }

  if ((source.type === "select" || source.type === "radio") && source.options?.length) {
    const current = typeof raw === "string" ? raw : "";
    return (
      <select
        aria-label={label}
        aria-invalid={hasStale || undefined}
        value={current}
        onChange={(e) => onChange(e.target.value)}
        className={`${selectClass} ${hasStale ? invalidClass : ""}`}
      >
        <option value="">Choose a value…</option>
        {hasStale && current && (
          <option value={current} disabled>
            ⚠ {current} (no longer an option)
          </option>
        )}
        {source.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  if (source.type === "number") {
    return (
      <input
        aria-label={label}
        type="number"
        step="any"
        value={typeof raw === "number" || typeof raw === "string" ? raw : ""}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        placeholder="0"
        className={selectClass}
      />
    );
  }

  if (source.type === "date") {
    return (
      <input
        aria-label={label}
        type="date"
        value={typeof raw === "string" ? raw : ""}
        onChange={(e) => onChange(e.target.value)}
        className={selectClass}
      />
    );
  }

  return (
    <input
      aria-label={label}
      value={typeof raw === "string" || typeof raw === "number" ? String(raw) : ""}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Value"
      className={selectClass}
    />
  );
}

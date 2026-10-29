/**
 * Shared form types. These mirror the Pydantic models in
 * `backend/models/forms.py` - keep the two in sync.
 */

export type FieldType =
  | "text"
  | "textarea"
  | "number"
  | "date"
  | "select"
  | "radio"
  | "checkbox";

/** Operators available in conditional logic. Which ones apply depends on the source field type. */
export type ConditionOperator =
  // generic
  | "equals"
  | "not_equals"
  | "is_empty"
  | "is_not_empty"
  // strings
  | "contains"
  | "not_contains"
  | "starts_with"
  | "ends_with"
  | "is_one_of"
  | "is_not_one_of"
  // numbers
  | "greater_than"
  | "greater_than_or_equal"
  | "less_than"
  | "less_than_or_equal"
  // dates
  | "before"
  | "after"
  | "on_or_before"
  | "on_or_after"
  // checkboxes
  | "is_checked"
  | "is_not_checked";

export type ConditionValue = string | number | boolean | string[] | null;

/** A single comparison against the current value of an earlier field. */
export type Condition = {
  fieldId: string;
  operator: ConditionOperator;
  value?: ConditionValue;
};

/** Visibility rules attached to a field. */
export type FieldLogic = {
  /** Whether matching conditions show or hide the field. */
  action: "show" | "hide";
  /** Require every condition to match, or any one of them. */
  match: "all" | "any";
  conditions: Condition[];
};

export type FieldConfig = {
  id: string;
  type: FieldType;
  label: string;
  required?: boolean;
  placeholder?: string;
  options?: string[];
  logic?: FieldLogic;
};

/** A single answer. Numbers are stored as numbers, checkboxes as booleans, everything else as strings. */
export type FieldValue = string | number | boolean | null | undefined;

export type FormValues = Record<string, FieldValue>;

export type FormConfig = {
  id: string;
  name: string;
  description?: string;
  fields: FieldConfig[];
  createdAt?: string;
  updatedAt?: string;
};

/** Shape returned by `/api/*` routes when something goes wrong. */
export type ApiError = {
  error: string;
  details?: unknown;
};

// ---------------------------------------------------------------------------
// Submissions (mirror backend/models/submissions.py)
// ---------------------------------------------------------------------------

export type SubmissionMetadata = {
  submittedAt: string;
  submittedBy?: string | null;
  source: string;
  userAgent?: string | null;
  ipAddress?: string | null;
  /** Fields hidden by conditional logic when the form was submitted. */
  hiddenFields: string[];
  /** Any extra context recorded by the client. */
  [key: string]: unknown;
};

export type Submission = {
  id: string;
  formId: string;
  formName: string;
  data: FormValues;
  metadata: SubmissionMetadata;
};

export type SubmissionList = {
  total: number;
  items: Submission[];
};

/** Body sent to POST /api/forms/:id/submissions */
export type SubmissionCreate = {
  data: FormValues;
  submittedBy?: string;
  source?: string;
  metadata?: Record<string, unknown>;
};

/** Shape of a 422 from the submissions endpoint. */
export type SubmissionValidationError = {
  error: string;
  details?: { message?: string; errors?: Record<string, string> };
};

// ---------------------------------------------------------------------------
// Reusable option lists (mirror backend/models/option_lists.py)
// ---------------------------------------------------------------------------

export type OptionList = {
  id: string;
  name: string;
  description?: string;
  options: string[];
  createdAt?: string;
  updatedAt?: string;
};

/** Body for POST/PUT /api/option-lists */
export type OptionListInput = {
  id?: string;
  name: string;
  description?: string;
  options: string[];
};

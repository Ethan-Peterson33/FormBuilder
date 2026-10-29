-- 0001_initial: forms, option lists, submissions.
--
-- Mirrors backend/data/*.json (see docs/database-schema-plan.md, phase 1).
-- Applied by: the Postgres container on first start (docker-entrypoint-initdb.d),
-- `python -m db.migrate` for existing databases, and the test-suite.
--
-- Every migration runs in one transaction and records itself in
-- schema_migrations as its last statement, so a failure leaves nothing behind
-- and a re-run is refused by the primary key.

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text        PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- forms  <->  models/forms.py#FormConfig
-- ---------------------------------------------------------------------------
CREATE TABLE forms (
    id          text        PRIMARY KEY,
    name        text        NOT NULL,
    description text        NOT NULL DEFAULT '',
    -- Ordered list of FieldConfig objects (camelCase, as served by the API).
    -- Structure and business rules are validated by the application; the
    -- database only guarantees it is a JSON array.
    fields      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- Soft delete: submissions reference forms and must outlive them.
    deleted_at  timestamptz,

    CONSTRAINT forms_id_not_blank     CHECK (btrim(id) <> ''),
    CONSTRAINT forms_name_not_blank   CHECK (btrim(name) <> ''),
    CONSTRAINT forms_fields_is_array  CHECK (jsonb_typeof(fields) = 'array')
);

COMMENT ON TABLE  forms            IS 'Form configurations (the builder''s output).';
COMMENT ON COLUMN forms.id         IS 'URL slug generated from the name, or requested by the client. Immutable.';
COMMENT ON COLUMN forms.fields     IS 'Ordered FieldConfig[] as JSON (camelCase). Logic may only reference earlier fields.';
COMMENT ON COLUMN forms.deleted_at IS 'Set by DELETE /forms/{id}. Deleted forms are hidden from the API but keep their submissions.';

CREATE INDEX forms_active_name_idx ON forms (name) WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- option_lists  <->  models/option_lists.py#OptionList
-- ---------------------------------------------------------------------------
CREATE TABLE option_lists (
    id          text        PRIMARY KEY,
    name        text        NOT NULL,
    description text        NOT NULL DEFAULT '',
    -- Ordered, trimmed, non-empty values; unique case-insensitively (checked
    -- by the application because logic compares options case-insensitively).
    options     text[]      NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT option_lists_id_not_blank   CHECK (btrim(id) <> ''),
    CONSTRAINT option_lists_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT option_lists_name_length    CHECK (char_length(name) <= 120),
    CONSTRAINT option_lists_not_empty      CHECK (cardinality(options) >= 1)
);

COMMENT ON TABLE  option_lists         IS 'Named reusable option lists. Loading one into a field COPIES the values; nothing references a list.';
COMMENT ON COLUMN option_lists.options IS 'Ordered option values.';

-- Names are unique regardless of case (repository.find_by_name lower-cases).
CREATE UNIQUE INDEX option_lists_name_unique ON option_lists (lower(name));

-- ---------------------------------------------------------------------------
-- submissions  <->  models/submissions.py#Submission
-- ---------------------------------------------------------------------------
CREATE TABLE submissions (
    id            text        PRIMARY KEY,
    -- Insertion order. Tie-breaker when two submissions share a timestamp,
    -- matching the JSON repository ("later in the file = newer").
    seq           bigint      GENERATED ALWAYS AS IDENTITY,
    form_id       text        NOT NULL REFERENCES forms (id) ON DELETE RESTRICT,
    -- Snapshot of forms.name at submit time; survives renames.
    form_name     text        NOT NULL,
    -- {fieldId: value} for the fields that were visible; values already
    -- coerced by services/submissions.py.
    data          jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- Server-owned metadata (SubmissionMetadata's declared fields).
    submitted_at  timestamptz NOT NULL DEFAULT now(),
    submitted_by  text,
    source        text        NOT NULL DEFAULT 'web',
    user_agent    text,
    ip_address    text,
    hidden_fields text[]      NOT NULL DEFAULT '{}'::text[],
    -- Anything else the client attached (pageUrl, clientHiddenFields, ...).
    -- Reserved keys are stripped by the application before storage.
    metadata      jsonb       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT submissions_id_not_blank       CHECK (btrim(id) <> ''),
    CONSTRAINT submissions_data_is_object     CHECK (jsonb_typeof(data) = 'object'),
    CONSTRAINT submissions_metadata_is_object CHECK (jsonb_typeof(metadata) = 'object')
);

COMMENT ON TABLE  submissions               IS 'Answers submitted to a form. Append-only from the API.';
COMMENT ON COLUMN submissions.seq           IS 'Insertion order; secondary sort key for listings (newest first).';
COMMENT ON COLUMN submissions.form_name     IS 'Form name at submit time (snapshot).';
COMMENT ON COLUMN submissions.hidden_fields IS 'Field ids hidden by conditional logic when submitted (server-evaluated).';
COMMENT ON COLUMN submissions.metadata      IS 'Client-supplied extras only; server-owned keys are columns.';

-- GET /forms/{id}/submissions and GET /submissions?formId=  (newest first).
CREATE INDEX submissions_form_recent_idx ON submissions (form_id, submitted_at DESC, seq DESC);
-- GET /submissions  (newest first).
CREATE INDEX submissions_recent_idx      ON submissions (submitted_at DESC, seq DESC);
-- Filtering / reporting on answers, e.g. data @> '{"hasPet": "Yes"}'.
CREATE INDEX submissions_data_gin        ON submissions USING gin (data jsonb_path_ops);

INSERT INTO schema_migrations (version) VALUES ('0001_initial');

COMMIT;

# Database schema plan

This document plans the PostgreSQL schema for the Form, Table and Workflow
Builder. It covers (1) the data the backend stores **today** in
`backend/data/*.json`, and (2) the features the README lists as the project's
direction: table editing, workflows, and authentication.

Phase 1 is implemented in `backend/db/migrations/0001_initial.sql`, used by
the API through `backend/repositories/pg_*.py`, and exercised by the whole
backend test-suite (`tests/conftest.py` runs the API tests against PostgreSQL,
`tests/test_db_schema.py` rebuilds the schema from scratch). Later phases are sketched here
so that phase-1 decisions do not paint us into a corner; they are **not**
created yet, because their shape will change once those features are designed.

Contents

1. [What is stored today](#1-what-is-stored-today)
2. [Design decisions](#2-design-decisions)
3. [Phase 1 - schema for the current data (implemented)](#3-phase-1---schema-for-the-current-data-implemented)
4. [Phase 2 - authentication and form versions](#4-phase-2---authentication-and-form-versions)
5. [Phase 3 - data tables](#5-phase-3---data-tables)
6. [Phase 4 - workflows](#6-phase-4---workflows)
7. [Cross-cutting: audit log](#7-cross-cutting-audit-log)
8. [Migration strategy and next steps](#8-migration-strategy-and-next-steps)

---

## 1. What is stored today

Three JSON collections, each a JSON array of records, defined by the pydantic
models in `backend/models/`. JSON keys are camelCase; Python is snake_case.

### `forms.json` -> `models/forms.py#FormConfig`

| Key           | Type                              | Notes |
| ------------- | --------------------------------- | ----- |
| `id`          | slug string, e.g. `pet-registration` | Generated from the name (`-2`, `-3`... on clash) or requested by the client. Immutable after creation. Early builder versions wrote `""`; `repair_ids()` fixes blank/duplicate ids at start-up. |
| `name`        | string, required, trimmed         | |
| `description` | string, default `""`              | |
| `fields`      | **ordered** array of `FieldConfig` | Order is semantically significant: logic may only reference *earlier* fields. |
| `createdAt`, `updatedAt` | ISO-8601 UTC with `Z`, optional | Missing on records written before timestamps existed. |

`FieldConfig`: `id` (unique within the form; either a camelCase name such as
`hasPet` or `field-<uuid>`), `type` (`text | textarea | number | date | select |
radio | checkbox`), `label`, `required`, `placeholder?`, `options?: string[]`
(select/radio), `logic?: { action, match, conditions[] }`.

`Condition`: `fieldId`, `operator` (21 operators, allowed set depends on the
source field type), `value?: string | number | boolean | string[] | null`.

Business rules (`services/form_rules.py`) run **on save only**. A stored form
that breaks a rule introduced later must still load, render and accept
submissions. **The database must therefore not enforce those rules.**

### `option_lists.json` -> `models/option_lists.py#OptionList`

| Key           | Type                      | Notes |
| ------------- | ------------------------- | ----- |
| `id`          | slug string               | Generated from name, or requested. |
| `name`        | string, 1-120 chars, **unique case-insensitively** | `find_by_name` compares `lower()`. |
| `description` | string, default `""`      | |
| `options`     | ordered `string[]`, >= 1 item, trimmed, unique case-insensitively | Enforced on load *and* save by the model validator. |
| `createdAt`, `updatedAt` | ISO-8601 UTC     | |

Loading a list into a field **copies** the values into the form. There is no
reference from a form field back to the list; the UI detects "identical copies"
by comparing values.

### `submissions.json` -> `models/submissions.py#Submission`

| Key        | Type                                 | Notes |
| ---------- | ------------------------------------ | ----- |
| `id`       | `sub-<uuid4>`                        | |
| `formId`   | form slug                            | Forms can currently be deleted while their submissions remain (dangling reference). |
| `formName` | string                               | **Snapshot** of the form name at submit time (`test_submissions_survive_form_rename`). |
| `data`     | object `{ fieldId: value }`          | Only *visible* fields, values already coerced (`number` -> int/float, `checkbox` -> bool, `date` -> `YYYY-MM-DD` string, choice -> option string). |
| `metadata.submittedAt` | ISO-8601 UTC             | Listing is ordered by this, newest first, ties broken by insertion order. |
| `metadata.submittedBy` | string or null           | Will come from the signed-in user once auth exists. |
| `metadata.source`      | string, default `web`    | |
| `metadata.userAgent`, `metadata.ipAddress` | string or null | Taken from headers forwarded by the Next.js proxy. |
| `metadata.hiddenFields` | `string[]`              | Field ids hidden by logic at submit time (server-evaluated). |
| `metadata.*`           | anything else            | Client extras (`pageUrl`, `clientHiddenFields`, ...). `extra="allow"` on the model; reserved keys cannot be overridden. |

### Access patterns the repositories need (`repositories/base.py`)

* Forms: list all, get by id, exists, insert, replace by id, delete by id.
* Option lists: list sorted by `lower(name)`, get by id, exists, find by
  `lower(name)`, insert, replace, delete.
* Submissions: list (optional `form_id` filter, newest first, `limit`/`offset`),
  count with the same filter, get by id, insert. Never updated or deleted by
  the API.

---

## 2. Design decisions

### D1. Hybrid relational + JSONB; one table per collection

Form configurations and submission answers are stored as `jsonb`; everything
that is queried, filtered, sorted, joined, or constrained becomes a real
column. Concretely:

* `forms.fields` is `jsonb`. A form is always read and written as a whole
  (`PUT` replaces the configuration), field order matters, logic is nested
  three levels deep, and the pydantic models already validate the structure.
  Normalising into `form_fields` / `field_options` / `field_logic` /
  `logic_conditions` would mean four tables, ordering columns, and a
  reassembly step on every read - for no query we need today. The same
  pattern (definition as a document, records as rows) will be reused for
  data tables (phase 3) and workflows (phase 4), keeping the codebase uniform.
* `submissions.data` is `jsonb` keyed by field id, with a GIN index. Field
  sets differ per form and change over time, so there is no fixed column set.
  Per-field reporting uses `data ->> 'fieldId'` and jsonb containment.
* `submissions.metadata` holds **only the client extras**; the server-owned
  keys (`submitted_at`, `submitted_by`, `source`, `user_agent`, `ip_address`,
  `hidden_fields`) are columns so they can be indexed and typed. The
  repository merges columns + extras back into the `metadata` object the API
  returns.
* `option_lists.options` is `text[]`, not `jsonb`: it is a flat, ordered list
  of strings and the array type says so.

**Why not a fully relational form model?** It gives referential integrity for
logic references (`condition.fieldId -> form_fields.id`) - but the README
explicitly requires that stale references *load* and are surfaced by the
builder, so that integrity cannot be enforced by the database anyway.

### D2. Keep the public identifiers as the primary keys

`forms.id` and `option_lists.id` are the slugs the API and the frontend
already use (`/forms/pet-registration`); `submissions.id` is `sub-<uuid>`.
Keeping them as `text` primary keys means the DB-backed repositories return
exactly what the JSON ones return and no URL changes. A `CHECK (btrim(id) <> '')`
makes the historical blank-id bug impossible to store.

Trade-off: renaming a slug later would require cascading the FK (phase 2
could add an immutable surrogate `uuid` if slug renames become a feature).

### D3. Submissions outlive forms: soft-delete forms, `ON DELETE RESTRICT`

Today, deleting a form leaves its submissions in place with a dangling
`formId`. For a records system that is the right *intent* (submissions are
evidence and must never silently disappear) but the wrong *mechanism*. Options:

| Option | Effect |
| ------ | ------ |
| No FK (status quo) | Orphans; no integrity. |
| `ON DELETE CASCADE` | Deleting a form destroys its records. Unacceptable for an app replacing a vendor records product. |
| **`ON DELETE RESTRICT` + `forms.deleted_at`** (chosen) | `DELETE /forms/{id}` marks the form deleted; it disappears from listings and returns 404, while submissions stay queryable (`GET /submissions?formId=`) with the form row still resolvable. The id stays reserved so a new form cannot silently inherit another form's history. |

Repository semantics under soft-delete: `list_forms`/`get_form` filter
`deleted_at IS NULL`; `exists()` must consider *all* rows (so id generation
never reuses a deleted slug); `delete_form` sets `deleted_at` (idempotent: a
second delete returns `False` -> 404). `GET /forms/{id}/submissions` for a
deleted form may return its submissions instead of 404 - a deliberate
improvement.

Option lists are not referenced by anything (values are copied), so they are
hard-deleted.

### D4. `timestamptz` everywhere, the application formats it

All timestamps are stored as `timestamptz` (UTC). The API keeps emitting
`YYYY-MM-DDTHH:MM:SS.mmmZ`; the repository formats on the way out with
`utc_now_iso`-style formatting. Legacy forms without `createdAt`/`updatedAt`
get the import time (columns are `NOT NULL`), so "unknown" is no longer a
state the API has to represent. `updated_at` stays application-managed (the
routers already set it), matching current behaviour; a trigger would fight
the importer, which must preserve the original timestamps.

### D5. Validation stays in the application; the database enforces structure

The database guarantees shape and integrity: non-blank ids and names, JSON
types (`fields` is an array, `data`/`metadata` are objects), at least one
option in a list, case-insensitive unique list names, referential integrity
from submissions to forms. Field types, operators, and logic references remain
the application's job, exactly as now. There are no PostgreSQL enums for
field types or operators: adding a field type must not require a migration.

### D6. Plain SQL migrations, applied the same way everywhere, never edited

`backend/db/migrations/NNNN_name.sql`, each wrapped in a transaction and ending
with an insert into `schema_migrations`. The Postgres container runs them on
first start (mounted into `/docker-entrypoint-initdb.d`); `python -m db.migrate`
applies pending ones to an existing database; the test-suite applies them to a
scratch database, as a fresh install and as an upgrade from every earlier
version, and compares the catalogs. One set of files, three consumers, no ORM
dependency. Applied files are immutable: the runner stores their checksum and
refuses to continue if one changed. If the team adopts SQLAlchemy later,
Alembic can take over from the same baseline.

### D7. Single `public` schema, single application role (for now)

An internal, single-tenant app. Per-module schemas and a read-only reporting
role are easy to add later; an unprivileged application role is recommended
for production (see `db/README.md`).

---

## 3. Phase 1 - schema for the current data (implemented)

```
+-----------------+        +-------------------------+        +------------------+
| forms           |        | submissions             |        | option_lists     |
|-----------------|        |-------------------------|        |------------------|
| id          PK  |<---+   | id                  PK  |        | id           PK  |
| seq  (identity) |    |   | seq  (identity)         |        | name  uniq(lower)|
| name            |    +---| form_id   FK RESTRICT   |        | description      |
| description     |        | form_name  (snapshot)   |        | options   text[] |
| fields    jsonb |        | data       jsonb  (GIN) |        | created_at       |
| created_at      |        | submitted_at  (idx)     |        | updated_at       |
| updated_at      |        | submitted_by            |        +------------------+
| deleted_at NULL |        | source                  |
+-----------------+        | user_agent              |
                           | ip_address              |        +------------------+
                           | hidden_fields text[]    |        | schema_migrations|
                           | metadata   jsonb extras |        |------------------|
                           +-------------------------+        | version      PK  |
                                                              | applied_at       |
                                                              +------------------+
```

The DDL is in `backend/db/migrations/0001_initial.sql` (tables) and
`0002_forms_seq.sql` (`forms.seq`, `schema_migrations.checksum`). Key points:

* `forms`: `id text PK`, `seq` (identity; `GET /forms` lists in creation
  order, matching the JSON file order), `name`, `description`, `fields jsonb`
  (array check), `created_at`, `updated_at`, `deleted_at`. Partial index on
  `seq WHERE deleted_at IS NULL` for the listing query.
* `option_lists`: `id text PK`, `name` (unique on `lower(name)`, <= 120 chars),
  `description`, `options text[]` (>= 1 element), timestamps.
* `submissions`: `id text PK`, `seq` (identity, tie-breaker for equal
  timestamps = "later in the file is newer"), `form_id -> forms ON DELETE RESTRICT`,
  `form_name`, `data jsonb` (object check, GIN `jsonb_path_ops`),
  `submitted_at`, `submitted_by`, `source`, `user_agent`, `ip_address` (`text`,
  because proxy headers are not guaranteed to be valid `inet`), `hidden_fields
  text[]`, `metadata jsonb` (client extras only, object check). Composite index
  `(form_id, submitted_at DESC, seq DESC)` and `(submitted_at DESC, seq DESC)`
  serve the two listing endpoints.
* `schema_migrations`: `version text PK`, `applied_at`, `checksum` (sha256 of
  the file as applied by `db.migrate`; drift detection).

Mapping JSON -> rows is implemented once, in `backend/db/import_json.py`, and
is what the DB repositories will reuse.

Deviation from JSON behaviour worth noting: `GET /forms/{id}` for a deleted
form returns 404 (same), but the id can no longer be reused for a new form
(409 instead of silently creating a namesake).

---

## 4. Phase 2 - authentication and form versions

Triggered by "Authentication, so `submittedBy` is populated from the signed-in
user" and by the need to render old submissions against the field definitions
that existed when they were made.

```sql
CREATE TABLE users (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text        NOT NULL,
    display_name  text        NOT NULL,
    external_id   text,                          -- subject from SSO / IdP, if any
    is_active     boolean     NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_unique ON users (lower(email));
CREATE UNIQUE INDEX users_external_id_unique ON users (external_id) WHERE external_id IS NOT NULL;

-- Ownership / attribution. Nullable so existing rows and anonymous submissions stay valid.
ALTER TABLE forms        ADD COLUMN created_by uuid REFERENCES users(id),
                         ADD COLUMN updated_by uuid REFERENCES users(id);
ALTER TABLE option_lists ADD COLUMN created_by uuid REFERENCES users(id),
                         ADD COLUMN updated_by uuid REFERENCES users(id);
ALTER TABLE submissions  ADD COLUMN submitted_by_user_id uuid REFERENCES users(id);
-- `submissions.submitted_by` (text) stays as the display-name snapshot.

-- Immutable snapshot of a form's definition at each save.
CREATE TABLE form_versions (
    id          bigserial   PRIMARY KEY,
    form_id     text        NOT NULL REFERENCES forms(id) ON DELETE RESTRICT,
    version     integer     NOT NULL,
    name        text        NOT NULL,
    description text        NOT NULL DEFAULT '',
    fields      jsonb       NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid        REFERENCES users(id),
    UNIQUE (form_id, version)
);
ALTER TABLE submissions ADD COLUMN form_version_id bigint REFERENCES form_versions(id);
```

Sessions: if the app issues its own sessions, add `sessions (id, user_id,
expires_at, created_at)`; if SSO issues JWTs, nothing is stored. Roles: start
with a `role text` column on `users` (`admin | builder | submitter`), promote
to a `roles`/`user_roles` pair only when per-form permissions are needed.

When `form_versions` lands, the DB repository writes a version row inside the
same transaction as the form update, and `build_submission` records the
current version id. Back-fill: one version per existing form from its current
`fields`; existing submissions keep `form_version_id = NULL`.

## 5. Phase 3 - data tables

> **Superseded by [data-tables.md](data-tables.md).** The sketch below kept
> the definition as a JSON document and rows as JSONB; the module is built
> instead on **real PostgreSQL tables** (one per user table, in schema
> `user_tables`) with a relational registry. `0003_data_tables.sql` is
> implemented. The reasoning is in data-tables.md section 1.

"Editing data tables" in a vendor-replacement tool usually means user-defined
tables (columns with types, rows edited in a grid), often filled by form
submissions. The forms pattern applies directly: the **definition** is a
document, the **rows** are JSONB records.

```sql
CREATE TABLE data_tables (
    id          text        PRIMARY KEY,                -- slug, like forms
    name        text        NOT NULL,
    description text        NOT NULL DEFAULT '',
    columns     jsonb       NOT NULL DEFAULT '[]',      -- [{id, name, type, required, options?, ...}]
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz,
    CHECK (jsonb_typeof(columns) = 'array')
);

CREATE TABLE data_table_rows (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    table_id    text        NOT NULL REFERENCES data_tables(id) ON DELETE RESTRICT,
    data        jsonb       NOT NULL DEFAULT '{}',      -- {columnId: value}
    position    bigint,                                 -- optional manual ordering
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid REFERENCES users(id),
    updated_by  uuid REFERENCES users(id),
    deleted_at  timestamptz,
    CHECK (jsonb_typeof(data) = 'object')
);
CREATE INDEX data_table_rows_table_idx ON data_table_rows (table_id, updated_at DESC);
CREATE INDEX data_table_rows_data_gin  ON data_table_rows USING gin (data jsonb_path_ops);

-- Optional link so a form can write into a table (form field -> table column mapping).
CREATE TABLE form_table_bindings (
    form_id     text  NOT NULL REFERENCES forms(id),
    table_id    text  NOT NULL REFERENCES data_tables(id),
    mapping     jsonb NOT NULL,                         -- {fieldId: columnId}
    PRIMARY KEY (form_id, table_id)
);
```

Column types should mirror the form field types (`text`, `number`, `date`,
`select`, `checkbox`, ...) so one option library and one rules engine serve
both. If grids grow to hundreds of thousands of rows with heavy filtering,
generated columns or expression indexes on `data ->> 'colId'` can be added
per table without changing the model. Physical per-table DDL (one real SQL
table per user table) is the alternative; it indexes better but makes schema
changes user-driven DDL - not recommended until there is a measured need.

## 6. Phase 4 - workflows

A workflow is a definition (trigger + steps, edited in a designer) plus its
runs. Definitions are documents; runs and tasks are rows because they are
queried by status, assignee and due date.

```sql
CREATE TABLE workflows (
    id          text        PRIMARY KEY,
    name        text        NOT NULL,
    description text        NOT NULL DEFAULT '',
    trigger     jsonb       NOT NULL,     -- e.g. {"type":"form_submitted","formId":"incident-report"}
    definition  jsonb       NOT NULL,     -- steps, transitions, conditions (reuses the rules engine)
    is_active   boolean     NOT NULL DEFAULT false,
    version     integer     NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

CREATE TABLE workflow_runs (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     text        NOT NULL REFERENCES workflows(id) ON DELETE RESTRICT,
    workflow_version integer    NOT NULL,
    status          text        NOT NULL,            -- pending | running | waiting | completed | failed | cancelled
    submission_id   text        REFERENCES submissions(id),     -- what triggered it (nullable: manual/scheduled)
    table_row_id    uuid        REFERENCES data_table_rows(id),
    context         jsonb       NOT NULL DEFAULT '{}',
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    error           text
);
CREATE INDEX workflow_runs_status_idx ON workflow_runs (status, started_at DESC);

CREATE TABLE workflow_tasks (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        uuid        NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_id       text        NOT NULL,              -- key of the step in workflows.definition
    kind          text        NOT NULL,              -- approval | form | notification | ...
    status        text        NOT NULL,              -- open | completed | skipped | cancelled
    assignee_id   uuid        REFERENCES users(id),
    due_at        timestamptz,
    completed_at  timestamptz,
    completed_by  uuid        REFERENCES users(id),
    outcome       jsonb,                              -- decision, comment, linked submission id ...
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workflow_tasks_assignee_open_idx ON workflow_tasks (assignee_id, due_at) WHERE status = 'open';
```

Runs cascade to tasks (a run *owns* its tasks); nothing else cascades.

## 7. Cross-cutting: audit log

Useful from phase 2 onwards (who changed which form, who approved what):

```sql
CREATE TABLE audit_log (
    id          bigserial   PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_id    uuid        REFERENCES users(id),
    entity_type text        NOT NULL,    -- form | option_list | submission | data_table_row | workflow_task ...
    entity_id   text        NOT NULL,
    action      text        NOT NULL,    -- create | update | delete | submit | approve ...
    changes     jsonb                    -- before/after or a diff
);
CREATE INDEX audit_log_entity_idx ON audit_log (entity_type, entity_id, occurred_at DESC);
```

Written by the repositories (application-level) rather than by triggers, so
the actor is known.

## 8. Migration strategy and next steps

1. **Done:** phase-1 DDL, Docker Compose for Postgres, migration runner,
   JSON -> Postgres importer, reproducibility test.
2. **Done:** `repositories/pg_forms.py`, `pg_option_lists.py`,
   `pg_submissions.py` against the `Protocol`s in `repositories/base.py`,
   wired in `core/dependencies.py`. Database failures are reported with the
   PostgreSQL message (`core/database.py`).
3. **Done:** the JSON file backend is removed; `DATABASE_URL` is required.
   `db/import_json.py` stays to load the seed files / file backups.
4. Phase 2+ migrations are added as `0002_*.sql`, `0003_*.sql` ... and applied
   with the same runner.
5. **In progress:** phase 3 (data tables) - `0003_data_tables.sql`, see
   [data-tables.md](data-tables.md#8-build-plan).

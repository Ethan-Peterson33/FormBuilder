# Data tables module - design

User-defined tables edited in a grid. **Every data table is a real PostgreSQL
table**: creating one runs `CREATE TABLE`, adding a column runs
`ALTER TABLE ... ADD COLUMN`, deleting one runs `DROP TABLE`. Rows are rows.
Nothing about a table's structure is stored as a JSON document.

Status: **definitions, rows and querying are live** - `/tables` (CREATE /
ALTER / DROP TABLE through the API, consistency check) and
`/tables/{ref}/rows` (typed cells, filter / sort / page) for user tables
**and any other allowed table**. See [Build plan](#8-build-plan) for what
is left.

Contents

1. [Principles](#1-principles)
2. [Where it lives](#2-where-it-lives)
3. [Schema](#3-schema)
4. [Domain model and type mapping](#4-domain-model-and-type-mapping)
5. [Behaviour](#5-behaviour)
6. [API (planned)](#6-api-planned)
7. [Sorting, filtering, paging (planned)](#7-sorting-filtering-paging-planned)
8. [Build plan](#8-build-plan)
9. [Decisions to confirm](#9-decisions-to-confirm)

---

## 1. Principles

* **Real tables, real constraints.** A user table is `user_tables.<name>`
  with typed columns, `NOT NULL` for required columns and a `CHECK` for the
  allowed values of a select column. PostgreSQL enforces them on every write
  path, including someone using `psql`. Reporting tools and SQL work on the
  tables directly.
* **The catalog is the source of truth for structure.** The registry
  (`data_tables`, `data_table_columns`) adds only what the catalog cannot
  express: the API slug, display names, the API column type (`textarea` and
  `select` are both `text` in SQL), option lists and column order. Registry
  and DDL change in **one transaction** - PostgreSQL DDL is transactional -
  so they cannot drift through the API; a health/`check_db` probe compares
  them to catch hand-made changes.
* **Changes are refused by the data, not by our code.** Making a column
  required while it holds NULLs, changing `text` to `number` with a
  non-numeric value, removing an option that is in use - PostgreSQL rejects
  the `ALTER TABLE`, and the API reports that as `409` with the PostgreSQL
  message and the column. No silent conversion or data loss.
* **No SQL from user text.** Table and column names are validated
  (`services/table_rules.py`), re-checked by `CHECK` constraints in the
  registry, and always rendered through `psycopg.sql.Identifier`; option
  values are `sql.Literal`s; cell values are query parameters.
* **One vocabulary with forms.** Column types are a subset of form field
  types, so the operator table, value coercion and the option library serve
  both, and a later form -> table binding is a `fieldId -> columnId` mapping.
* **The row API is catalog-driven.** It reads a table's columns, SQL types,
  `NOT NULL`, defaults, identity/generated columns, primary key and option
  `CHECK`s from `information_schema` / `pg_constraint` - so it works on any
  allowed table (`public.option_lists`, `public.forms`, ...) exactly as on
  the user tables, and the types it enforces are the PostgreSQL column types.
* **PostgreSQL only.** The JSON file backend is gone; `DATABASE_URL` is
  required. `db/import_json.py` remains to load the seed files.

Why not the JSONB-document design from the schema plan? It would have made
"table" a presentation over a blob: no per-column types or constraints in the
database, no plain SQL access for reporting, and every query a `jsonb`
expression. The vendor product being replaced exposes real tables, and so
does this.

## 2. Where it lives

Same layering as forms (`models` -> `services` -> `repositories` Protocol ->
`pg_*` -> `routers`, wired in `core/dependencies.py`).

| File | Phase | Contents |
| --- | --- | --- |
| `db/migrations/0003_data_tables.sql` | 1 (done) | schema `user_tables`; registry `data_tables`, `data_table_columns` |
| `services/table_ddl.py` | 1 (done) | `CREATE/ALTER/DROP TABLE` statement builder, type mapping, catalog queries |
| `models/tables.py` | 1 (done) | `TableColumn`, `DataTable*`, `TableRow*`, `ColumnType`, `RowValue` |
| `services/table_rules.py` | 1 (done) | save-time definition rules (identifier-safe column ids, reserved names, options) |
| `services/ids.py` | 1 (done) | `unique_table_id` |
| `db/mapping.py` | 1 (done) | registry rows <-> `DataTable`; user-table row -> `TableRow` (numeric/date conversion) |
| `repositories/base.py` | 1 (done) | `DataTableRepository`, `TableRowRepository`, `DefinitionConflictError` |
| `repositories/pg_tables.py` | 2 (done) | registry + DDL in one transaction; definition diff -> ALTER steps; `verify_registry` catalog check |
| `routers/tables.py` | 2 (done) | `/tables` endpoints |
| `main.py`, `core/database.py` | 2 (done) | `409` / `500` handlers for table errors; consistency check in `/health` |
| `services/values.py` | 3 (done) | value parsers (number, integer, bool, date, timestamp, text, uuid, text list, json); also used by submissions |
| `services/table_catalog.py` | 3 (done) | `TableInfo` / `ColumnInfo` from the catalog; reference resolution and the allowlist |
| `services/table_rows.py` | 3 (done) | validate/coerce row payloads against the column types; row -> API conversion |
| `services/row_query.py` | 4 (done) | `RowQuery` model, query-string parsing, filters/sort/paging -> parameterised SQL |
| `repositories/pg_table_rows.py`, `routers/table_rows.py` | 3 (done) | `SELECT/INSERT/UPDATE/DELETE`; `/tables/{ref}/schema`, `/tables/{ref}/rows[...]` |
| `tests/test_table_models.py`, `tests/test_tables_schema.py` | 1 (done) | models, rules, DDL text, diff -> ALTER steps; registry constraints and DDL executed on PostgreSQL |
| `tests/test_tables_api.py` | 2 (done) | every endpoint; after each change the catalog is compared with the definition; refusals; drift |
| `tests/test_row_query.py`, `tests/test_table_rows_api.py` | 3-4 (done) | parsers, catalog parsing, rendered SQL; rows on user tables and on `public.*` tables, type enforcement, filters, sorting, paging |

## 3. Schema

```
public                                             user_tables
+-------------------------+                        +---------------------------------+
| data_tables             |                        | work_orders          (1 per row |
|-------------------------|                        |  of data_tables)                |
| id     PK slug          |--- table_name -------->|---------------------------------|
| table_name (generated)  |                        | id          bigint identity PK  |
| seq    (identity)       |                        | created_at  timestamptz         |
| name   uniq(lower)      |                        | updated_at  timestamptz         |
| description             |                        | title       text NOT NULL       |
| created_at / updated_at |                        | status      text CHECK (IN ...) |
+-------------------------+                        | cost        numeric             |
            | 1:n (CASCADE)                         | due         date                |
+-------------------------+                        | urgent      boolean NOT NULL    |
| data_table_columns      |                        +---------------------------------+
|-------------------------|
| table_id  FK            |   one registry row per physical column:
| id  (= column name)     |   display name, API type, required, description,
| position                |   options (mirrors the CHECK), order
| name  uniq(table,lower) |
| type / required         |
| description / options   |
+-------------------------+
```

* `data_tables.table_name` is a generated column (`replace(id, '-', '_')`),
  unique, so the slug `work-orders` always means `user_tables.work_orders`
  and two slugs can never share a physical table.
* Column ids are checked by the registry (`^[a-z][a-z0-9_]*$`, <= 48 chars)
  and may not be a system column (`id`, `created_at`, `updated_at`) or a
  PostgreSQL system column name.
* Column order lives in `position` (PostgreSQL cannot reorder columns); the
  unique constraint is deferred so reordering is one transaction.
* Deleting a table is `DROP TABLE` + `DELETE FROM data_tables` (columns
  cascade). Deleting a row is `DELETE`. Both are real deletes - see 9.
* Application role: needs `USAGE, CREATE` on schema `user_tables` (it owns
  the tables it creates). `docs/database.md` has the grants.

## 4. Domain model and type mapping

```jsonc
// DataTable  (GET /tables/{id})
{
  "id": "work-orders",
  "name": "Work Orders",               // 1-120 chars, unique (case-insensitive)
  "description": "",
  "columns": [
    { "id": "title",  "name": "Title",  "type": "text",   "required": true,  "description": "" },
    { "id": "status", "name": "Status", "type": "select", "required": false, "description": "",
      "options": ["Open", "Closed"] }
  ],
  "createdAt": "2026-01-01T09:00:00.123Z",
  "updatedAt": "2026-01-02T10:00:00.456Z"
}

// TableRow  (GET /tables/{id}/rows/{rowId})
{ "id": 42, "tableId": "work-orders",
  "data": { "title": "Leaking tap", "status": "Open", "urgent": false },
  "createdAt": "...", "updatedAt": "..." }
```

| API type | SQL column | Notes |
| --- | --- | --- |
| `text` | `text` | |
| `textarea` | `text` | multi-line editor; same storage |
| `number` | `numeric` | exact; API returns `int` for whole numbers, else `float` |
| `date` | `date` | API value `YYYY-MM-DD` |
| `select` | `text` + `CONSTRAINT <id>_options CHECK (<id> IN (...))` | options unique case-insensitively |
| `checkbox` | `boolean NOT NULL DEFAULT false` | always true/false; `required` does not apply |

`required: true` -> `NOT NULL`. An empty cell is `NULL` and is omitted from
`data` in the API.

**Column ids** are the physical column names: lower-case identifiers chosen
by the client (`status`, `due_date`), immutable. The display `name` can change
freely. SQL keywords (`select`, `order`) are allowed - identifiers are always
quoted.

**Rules on save** (`services/table_rules.py`, all reported at once): id
format/length/reserved, unique ids, non-blank unique names (case-insensitive,
<= 120), `select` needs >= 1 unique option, options only on `select`,
`required` not on `checkbox`, at most 200 columns.

## 5. Behaviour

### 5.1 Definition changes = ALTER TABLE

`PUT /tables/{id}` sends the desired definition; the repository diffs it with
the stored one (columns matched by `id`) and runs, in one transaction:

| Change | SQL | Can fail because of existing rows? |
| --- | --- | --- |
| New column id | `ADD COLUMN` | Yes if `required` and the table has rows (PostgreSQL: `column contains null values`) -> add it optional, fill it, then make it required |
| Column id gone | `DROP COLUMN` | No (its data goes with it - the UI confirms, showing the row count) |
| `type` changed | `ALTER COLUMN TYPE ... USING <col>::<type>` | Yes if a value does not convert |
| `required` changed | `SET NOT NULL` / `DROP NOT NULL` | `SET` fails while NULLs exist |
| `options` changed | drop + re-add the `CHECK` | Fails if a stored value is not among the new options |
| `name`, `description`, order | registry only | No |

A failure rolls back everything (registry and DDL) and the API answers `409`
with the PostgreSQL message and the offending column; the definition is
unchanged. Nothing is rewritten or dropped behind the user's back.

### 5.2 Writing rows

Cells are validated against the **PostgreSQL column type** read from the
catalog (`services/table_catalog.py` -> `services/table_rows.py`):

| Column kind (SQL types) | Accepted input | Stored as |
| --- | --- | --- |
| text (`text`, `varchar`, `char`) | string, number, bool | text |
| integer (`smallint`, `integer`, `bigint`) | whole number or numeric string | integer |
| number (`numeric`, `real`, `double precision`) | number or numeric string | numeric |
| boolean | `true`/`false` or `"true"`/`"false"` | boolean |
| date | `YYYY-MM-DD` (a real date) | date |
| timestamp (`timestamptz`, `timestamp`) | ISO-8601 (`Z` / offset / date-only) | timestamptz |
| json (`json`, `jsonb`) | anything JSON | jsonb |
| text_array (`text[]`) | list of strings | text[] |
| uuid | UUID string | uuid |
| anything else | rejected on write (`422`), shown as text on read | - |

Plus: a select column's option `CHECK` is enforced (`must be one of: ...`);
identity `GENERATED ALWAYS` and generated columns are refused (`is generated
by the database`); unknown keys are refused. Empty input (`null`, blank
text) is NULL, except for a `NOT NULL` text column where blank text is `''`.

* `POST` checks every given cell and requires every `NOT NULL` column
  without a default, then `INSERT ... RETURNING *`. Columns with defaults
  (`created_at`, `source`, ...) may be given or left to the database.
* `PATCH` is **partial**: `UPDATE ... SET` only the named columns; `null`
  sets `NULL` (refused for a `NOT NULL` column). Cells not named are
  untouched, so two people editing different cells of one row never
  overwrite each other. `updated_at` is bumped when the table has one.
* `422 {"message": "Row failed validation", "errors": {column: message}}`
  lists every bad cell at once. A constraint the validation does not know
  about (a hand-made `CHECK`, `UNIQUE`, a foreign key) is reported by
  PostgreSQL as `409 {"message": "The row was refused by a table constraint",
  "table", "column", "constraint", "error", "sqlstate", "hint"}`.

### 5.2a Which tables

`{ref}` is a user table's slug or `schema.table`. Allowed: every table in
`user_tables` and `public`, except `schema_migrations`
(`services.table_catalog.ALLOWED_SCHEMAS` / `HIDDEN_TABLES`). Anything else
is `404`, as is a table that does not exist. Rows are addressed by the
table's single-column primary key (`400` if it has none); the key is coerced
to its type, so `/rows/abc` on an integer key is `404`.

Writing to application tables through the row API applies the database's
constraints only - not the business rules of the feature that owns the table
(e.g. a form's logic rules, or soft delete). That is by design: it is a
table editor, the same as `psql` with type checking and nicer errors.

### 5.3 Drift detection

The API never changes a table without its registry row (one transaction), so
the two can only disagree through changes made by hand. `verify_registry`
(`repositories/pg_tables.py`) compares, for every registered table, the
registry with `information_schema.columns` and `pg_constraint`: table exists,
system columns present, each registered column exists with the expected SQL
type and `NOT NULL`, select columns have a `<id>_options` CHECK with exactly
the registered values, no unregistered columns, no unregistered tables in
`user_tables`.

Where it surfaces:

* `GET /health` -> `503` with `database.error.problems` (one line each).
* `python scripts/check_db.py` -> lists the problems, exit 1.
* `GET /tables/{id}/check` -> `{ok, problems}` for one table.
* A write that trips over the mismatch (e.g. `ALTER COLUMN` on a column that
  was dropped by hand) -> `500 {"message": "Data table does not match its
  registry", "table", "column", "error", "hint"}`. Reads keep serving the
  registered definition so the repair can be planned.

Nothing is repaired automatically: a hand-made change is a decision somebody
took, and the right fix (update the registry or the table) depends on why.

### 5.4 Concurrency

Definition updates are serialised by PostgreSQL's table lock for the
duration of the transaction; two conflicting `PUT`s resolve in order and the
second sees the first's result in its diff. Row edits are column-level
`UPDATE`s (5.2). Optimistic concurrency tokens (`If-Unmodified-Since` on
`updated_at`) can be added later without a schema change.

## 6. API

Table definitions (implemented):

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/tables` | All tables, creation order |
| `GET` | `/tables/{tableId}` | One definition |
| `POST` | `/tables` | `{name, description?, columns?, id?}` -> `201` + `CREATE TABLE`. Id from the name (`-2`...); requested id taken -> `409`; name taken -> `409`; rule problems -> `422` |
| `PUT` | `/tables/{tableId}` | Desired definition -> `ALTER TABLE` steps (5.1); `409` when the data refuses a change. A `GET` response is valid input |
| `DELETE` | `/tables/{tableId}` | `DROP TABLE`, `204` |
| `GET` | `/tables/{tableId}/check` | `{tableId, ok, problems[]}` - registry vs. catalog for this table |

Error bodies:

| Status | `detail` |
| --- | --- |
| `404` | `"Table not found"` |
| `409` id/name taken | text, e.g. `"A table named 'Work Orders' already exists"` |
| `409` data refuses the change | `{"message": "The table's data does not allow this change", "table", "column", "error": <PostgreSQL message>, "sqlstate", "hint"}` |
| `422` | list of `{type, loc, msg}` (pydantic structure errors, or one entry per rule problem with `loc: ["body", "columns"]`) |
| `500` registry/table mismatch | `{"message": "Data table does not match its registry", "table", "column", "error", "hint"}` |

Example of a refused change (`text` -> `number` with "n/a" in a cell):

```json
HTTP 409
{"detail": {"message": "The table's data does not allow this change",
            "table": "work-orders", "column": "notes",
            "error": "invalid input syntax for type numeric: \"n/a\"", "sqlstate": "22P02",
            "hint": "Some stored values cannot be converted to the new type; fix or clear them first, or keep the type."}}
```

Rows (implemented; `{ref}` = slug or `schema.table`):

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/tables/{ref}/schema` | `{ref, schema, table, primaryKey, columns: [{id, sqlType, kind, nullable, hasDefault, writable, primaryKey, options}]}` - the table as the catalog describes it |
| `GET` | `/tables/{ref}/rows?limit=&offset=&sort=&filter=&match=&search=` | `{total, limit, offset, items}` (section 7) |
| `POST` | `/tables/{ref}/rows/query` | Same, with a JSON `RowQuery` body: `{filters: [{column, operator, value}], match, sort: [{column, direction}], search, limit, offset}` |
| `GET` | `/tables/{ref}/rows/{rowId}` | One row: `{id, tableId, data, createdAt, updatedAt}` |
| `POST` | `/tables/{ref}/rows` | `{data}` -> `201` with the stored row (defaults applied) |
| `PATCH` | `/tables/{ref}/rows/{rowId}` | `{data: partial}`; `null` clears |
| `DELETE` | `/tables/{ref}/rows/{rowId}` | `204` |

Row errors: `404` table/row, `400` no single-column primary key, `422
{"message", "errors": {cell or query part: message}}`, `409` constraint (above).

## 7. Sorting, filtering, paging

Query string (`GET .../rows`) or JSON body (`POST .../rows/query`); both
become a `RowQuery` (`services/row_query.py`).

* **Paging:** `limit` (default 100, max 1000, `0` allowed for "count only"),
  `offset`. The response carries `total` for the whole match.
* **Sorting:** `sort=status,-cost` (`-` = descending; body: `[{column,
  direction}]`). Any column of the table, `NULLS LAST`; the primary key is
  always appended so pages are stable. `json` columns cannot be sorted.
* **Filtering:** `filter=column:operator[:value]`, repeatable (the value is
  everything after the second colon; `is_one_of` takes a comma-separated
  list); `match=all|any`. Operators are the form rules engine's, allowed per
  column kind:

  | kind | operators |
  | --- | --- |
  | text | `equals`, `not_equals` (case-insensitive), `contains`, `not_contains`, `starts_with`, `ends_with` (ILIKE, wildcards escaped), `is_one_of`, `is_not_one_of`, `is_empty`, `is_not_empty` (NULL or blank) |
  | integer, number | `equals`, `not_equals`, `greater_than`, `greater_than_or_equal`, `less_than`, `less_than_or_equal`, `is_one_of`, `is_not_one_of`, `is_empty`, `is_not_empty` |
  | date, timestamp | the above plus `before`, `after`, `on_or_before`, `on_or_after` |
  | boolean | `is_checked`, `is_not_checked`, `equals`, `not_equals`, `is_empty`, `is_not_empty` |
  | text_array | `contains`, `not_contains` (element), `is_empty`, `is_not_empty` |
  | json, unknown | `is_empty`, `is_not_empty` |

  Values are coerced to the column type (a `number` filter with `lots` is
  `422`). Negative operators match NULL cells, like the rules engine.
* **Search:** `search=text` - case-insensitive substring over every text
  column, combined with the filters.
* Every column name is resolved against the catalog description and rendered
  with `sql.Identifier`; every value is a parameter. Problems in filters and
  sort are reported together: `422 {"message": "Invalid query", "errors":
  {"filters[1]": "...", "sort[0]": "..."}}`.
* Indexes: `CREATE INDEX` per column can be offered later (`indexed: true`
  in `TableColumn`) - one more ALTER step, no model change.

## 8. Build plan

Each phase is reviewable and shippable on its own, with tests on the real
schema.

1. **Foundation - done.** JSON storage removed (`DATABASE_URL` required;
   importer reads the files itself). Migration `0003_data_tables` (schema
   `user_tables`, registry). `services/table_ddl.py` with every statement
   exercised against PostgreSQL in `tests/test_tables_schema.py`, including
   the refusals in 5.1. Models, rules, mapping, repository contracts.
   Existing API unchanged; the full suite passes.
2. **Table definitions - done.** `PgDataTableRepository` (create, diff ->
   ALTER steps, delete, `verify_registry`), `/tables` router, `409`/`500`
   handlers, consistency check in `/health`, `check_db.py` and
   `/tables/{id}/check`, `smoke_test.py` coverage, `tests/test_tables_api.py`
   (catalog compared with the definition after every change).
3. **Rows - done.** Catalog-driven table description (`table_catalog`),
   shared value parsers (`values`, also used by submissions), row validation
   against the column types, `PgTableRowRepository`, row endpoints for user
   tables and `public.*` tables, `409` handler for constraints.
4. **Querying - done.** `RowQuery`, query-string and JSON forms, filters by
   column kind, sorting with stable paging, search.
5. **Later, separately designed:** bulk paste / CSV import-export, form ->
   table bindings, attribution and audit once users exist, per-column
   indexes, frontend grid.

## 9. Decisions to confirm

Built into the foundation (1-5) or the next phases (6-8). 1-5 are free to
change **until `0003` is applied to a shared database** (`./start.sh` applies
pending migrations automatically); after that they need a new migration.

1. **Deletes are real.** `DROP TABLE` for tables, `DELETE` for rows; a
   deleted slug/name can be reused. (Forms are soft-deleted because
   submissions reference them; nothing references a data table yet.)
   Alternative: soft-delete rows with a `deleted_at` system column.
2. **Column ids are lower-case identifiers** (`due_date`, not `dueDate`)
   because they are the real column names. The grid shows `name`, not `id`.
3. **`checkbox` is `boolean NOT NULL DEFAULT false`** - never "unknown".
4. **`number` is `numeric`**, not `double precision` (exact money/quantities).
5. **Table names are unique** (case-insensitive) - the grid lists by name.
6. **A refused change is a `409`, never a conversion.** E.g. `text` ->
   `number` with "n/a" in a cell fails with the cell named; the user fixes the
   data or keeps the type.
7. **Dropping a column drops its data** (after a UI confirmation with the row
   count). Alternative: keep the column and mark it hidden in the registry.
8. **Options on non-`select` columns are rejected** rather than silently
   dropped (the form builder tolerates them).

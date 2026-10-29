<<<<<<< HEAD
# Form, Table, and Workflow Builder

An internal application for building forms, editing data tables, and designing workflows. The project uses a Next.js frontend and a FastAPI backend. It is intended to replace a vendor product for company use.

## Project status

Form building and submissions are the current focus. Table editing and workflow features are part of the broader project. The backend stores its data in PostgreSQL (`docker-compose.yml`); see [docs/database.md](docs/database.md) for running it and [docs/database-schema-plan.md](docs/database-schema-plan.md) for the schema and its roadmap. PostgreSQL is the only storage: database problems are reported as errors, never worked around. The data tables module (user tables as real PostgreSQL tables) is in progress - design in [docs/data-tables.md](docs/data-tables.md).

**Implemented**

- Save and retrieve form configurations through the backend API (create, read, update, delete).
- Conditional field logic: show or hide a field based on answers to earlier fields (equals, contains, numeric comparisons, date comparisons, checkbox state, "is one of", and more; combine with *all* / *any*).
- Submissions saved with metadata (timestamp, submitter, source, user agent, IP, fields hidden by logic) and retrievable per form or across all forms.
- Submissions view with client-side filtering.
- Landing page that routes to Forms, the Form Builder, Submissions and the Option Library.
- Option library: dropdown/radio options are edited as rows (add, edit, delete, reorder) and can be saved as named reusable lists, loaded into any choice field, and managed from a modal in the builder. Lists persist in the `option_lists` table.
- Option Library page (`/forms/option-lists`): view, search (names, descriptions and option values), create, rename, edit, duplicate and delete saved lists outside the form builder. Deep-linkable with `?id=`, and shows which form fields carry an identical copy of a list's options.
- PostgreSQL storage: `docker-compose.yml` (Postgres 16), SQL migrations, a migration runner, and an importer for the JSON seed files / file backups. `/health` reports database connectivity and pending migrations; database failures return the real PostgreSQL error (`503` unreachable, `500` schema problem). The API test-suite runs against a throw-away PostgreSQL database.
- Data tables: `/tables` creates, alters and drops **real PostgreSQL tables** (schema `user_tables`) from a column definition; changes the existing data does not allow are refused with `409` and the PostgreSQL message; `/health`, `check_db.py` and `/tables/{id}/check` report any table that no longer matches its definition.
- Table rows: `/tables/{ref}/rows` reads, inserts, patches and deletes rows of user tables **and of any other allowed table** (`public.option_lists`, ...), enforcing the PostgreSQL column types, with filtering, sorting, search and paging. Design: [docs/data-tables.md](docs/data-tables.md).
- API console: `http://localhost:8000/ui/` (the backend serves it; no frontend needed) - pages for health, tables (column editor -> CREATE / ALTER TABLE, consistency check), data (grid over any exposed table with filters, sorting, paging, inline edit, insert, delete), SQL (editor, results, rules), forms (+ submit), option lists, submissions and a raw request panel. Every request and response is shown in the footer.
- SQL endpoint: `POST /sql/query` runs one `SELECT`/`WITH`/`EXPLAIN`/`INSERT`/`UPDATE` statement and returns a grid-shaped result; `ALTER`, `DROP`, `DELETE`, `TRUNCATE`, `CREATE` and other dangerous statements are refused, reads run in a read-only transaction with a row cap and a timeout. Specification of the table management, data management and SQL pages: [docs/admin-pages.md](docs/admin-pages.md).

## Configuration

Copy `frontend/.env.example` to `frontend/.env.local` and adjust if your backend runs elsewhere:

```env
FASTAPI_URL=http://127.0.0.1:8000
NEXT_PUBLIC_API_URL=http://localhost:8000
```

`FASTAPI_URL` is the server-side URL the Next.js route handlers (`/api/*`) use to reach FastAPI. The browser never calls FastAPI directly. If the variable is missing, `http://127.0.0.1:8000` is assumed.

Backend settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | - | `postgresql://USER:PASSWORD@HOST:PORT/DBNAME`; required, the backend refuses to start without it. `.env.example` has the value matching `docker-compose.yml`. |
| `FORMBUILDER_DB_TIMEOUT` | `5` | Seconds a request waits for a database connection before failing with `503`. |
| `FORMBUILDER_DATA_DIR` | `backend/data` | JSON seed files read by `python -m db.import_json`. |
| `FORMBUILDER_IMAGE_DIR` | `backend/images` | Static images. |
| `FORMBUILDER_PYTHON` | - | Interpreter `scripts/start.py` builds the virtualenv with (default: newest 3.11-3.13 found). |
| `FORMBUILDER_API_PORT` | `8000` | Port `scripts/start.py` serves on. |

The backend reads `backend/.env` then `.env` (repository root) at start-up;
variables already set in the environment win. `./start.sh` maintains the root
`.env`; to do it by hand, copy `.env.example`. Details in [docs/database.md](docs/database.md).

## Run locally

### Backend + database: one command

After cloning or pulling new code:

```bash
./start.sh          # macOS / Linux / WSL / Git Bash
start.cmd           # Windows
```

`scripts/start.py` does everything the backend needs, in order, and stops
with a clear message at the first thing that is wrong:

1. **Python** - finds 3.11-3.13 (prefers the newest; never 3.14 unless you ask
   with `--python` or `FORMBUILDER_PYTHON`), creates `backend/.venv` with it
   (rebuilding it if it was made with another version) and installs
   `backend/requirements.txt` when they changed.
2. **PostgreSQL** - tries, in order: `--database-url`, `DATABASE_URL` in the
   environment, `DATABASE_URL` in `.env`, **every running Docker container
   with a postgres image** (whatever directory it was started from, using the
   port and credentials from `docker inspect`), then `localhost:55432` and
   `localhost:5432` with the default credentials. Each is tested with a real
   connection and the PostgreSQL error is shown for the ones that fail.
   A container that is running but still initialising is waited for. If
   nothing answers but a **stopped** postgres container exists, it is started
   with `docker start` and waited for. Only if no container exists at all is
   this repository's `docker compose` service started. If the Docker daemon is
   not running, that is said explicitly.
3. **`.env`** - rewritten so `DATABASE_URL` / `POSTGRES_*` point at the
   instance that answered. The backend, `pytest` and the `db.*` tools read
   `.env` themselves, so nothing needs exporting afterwards.
4. **Migrations** - applies pending ones; refuses if an applied file was edited.
5. **Data** - imports `backend/data/*.json` once, if the database is empty.
6. **Check** - the same verification as `scripts/check_db.py`.
7. **API** - `uvicorn main:app --reload` on port 8000 (`--port` to change),
   waits for `/health`, prints the result, runs until Ctrl+C.

Useful flags: `--no-serve` (set up only, e.g. CI), `--no-docker`,
`--no-start-containers` (only use what is already running), `--container NAME`
(use that container only), `--database-url URL`, `--recreate-venv`,
`--reinstall`, `--no-import`, `--no-migrate`. `--help` lists all.

### Verifying separately

```bash
python scripts/check_db.py        # connection, migrations (applied/pending/MODIFIED), schema, row counts
python scripts/check_health.py    # GET /health, /forms, /option-lists, /submissions on the running API
python scripts/smoke_test.py      # every endpoint end-to-end (creates and removes a smoke-test form/list)
```

All print `RESULT: OK` / `RESULT: ...` with the real error and exit non-zero
on problems. `check_db.py` names stopped containers when it cannot connect.

### By hand

```bash
cp .env.example .env                      # POSTGRES_PORT defaults to 55432 (5432 is often taken)
docker compose up -d                      # first start creates the schema from backend/db/migrations
cd backend
python3.13 -m venv .venv && . .venv/bin/activate   # Windows: py -3.13 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python -m db.migrate                      # existing database: apply new migrations
python -m db.import_json                  # one-off: load the seed data/*.json (safe to repeat)
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# frontend
cd frontend
npm install
npm run dev -- --hostname 0.0.0.0
```

Open http://localhost:3000. `curl localhost:8000/health` shows the database status, and
http://localhost:8000/ui/ is the backend's own console for exercising every endpoint.

## Tests

```bash
# backend: with DATABASE_URL (environment or .env) every API test runs against
# a throw-away PostgreSQL database (created, migrated and dropped by the
# test-suite), plus schema/upgrade reproducibility, JSON import round trip,
# data-table DDL, database error handling and the start-up scripts.
# Without DATABASE_URL only the pure unit tests run (rules engine, models, scripts).
cd backend
.venv/bin/python -m pytest          # Windows: .venv\Scripts\python -m pytest

# frontend (rules engine, components, API route handlers)
cd frontend
npm test            # vitest run
npm run lint        # eslint
npm run typecheck   # next typegen + tsc
```

## Architecture

```
backend/
  main.py                 FastAPI app factory; mounts routers and the console (/ui)
  ui/                     API console: index.html, app.js, styles.css (vanilla JS, no build step)
  core/
    config.py             Settings (DATABASE_URL, timeout), data/image directories
    database.py           PostgreSQL connection pool, health check, error -> HTTP status/detail mapping
    dependencies.py       hands the repositories to the routers
  models/
    forms.py              FieldConfig / FieldLogic / Condition / FormConfig (pydantic, camelCase JSON)
    submissions.py        Submission / SubmissionMetadata / SubmissionCreate
    option_lists.py       OptionList (named reusable option lists)
    tables.py             TableColumn / DataTable / TableRow (data tables; see docs/data-tables.md)
  repositories/
    base.py               repository protocols (forms, submissions, option lists, data tables, table rows)
    pg_forms.py           forms (soft delete; submissions outlive forms)
    pg_submissions.py     submissions
    pg_option_lists.py    option lists
    pg_tables.py          data tables: registry + CREATE/ALTER/DROP TABLE in one transaction; verify_registry
    pg_table_rows.py      rows of any allowed table: SELECT/INSERT/UPDATE/DELETE, constraint errors -> 409
    pg_sql.py             the SQL page: read-only transaction + row cap + timeout, grid-shaped results
  services/
    form_logic.py         conditional logic evaluation (mirror of frontend/lib/formLogic.ts)
    form_rules.py         business rules checked when a form is saved (options, logic references)
    submissions.py        validate answers against the form, strip hidden fields, add metadata
    table_ddl.py          CREATE / ALTER / DROP TABLE statements for user tables (schema user_tables)
    table_rules.py        business rules checked when a data table definition is saved
    table_catalog.py      describe any allowed table from the PostgreSQL catalog (types, keys, constraints)
    table_rows.py         validate/coerce row cells against the column types; row -> API conversion
    row_query.py          filters / sort / paging -> parameterised SQL
    sql_guard.py          tokenises a statement and decides whether the SQL page may run it
    values.py             value parsers shared by submissions and table rows
    ids.py                slug / id / timestamp helpers
  routers/
    forms.py              /forms endpoints
    submissions.py        /forms/{id}/submissions and /submissions endpoints
    option_lists.py       /option-lists endpoints
    tables.py             /tables endpoints (data table definitions)
    table_rows.py         /tables/{ref}/schema and /tables/{ref}/rows endpoints (any allowed table)
    sql.py                /sql/query, /sql/tables, /sql/rules
    images.py             /images listing
  db/
    migrations/*.sql      PostgreSQL schema, one transaction per file, self-recording in schema_migrations; never edited once applied
    migrate.py            python -m db.migrate - apply pending migrations (--status: applied / pending / MODIFIED)
    import_json.py        python -m db.import_json - load the seed data/*.json (or a file backup) into PostgreSQL
    mapping.py            pydantic model <-> table row conversion (used by pg_* repositories and the importer)
  data/
    forms.json            seed data / import source
    submissions.json
    option_lists.json
  tests/                  pytest suite; the API tests need DATABASE_URL (scratch database per session)

start.sh / start.cmd                      one-command start-up -> scripts/start.py
scripts/
  start.py                                python, venv, requirements, PostgreSQL discovery, .env, migrations, data, API
  check_db.py                             verify database connection / migrations / schema
  check_health.py                         verify the running API
  smoke_test.py                           every endpoint end-to-end against a running API
  _common.py, _postgres.py                helpers (env files, interpreter search, docker discovery)
docker-compose.yml                        PostgreSQL 16 on port 55432 (+ Adminer with --profile tools)
.env.example                              container credentials and DATABASE_URL
docs/database.md                          running, migrating, importing, resetting the database
docs/database-schema-plan.md              schema design for current data and planned features
docs/data-tables.md                       data tables module: design, schema, build plan
docs/admin-pages.md                       table management / data management / SQL pages: specification, SQL endpoint

frontend/
  app/page.tsx                          landing page
  app/forms/                            forms list, form fill page, builder, submissions views
  app/api/                              Next.js route handlers proxying to FastAPI
  app/tools/pdf/                        PDF export experiment (moved from the old home page)
  components/FormBuilder.tsx            builder (create + edit via /forms/builder?id=...)
  components/FieldEditor.tsx            per-field settings
  components/LogicEditor.tsx            conditional logic editor (shows stale references explicitly)
  components/OptionListEditor.tsx       row-based add/edit/delete/reorder editor for options
  components/ManageOptionsModal.tsx     option library modal in the builder (save / load / edit lists)
  components/OptionLibrary.tsx          option library page (search / create / edit / duplicate / delete)
  components/OptionListForm.tsx         name + description + options form shared by modal and page
  lib/options.ts                        option validation, usage lookup, rename propagation into rules
  lib/optionLists.ts                    list draft helpers, search, "where used" matching
  lib/optionListsApi.ts                 browser client for /api/option-lists
  components/FormRender.tsx             renders a FormConfig, evaluates logic live, submits
  components/SubmissionsTable.tsx       filterable submissions table
  lib/types.ts                          shared types (mirror of backend/models)
  lib/formLogic.ts                      conditional logic engine (pure functions)
  lib/server/backend.ts                 server-side proxy helper
  __tests__/                            vitest suite
```

Routers depend on the repository *protocols* in `repositories/base.py`; `core/dependencies.py` hands them the PostgreSQL implementations. Tests substitute small doubles through `app.dependency_overrides` to reach error branches.

### Storage errors

PostgreSQL is the only storage; nothing is papered over:

| Situation | Behaviour |
| --- | --- |
| `DATABASE_URL` missing | start-up fails with `StorageConfigurationError` |
| database unreachable / wrong credentials / unknown database | every data request answers `503` `{"detail": {"message": "Database unavailable", "type", "error", "hint"}}` with the PostgreSQL message; `/health` answers `503`; the API recovers on its own when the database is back |
| schema missing or out of date | `500` `{"detail": {"message": "Database schema is missing or out of date", "sqlstate": "42P01", "error": "relation \"forms\" does not exist", "hint": "Run `python -m db.migrate` ..."}}`; `/health` lists `pendingMigrations` |
| any other PostgreSQL error | `500` with `type`, `sqlstate`, `error`, and `constraint` when applicable |

All of them are also logged by the backend. `DELETE /forms/{id}` is a soft delete: the form disappears from the API but its submissions stay listable, and the id is not reused.

## API

All endpoints are exposed by FastAPI and proxied 1:1 by Next.js under `/api/...`.

### Forms

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/forms` | All form configurations (full field definitions included) |
| `GET` | `/forms/{id}` | One form, `404` if unknown |
| `POST` | `/forms` | Create. `id` optional - generated from the name (`resident-inspection`, `-2`, `-3`...). Explicit duplicate id -> `409` |
| `PUT` | `/forms/{id}` | Replace a form's configuration (id and `createdAt` preserved) |
| `DELETE` | `/forms/{id}` | Remove a form (`204`) |

Validation (`422`) covers: missing name, duplicate field ids, unknown field types, choice fields without options or with duplicate options, and conditional logic that references a later/unknown field, uses an operator that does not suit the source field type, lacks a comparison value, or compares a select/radio field against a value that is not one of its options.

Business rules are enforced **when saving** (`services/form_rules.py`), never when loading. A form stored before a rule existed still loads, renders and accepts submissions; opening it in the builder shows exactly what needs fixing. On startup the form repository also repairs records that early builder versions saved without a usable id (blank or duplicate `id`) by assigning a unique slug from the form name and logging the change.

### Option lists

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/option-lists` | All reusable lists, sorted by name |
| `GET` | `/option-lists/{id}` | One list |
| `POST` | `/option-lists` | Create `{ name, description?, options }`. Id generated from the name; duplicate name -> `409` |
| `PUT` | `/option-lists/{id}` | Replace name/description/options |
| `DELETE` | `/option-lists/{id}` | Remove (`204`) |

Options are trimmed, empties dropped, and duplicates rejected case-insensitively (logic compares option values case-insensitively, so `Yes` and `yes` would clash). Loading a list into a field **copies** the options into that form's configuration - editing or deleting the list later never changes existing forms.

#### Options and conditional logic

Rules are value-based, so **reordering** options never affects them. For the operations that can:

| Operation | What happens |
| --- | --- |
| Rename an option (inline or in the modal) | Rules on later fields that referenced it follow the new name - including `is one of` lists and after clearing/retyping a row |
| Delete an option a rule uses | Inline confirmation listing the dependent fields; after confirming, the rule is flagged as stale |
| Load a saved list that drops used options | The modal lists which rules would break and requires *Apply anyway* |
| Delete a field other fields depend on | Confirmation explaining that the dependent conditions will be removed (those fields become always visible) |
| Move a dependent field above its source / change the source type | The affected condition is flagged |

Invalid references are never rendered as blank selections. The logic editor shows the stale value, operator or field explicitly (`⚠ Other (no longer an option)`, `⚠ Type of pet (appears after this field)`, `⚠ ghost (deleted field)`) with a red outline and a message under the condition; the field header shows an `N problems` badge; and **Save** is disabled until every problem is fixed.

### Submissions

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/forms/{id}/submissions` | Save answers. Body: `{ "data": {fieldId: value}, "submittedBy"?, "source"?, "metadata"?: {...} }` |
| `GET` | `/forms/{id}/submissions?limit=&offset=` | Submissions for one form, newest first |
| `GET` | `/submissions?formId=&limit=&offset=` | All submissions, optional filter |
| `GET` | `/submissions/{submissionId}` | One submission |

On save the server re-evaluates conditional logic: answers to hidden fields are discarded, `required` is enforced only for visible fields, and values are type-checked (numbers, `YYYY-MM-DD` dates, booleans, allowed options). Stored record:

```json
{
  "id": "sub-<uuid>",
  "formId": "pet-registration",
  "formName": "Pet Registration",
  "data": { "residentName": "Ada", "hasPet": "No" },
  "metadata": {
    "submittedAt": "2025-01-15T10:22:31.120Z",
    "submittedBy": null,
    "source": "web",
    "userAgent": "Mozilla/5.0 ...",
    "ipAddress": "203.0.113.5",
    "hiddenFields": ["petType", "petWeight"],
    "pageUrl": "/forms/pet-registration"
  }
}
```

### Data tables

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/tables` | All table definitions, creation order |
| `GET` | `/tables/{id}` | One definition |
| `POST` | `/tables` | Create: registry row + `CREATE TABLE user_tables.<id>`. `{name, description?, columns?, id?}`; name/id taken -> `409`; rule problems -> `422` |
| `PUT` | `/tables/{id}` | Send the desired definition; the server runs the `ALTER TABLE` steps (add/drop column, type, required, options). A change the existing rows do not allow -> `409` `{message, table, column, error, sqlstate, hint}` and nothing changes |
| `DELETE` | `/tables/{id}` | `DROP TABLE` + unregister (`204`); id and name become free |
| `GET` | `/tables/{id}/check` | `{ok, problems[]}`: does the physical table match the definition? |

Columns: `{id, name, type, required?, description?, options?}` with `type` one of `text | textarea | number | date | select | checkbox`. `id` is the real column name (lower-case identifier, immutable); `name` is the header. Full design, type mapping and rules: [docs/data-tables.md](docs/data-tables.md).

### Table rows

`{ref}` is a user table's slug (`work-orders`) or `schema.table` for any other allowed table (`public.option_lists`, `public.forms`, `public.submissions`; never `schema_migrations` or system schemas). Cell types are the PostgreSQL column types.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/tables/{ref}/schema` | Columns as the catalog describes them: SQL type, kind, nullable, default, writable, primary key, options |
| `GET` | `/tables/{ref}/rows` | `{total, limit, offset, items}`. `?limit=100&offset=0&sort=-cost,title&filter=status:equals:Open&filter=cost:greater_than:10&match=all&search=leak` |
| `POST` | `/tables/{ref}/rows/query` | Same with a JSON body `{filters: [{column, operator, value}], match, sort: [{column, direction}], search, limit, offset}` |
| `GET` | `/tables/{ref}/rows/{id}` | One row `{id, tableId, data, createdAt, updatedAt}` |
| `POST` | `/tables/{ref}/rows` | `{data: {column: value}}` -> `201`; every cell checked against its column type, `NOT NULL` columns without defaults required, generated columns refused -> `422 {message, errors: {column: message}}` |
| `PATCH` | `/tables/{ref}/rows/{id}` | Partial `{data}`; `null` clears a cell; only the named columns are written |
| `DELETE` | `/tables/{ref}/rows/{id}` | `204` |

A constraint PostgreSQL enforces beyond that (`UNIQUE`, foreign key, a hand-made `CHECK`) answers `409 {message, table, column, constraint, error, sqlstate, hint}`. Filter operators are the conditional-logic operators below, allowed per column kind; an invalid query is `422 {message: "Invalid query", errors: {"filters[0]": ..., "sort[0]": ...}}`.

### SQL

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/sql/query` | `{sql, params?, limit?, timeoutMs?}` -> `{kind, columns: [{name, type}], rows: [[...]], rowCount, truncated, durationMs}`. One statement; `SELECT`/`WITH`/`VALUES`/`TABLE`/`EXPLAIN`/`SHOW` run read-only with a row cap, `INSERT`/`UPDATE` are committed; `ALTER`/`DROP`/`DELETE`/`TRUNCATE`/`CREATE`/... anywhere in the statement -> `400 {message: "Statement not allowed", error}`; a PostgreSQL error -> `400 {message: "SQL error", error, sqlstate, position, hint, timedOut}` |
| `GET` | `/sql/tables` | Every exposed table with column count and estimated rows |
| `GET` | `/sql/rules` | Allowed statement kinds, blocked keywords/functions, limits |

Details and the reasoning behind the two protection layers: [docs/admin-pages.md](docs/admin-pages.md#5-the-sql-endpoint).

## Conditional logic

Each field may carry a `logic` block:

```json
{
  "action": "show",
  "match": "all",
  "conditions": [
    { "fieldId": "hasPet", "operator": "equals", "value": "Yes" },
    { "fieldId": "petWeight", "operator": "greater_than", "value": 50 }
  ]
}
```

- `action`: `show` or `hide` the field when the conditions match.
- `match`: `all` conditions must match, or `any` one of them.
- Conditions may only reference fields that appear **earlier** in the form. Fields hidden by logic count as unanswered for anything that depends on them, so chains collapse correctly.
- Operators by source type:
  - text / textarea: `equals`, `not_equals`, `contains`, `not_contains`, `starts_with`, `ends_with`, `is_one_of`, `is_not_one_of`, `is_empty`, `is_not_empty`
  - select / radio: `equals`, `not_equals`, `is_one_of`, `is_not_one_of`, `is_empty`, `is_not_empty`
  - number: `equals`, `not_equals`, `greater_than`, `greater_than_or_equal`, `less_than`, `less_than_or_equal`, `is_empty`, `is_not_empty`
  - date: `equals`, `not_equals`, `before`, `after`, `on_or_before`, `on_or_after`, `is_empty`, `is_not_empty`
  - checkbox: `is_checked`, `is_not_checked`

The rules engine exists twice on purpose - `frontend/lib/formLogic.ts` (live show/hide in the browser) and `backend/services/form_logic.py` (authoritative check on submit). Their test suites share the same cases.

## Development direction

- Data tables: build out [docs/data-tables.md](docs/data-tables.md) (table endpoints, rows, querying, then the grid UI).
- Authentication, so `submittedBy` is populated from the signed-in user (schema plan phase 2: `users`, attribution columns, `form_versions`).
- Workflow builder (schema plan phase 4).
=======
Just a simple FastAPI endpoint to send images to form for a pdf creation.


front end start
npm run dev -- --hostname 0.0.0.0

backend start
uvicorn main:app --reload --host 0.0.0.0 --port 8000


## License

This project is licensed under the MIT License.
See the [LICENSE](LICENSE) file for details.
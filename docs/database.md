# Database: running PostgreSQL locally

The backend stores forms, option lists, submissions and data tables in
PostgreSQL. This document covers how to start it, create the schema, load the seed JSON data,
what the API does when the database is broken, and how to prove the setup is
reproducible. The schema itself is explained in
[database-schema-plan.md](database-schema-plan.md).

## Files

```
docker-compose.yml                  postgres:16 (+ optional Adminer), mounts the migrations
.env.example                        POSTGRES_* and DATABASE_URL; copy to .env
backend/core/config.py              Settings: DATABASE_URL, FORMBUILDER_DB_TIMEOUT
backend/core/database.py            connection pool, /health check, PostgreSQL error -> HTTP response
backend/repositories/pg_*.py        the repositories the routers use
backend/db/
  migrations/0001_initial.sql       forms, option_lists, submissions, schema_migrations
  migrations/0002_forms_seq.sql     forms.seq (creation order) + schema_migrations.checksum
  migrations/0003_data_tables.sql   schema user_tables + registry data_tables / data_table_columns (docs/data-tables.md)
  migrate.py                        python -m db.migrate  - apply pending migrations, detect edited files
  import_json.py                    python -m db.import_json - load the seed data/*.json (or a file backup) into the tables
  mapping.py                        pydantic model <-> table row conversion
backend/tests/test_db_schema.py     schema reproducibility + import round trip
backend/tests/test_pg_api.py        soft delete, legacy rows, database error handling
backend/tests/test_tables_schema.py data tables: registry constraints, CREATE/ALTER/DROP TABLE for real, mapping
backend/tests/test_tables_api.py    data tables through the API; catalog compared with the definition after every change
backend/tests/test_table_rows_api.py rows of user tables and public.* tables: type enforcement, filters, sorting, paging
backend/tests/conftest.py           scratch database per session; every API test runs on it
```

## Quick start

```bash
./start.sh                      # Windows: start.cmd
```

`scripts/start.py` finds (or starts) PostgreSQL, writes `.env`, migrates,
imports the JSON data once, verifies, and serves the API - see the README for
the step list and flags. It works with a container started from **any**
directory: running containers are discovered through `docker ps` /
`docker inspect`, including their published port and credentials.

By hand:

```bash
# 1. Settings. POSTGRES_PORT defaults to 55432 because 5432 is usually owned
#    by a PostgreSQL installed on the machine.
cp .env.example .env

# 2. Start PostgreSQL. The first start creates the schema from backend/db/migrations.
docker compose up -d
docker compose ps            # wait for "healthy"

# 3. Backend dependencies (Python 3.11-3.13) and the legacy JSON data (one-off; idempotent)
cd backend
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m db.migrate         # no-op on a fresh container, applies new files on an existing one
python -m db.import_json     # seed data; DATABASE_URL comes from .env

# 4. Run the API
uvicorn main:app --reload --host 0.0.0.0 --port 8000
curl localhost:8000/health

# 5. Verify
python ../scripts/check_db.py
python ../scripts/check_health.py
python -m pytest             # whole suite incl. PostgreSQL, using DATABASE_URL from .env
```

`.env` is read by the backend, the `db.*` tools and pytest (repository root
`.env`, after `backend/.env`; real environment variables win), so a single
file keeps everything pointed at the same database.

### Using a PostgreSQL that is already running

You do not have to use this repository's compose file. `./start.sh` picks up
any running postgres container and, failing that, `localhost:55432` /
`localhost:5432` with the default credentials. To be explicit:

```bash
./start.sh --container my-other-db          # that container only
./start.sh --database-url postgresql://user:pass@host:port/db
```

The URL that answered is written to `.env`. If `.env` already had a
`DATABASE_URL` that does not answer (for example an old port), the script says
why it failed, uses the instance that works and updates `.env`.

Container states the script handles:

| State (`docker ps -a`) | What happens |
| --- | --- |
| running, PostgreSQL answering | used |
| running, PostgreSQL still starting (`Connection refused` / `starting up`) | waited for, up to 30 s |
| exited / created (stopped) | `docker start NAME`, then waited for up to 60 s; `--no-start-containers` disables this |
| no postgres container at all | `docker compose up -d postgres` from this repository |
| Docker daemon not running | reported; only URLs and localhost ports are tried |
| several containers | running ones first, then `formbuilder-postgres`, then the one whose database is `formbuilder`; `--container NAME` to choose |

Connect with `psql "$DATABASE_URL"`, or start Adminer with
`docker compose --profile tools up -d` and open http://localhost:8080
(System: PostgreSQL, Server: `postgres`, user/password/database from `.env`).

## How the schema gets created and updated

There is exactly one definition of the schema: the SQL files in
`backend/db/migrations/`, named `NNNN_description.sql`. Each file

* is wrapped in `BEGIN; ... COMMIT;` so it applies completely or not at all, and
* ends with `INSERT INTO schema_migrations (version) VALUES ('NNNN_description');`
  so the database itself records what has been applied.

**New installs and existing installations run the same files in the same
order.** A new install runs all of them on an empty database; an existing
installation runs the ones it has not recorded yet. The test-suite proves the
two paths end in an identical catalog
(`test_upgrade_from_every_earlier_version_matches_a_fresh_install`).

| Consumer | When | How |
| --- | --- | --- |
| Postgres container | first start of an empty data volume | the official image executes every `*.sql` in `/docker-entrypoint-initdb.d` (our `migrations/` mount) in name order with `ON_ERROR_STOP` |
| `python -m db.migrate` | existing database, after pulling new migration files | applies the files whose version is missing from `schema_migrations`, in order; records each file's sha256 |
| `python -m db.migrate --status` | any time | one line per file: `applied`, `pending`, or `MODIFIED` |
| `pytest` with `DATABASE_URL` | CI / before committing a migration | fresh install, every upgrade path, container init path, drift detection, API on an upgraded database |

The container only runs init scripts on an **empty** volume. After that, the
runner is the only way migrations get applied.

### Upgrading an existing installation

```bash
git pull
cd backend
python -m db.migrate --status      # shows what is pending
python -m db.migrate               # applies it (each file in its own transaction)
```

No restart is needed: a running API keeps answering with a clear error
(`503` from `/health`, `500` with the missing table/column from data routes)
until the migration is applied, then works again immediately.

What the runner guarantees:

* **Order** - files are applied in name order, never skipped.
* **Atomicity** - a failing file rolls back entirely; nothing after it runs;
  the error is printed; re-running resumes at that file.
* **Immutability** - the sha256 of every applied file is stored in
  `schema_migrations.checksum`. If an applied file has been edited, the runner
  refuses with `MigrationDriftError` and `--status` shows `MODIFIED`. Files
  applied by the container (no checksum) are adopted on the runner's first
  contact.
* **Reality check** - start-up and `/health` also run a probe query per table
  (`SELECT <every column the repositories use> FROM forms WHERE false` ...).
  If `schema_migrations` claims the schema is current but a column is missing,
  the response says *"Database schema does not match the applied migrations"*
  instead of *"up to date"*.

### Adding a migration

1. Create `backend/db/migrations/NNNN_short_description.sql` with the next
   number, following the template of the existing files
   (BEGIN / DDL / `INSERT INTO schema_migrations` / COMMIT). Write it so it
   works on the schema the previous file leaves behind **with data in it**
   (back-fill new `NOT NULL` columns; see `0002_forms_seq.sql`).
2. If a repository starts using a new column, add it to that repository's
   `SCHEMA_PROBE` so start-up/health checks cover it.
3. `python -m db.migrate` against your development database.
4. `DATABASE_URL=... python -m pytest` - it applies the file to a fresh
   database *and* as an upgrade from every earlier version, and compares the
   results. Update `test_expected_tables_and_columns` with the new shape.
5. Update `docs/database-schema-plan.md`.

**Never edit a migration file that has been applied anywhere** - not even
your own development database. Restore it and put the change in a new file.
(This rule was broken once, between the first two releases, which is why
`0002_forms_seq.sql` checks the catalog before each step and why checksums
exist now.)

### If your database was created before `0002`

Nothing special: `python -m db.migrate` applies `0002_forms_seq`, numbering
the existing forms by `created_at` so their current order is kept. If your
database happened to be created from the short-lived `0001` that already
contained `forms.seq`, `0002` detects that and only adds what is missing.

## Importing the JSON data

```bash
cd backend
python -m db.import_json                       # from backend/data (or $FORMBUILDER_DATA_DIR)
python -m db.import_json --data-dir /path/to/backup
python -m db.import_json --orphans placeholder
```

* Records are validated with the same models the API uses. Blank or duplicate
  form ids (early builder versions wrote `id: ""`) become unique slugs.
* Upserts by id: re-running refreshes rows and keeps original timestamps and
  insertion order. Rows that exist only in the database are left alone.
* Legacy records without `createdAt`/`updatedAt`: `created_at` falls back to
  `updated_at`, then to the import time; `updated_at` falls back to `created_at`.
* Submissions whose `formId` matches no form ("orphans") violate the foreign
  key. `--orphans fail` (default) aborts and lists them; `--orphans skip`
  drops them; `--orphans placeholder` creates a soft-deleted form with the
  submission's `formName` so the records are kept and still listable.

## Failure behaviour

PostgreSQL is the only storage. `DATABASE_URL` is required: without it the
backend does not start (`StorageConfigurationError` with the fix in the
message).

The pool is opened at start-up and the log shows either
`PostgreSQL 16.x reachable; schema up to date (0001_initial)` or an `ERROR`
describing the problem. The API keeps running either way, so that it can come
up before the database and recover without a restart; until then every data
request fails with a clear error:

| Problem | HTTP | `detail.message` | Where the cause is |
| --- | --- | --- | --- |
| server down, wrong host/port, wrong password, unknown database | `503` | `Database unavailable` | `detail.error` holds the libpq message, e.g. `connection to server at "127.0.0.1", port 5432 failed: Connection refused` or `FATAL: database "x" does not exist`; `detail.hint` says what to check |
| schema not created / migration pending | `500` | `Database schema is missing or out of date` | `detail.error` = `relation "forms" does not exist`, `detail.sqlstate` = `42P01`, `detail.hint` = run `python -m db.migrate` |
| permission problem | `500` | `Database permission denied` | `detail.error`, `detail.sqlstate` |
| any other PostgreSQL error | `500` | `Database error` | `detail.type` (psycopg class), `detail.sqlstate`, `detail.error`, `detail.constraint` |

Example:

```json
HTTP 503
{
  "detail": {
    "message": "Database unavailable",
    "type": "OperationalError",
    "error": "couldn't get a connection after 5.00 sec: connection failed: connection to server at \"127.0.0.1\", port 5432 failed: Connection refused\n\tIs the server running on that host and accepting TCP/IP connections?",
    "hint": "Check DATABASE_URL and that PostgreSQL is running and reachable (`docker compose up -d`, `docker compose ps`)."
  }
}
```

`GET /health` returns `200` with `{"status": "ok", "database": {"ok": true, "serverVersion", "appliedMigrations", "pendingMigrations": [], "error": null}}`
when everything is fine, and `503` with `status: "error"` and the same
`database.error` object otherwise. It also checks that every data table
matches its registry (`error.problems` lists each discrepancy; see
docs/data-tables.md section 5.3). Point a monitor or the compose healthcheck
of a future backend service at it.

`FORMBUILDER_DB_TIMEOUT` (seconds, default 5) bounds how long a request waits
for a connection before the `503`.

### Semantics that differ from the JSON files

* `DELETE /forms/{id}` soft-deletes (`forms.deleted_at`). The form is gone
  from `GET /forms` and `GET /forms/{id}` (404), but its submissions remain in
  `GET /submissions?formId=` and `GET /forms/{id}/submissions`, and the slug
  cannot be reused (`POST /forms` with that id -> 409; a generated id gets the
  next suffix).
* Every form and option list has `createdAt`/`updatedAt` (legacy records got
  them at import time). The update endpoints accept and ignore these keys, so a
  `GET` response can be edited and sent back with `PUT` unchanged.
* `GET /forms` lists in creation order (`forms.seq`). Forms imported from the
  JSON file keep the file order; forms that existed before `0002` were
  numbered by `created_at`.

## Reset / start over

```bash
docker compose down -v      # removes the container AND the data volume
docker compose up -d        # fresh cluster, schema re-created from the migrations
cd backend && python -m db.import_json
```

## Backup and restore

```bash
docker compose exec postgres pg_dump -U formbuilder -Fc formbuilder > backup.dump
docker compose exec -T postgres pg_restore -U formbuilder -d formbuilder --clean --if-exists < backup.dump
```

## Configuration reference

| Variable | Default | Used by |
| --- | --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `formbuilder` x3 | container (role and database created on first start) |
| `POSTGRES_PORT` | `55432` | host port published by the container (5432 is usually taken by a local PostgreSQL) |
| `ADMINER_PORT` | `8080` | Adminer, `--profile tools` only |
| `DATABASE_URL` | unset | the API (required), `db.migrate`, `db.import_json`, the tests; maintained in `.env` by `scripts/start.py` |
| `FORMBUILDER_DB_TIMEOUT` | `5` | seconds to wait for a connection before `503` |

`python -m db.migrate` exit codes: `0` ok, `1` cannot connect, `2` an applied
migration file was modified.

`DATABASE_URL` is a libpq URL: `postgresql://USER:PASSWORD@HOST:PORT/DBNAME`.
The tests need a role allowed to `CREATE DATABASE`; the compose user is the
cluster superuser, which is fine for development. For production, create an
unprivileged application role:

```sql
CREATE ROLE formbuilder_app LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE formbuilder TO formbuilder_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO formbuilder_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO formbuilder_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO formbuilder_app;
-- Data tables: the API creates, alters and drops tables in this schema.
GRANT USAGE, CREATE ON SCHEMA user_tables TO formbuilder_app;
ALTER SCHEMA user_tables OWNER TO formbuilder_app;
```

## How the repositories use the schema

* `db/mapping.py` converts rows to the pydantic models the API serves, so the
  JSON is byte-for-byte what the file backend produced (timestamps as
  `...T...sssZ`, submission metadata extras merged back into `metadata`).
* Listing queries: forms `WHERE deleted_at IS NULL ORDER BY seq`; option lists
  `ORDER BY lower(name) COLLATE "C"` (= Python's `sorted(key=str.lower)`);
  submissions `ORDER BY submitted_at DESC, seq DESC`.
* Each repository call borrows one pooled connection and runs in its own
  transaction (committed on success, rolled back on error). Uniqueness races
  (two requests generating the same slug) surface as `UniqueViolation`, which
  the routers turn into a retry with the next free slug for generated ids and
  into `409` for explicitly requested ids.

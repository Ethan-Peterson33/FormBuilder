# Table management, data management and SQL pages

Three pages on top of the existing APIs. A lightweight version of all of
them (plus forms, option lists, submissions and a raw request panel) is
served by the backend itself at `/ui/` - `backend/ui/` (`index.html`,
`app.js`, `styles.css`; vanilla JS, no build step) - for exercising every
endpoint. This document is also the specification for the eventual Next.js
pages, should they be built into the frontend (`app/<route>/page.tsx`,
route handlers under `app/api/*` proxying to FastAPI).

Contents

1. [Table management - `/tables`](#1-table-management---tables)
2. [Data management - `/tables/[ref]/data`](#2-data-management---tablesrefdata)
3. [SQL - `/sql`](#3-sql---sql)
4. [Proxy routes to add](#4-proxy-routes-to-add)
5. [The SQL endpoint](#5-the-sql-endpoint)

---

## 1. Table management - `/tables`

Create, edit and delete user tables (real PostgreSQL tables; see
[data-tables.md](data-tables.md)).

**List view** - `GET /sql/tables` (every exposed table: user tables first,
with `name`, `columns`, `estimatedRows`, `userTable`), or `GET /tables` for
user tables only with their full definitions. Each row links to the editor
(user tables) and to the data page (all tables). "New table" opens the
editor empty.

**Editor** (`/tables/new`, `/tables/[id]/edit`) - mirrors the form builder:
name, description and an ordered list of columns (`id`, `name`, `type`,
`required`, `description`, `options` for `select`). Column `id` is the real
column name: lower-case identifier, immutable once saved (disable the input
for existing columns). The option editor and option library from the form
builder are reused for `select` columns.

* Save: `POST /tables` (new) or `PUT /tables/{id}` (the whole definition;
  the server diffs it and runs the `ALTER TABLE` steps).
* `422` - list of `{loc, msg}`: show the `msg`s next to the columns, like the
  builder does for form rules.
* `409` with `detail.column` - "the table's data does not allow this change":
  show `detail.error` and `detail.hint` on that column and keep the editor
  open; nothing was changed on the server.
* `409` text - name or id taken.
* `500` with `detail.table` - the physical table no longer matches the
  definition (changed by hand). Show `detail.hint` and link to the check.

Before saving a definition that **removes a column** or **narrows options /
changes a type / makes a column required**, warn: the column's data will be
dropped, or the change may be refused if rows do not fit. The row count is
available from `GET /tables/{id}/rows?limit=0` (`total`).

**Check** - `GET /tables/{id}/check` -> `{ok, problems[]}`; show a badge on
the list and the problem list on the editor.

**Delete** - `DELETE /tables/{id}` after a confirmation that names the row
count; the table is really dropped.

## 2. Data management - `/tables/[ref]/data`

A grid over any exposed table: user tables by slug, others by
`schema.table` (`public.option_lists`). Works the same for both because the
row API is catalog-driven.

* Columns: `GET /tables/{ref}/schema` - `columns[]` with `id`, `sqlType`,
  `kind`, `nullable`, `hasDefault`, `writable`, `primaryKey`, `options`.
  `kind` picks the cell editor: `text` (input / textarea), `integer` /
  `number` (number input), `boolean` (checkbox), `date` (date picker),
  `timestamp` (datetime input), `select` via `options` (dropdown),
  `text_array` (tag list), `json` (JSON textarea), `unknown` (read-only).
  Non-`writable` columns are read-only; `primaryKey` is the row id.
* Rows: `POST /tables/{ref}/rows/query` with
  `{filters, match, sort, search, limit, offset}` -> `{total, limit, offset,
  items}`; each item is `{id, data, createdAt, updatedAt}`. Use the
  condition editor from the form builder for filters (same operators;
  allowed per `kind`, see data-tables.md section 7). Column-header clicks
  set `sort`; a search box sets `search`; pager uses `limit`/`offset` and
  `total`.
* Edit a cell: `PATCH /tables/{ref}/rows/{id}` with `{data: {column:
  value}}` for just that cell (`null` clears). The response is the full
  stored row - replace the grid row with it.
* Add a row: a blank row whose required cells (`!nullable && !hasDefault &&
  writable`) are highlighted; `POST /tables/{ref}/rows` with the cells
  entered.
* Delete: `DELETE /tables/{ref}/rows/{id}` with confirmation.
* Errors: `422 {message, errors: {column: message}}` - show each message on
  its cell; `409 {message, column, constraint, error, hint}` - show on the
  cell (`column`) or as a row error; `400` - the table has no single-column
  primary key: show the grid read-only.
* Link to the SQL page with `SELECT * FROM <schema>.<table> LIMIT 100`
  prefilled.

## 3. SQL - `/sql`

An editor, a run button and a results grid.

* Sidebar: `GET /sql/tables` (click inserts the qualified name; expand ->
  `GET /tables/{ref}/schema` for the columns).
* Run: `POST /sql/query` with `{sql, params?, limit?, timeoutMs?}`. Show
  `columns` as headers (with `type` as a tooltip), `rows` as the grid,
  `rowCount`, `durationMs`, and "showing the first N rows" when
  `truncated`. For `kind: "write"` show "N rows updated/inserted" and the
  `RETURNING` rows if any.
* Errors (`400`): `detail.message` is `"Statement not allowed"` (guard:
  `detail.error` says which keyword / why) or `"SQL error"` (`detail.error`,
  `detail.sqlstate`, `detail.position` - 1-based character offset in the
  text as typed, to move the cursor / underline; `detail.hint`;
  `detail.timedOut`).
* Show the rules from `GET /sql/rules` in a help panel (allowed statement
  kinds, blocked keywords, single statement, limit and timeout).
* Keep a history of statements in local storage.

## 4. Proxy routes to add

One Next.js route handler per backend path, like the existing ones:

```
app/api/tables/route.ts                          GET, POST          -> /tables
app/api/tables/[ref]/route.ts                    GET, PUT, DELETE   -> /tables/{ref}
app/api/tables/[ref]/check/route.ts              GET                -> /tables/{ref}/check
app/api/tables/[ref]/schema/route.ts             GET                -> /tables/{ref}/schema
app/api/tables/[ref]/rows/route.ts               GET, POST          -> /tables/{ref}/rows (+ query string)
app/api/tables/[ref]/rows/query/route.ts         POST               -> /tables/{ref}/rows/query
app/api/tables/[ref]/rows/[id]/route.ts          GET, PATCH, DELETE -> /tables/{ref}/rows/{id}
app/api/sql/query/route.ts                       POST               -> /sql/query
app/api/sql/tables/route.ts                      GET                -> /sql/tables
app/api/sql/rules/route.ts                       GET                -> /sql/rules
```

## 5. The SQL endpoint

`POST /sql/query` runs **one statement** directly against the database and
returns a grid-shaped result. Two layers decide what may run:

**1. The guard (`backend/services/sql_guard.py`)** tokenises the statement
(string literals, `E''` strings, dollar-quoted strings, quoted identifiers,
`--` and nested `/* */` comments are recognised, so a keyword inside a
string is ignored and one inside a CTE is found) and requires:

* a single statement (no `;` except a trailing one);
* a first keyword of `SELECT`, `WITH`, `VALUES`, `TABLE`, `EXPLAIN`,
  `SHOW` (read-only) or `INSERT`, `UPDATE` (write);
* none of `ALTER DROP DELETE TRUNCATE CREATE GRANT REVOKE COPY VACUUM
  REINDEX CLUSTER CALL LOCK COMMENT MERGE REFRESH REASSIGN SECURITY IMPORT
  DISCARD CHECKPOINT LOAD LISTEN NOTIFY UNLISTEN PREPARE DEALLOCATE DECLARE
  FETCH MOVE` anywhere, nor the functions `pg_terminate_backend`,
  `pg_cancel_backend`, `pg_sleep*`, `pg_read_file`, `pg_read_binary_file`,
  `pg_ls_dir`, `pg_stat_file`, `pg_reload_conf`, `pg_rotate_logfile`,
  `pg_switch_wal`, `pg_create_restore_point`, `pg_promote`, `lo_import`,
  `lo_export`, `lo_unlink`, `dblink*`, `set_config`, `pg_advisory_lock`,
  `pg_advisory_xact_lock`, `pg_notify`;
* no `INSERT`/`UPDATE` inside a read statement (data-modifying CTE) and no
  `FOR UPDATE`;
* write statements may only target tables the row API exposes (schemas
  `public` and `user_tables`, never `schema_migrations`), including nested
  writes in subqueries.

`SET`, `DO` and `END` are not blocked as words because they are legitimate
inside `UPDATE ... SET`, `ON CONFLICT DO UPDATE` and `CASE ... END`; as
statements they fail the first-keyword check.

**2. The database (`backend/repositories/pg_sql.py`)** runs read statements
in a `READ ONLY` transaction, wrapped as `SELECT * FROM (<sql>) AS _q LIMIT
n+1`: whatever the scan did not foresee - `SELECT INTO`, a function that
writes, `nextval()` - is refused by PostgreSQL (`25006`), and at most
`limit` rows are read (`truncated` says if there were more). Writes run in
their own transaction and are committed on success; a failure rolls back.
Every statement has a `statement_timeout` (`timeoutMs`, default 10 s, max
60 s; `57014` -> `timedOut: true`).

Not covered, by design: a `SELECT` can read any table in the database
(including the catalog - useful for the page), and `UPDATE`/`INSERT` on
application tables bypass the owning feature's business rules, as the row
API does. If stronger isolation is ever needed, the right tool is a
dedicated PostgreSQL role for the SQL page with `SELECT` everywhere and
`INSERT`/`UPDATE` only where wanted; the endpoint would not change.

Request / response:

```jsonc
POST /sql/query
{"sql": "SELECT id, title FROM user_tables.work_orders WHERE cost > %s", "params": [10], "limit": 500, "timeoutMs": 10000}

200
{"kind": "read",                              // read | explain | write
 "columns": [{"name": "id", "type": "int8"}, {"name": "title", "type": "text"}],
 "rows": [[1, "Leaking tap"], [2, "Broken window"]],
 "rowCount": 2,                               // rows returned (read) / rows affected (write)
 "truncated": false, "durationMs": 1.8, "targets": []}

400 {"detail": {"message": "Statement not allowed", "error": "DELETE is not allowed (position 11)"}}
400 {"detail": {"message": "SQL error", "error": "column \"nope\" does not exist", "sqlstate": "42703",
                "position": 8, "detail": null, "hint": null, "timedOut": false}}
```

`GET /sql/tables` -> `{"tables": [{ref, schema, table, name, userTable,
columns, estimatedRows, comment}]}`; `GET /sql/rules` -> the lists above
plus limits.

-- 0003_data_tables: user-defined data tables as REAL PostgreSQL tables.
--
-- Design: docs/data-tables.md. A user table is a real table in the
-- `user_tables` schema (CREATE TABLE / ALTER TABLE ... ADD, DROP, ALTER COLUMN
-- / DROP TABLE, all issued by the application inside one transaction with the
-- registry update below). The PostgreSQL catalog is the source of truth for
-- structure; the two registry tables hold what the catalog cannot express
-- (display names, the API column type, option lists, order) and give the API
-- stable slugs.

BEGIN;

-- Where the user tables live. Kept apart from the application's own tables
-- so a user can never name a table `forms`, and so "list every user table"
-- is one catalog query.
CREATE SCHEMA user_tables;
COMMENT ON SCHEMA user_tables IS 'User-defined data tables, created and altered by the API. Registry: public.data_tables.';

-- ---------------------------------------------------------------------------
-- data_tables  <->  models/tables.py#DataTable (one row per user table)
-- ---------------------------------------------------------------------------
CREATE TABLE data_tables (
    -- URL slug (like forms.id), generated from the name or requested.
    id          text        PRIMARY KEY,
    -- Name of the physical table in schema user_tables: the slug with '-'
    -- replaced by '_' so it is a plain identifier (`user_tables.work_orders`).
    table_name  text        GENERATED ALWAYS AS (replace(id, '-', '_')) STORED,
    -- Insertion order; GET /tables lists by it (like forms.seq).
    seq         bigint      GENERATED ALWAYS AS IDENTITY,
    name        text        NOT NULL,
    description text        NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT data_tables_id_format     CHECK (id ~ '^[a-z0-9][a-z0-9-]*$'),
    CONSTRAINT data_tables_id_length     CHECK (char_length(id) <= 63),
    CONSTRAINT data_tables_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT data_tables_name_length   CHECK (char_length(name) <= 120),
    CONSTRAINT data_tables_table_name_unique UNIQUE (table_name)
);

COMMENT ON TABLE  data_tables            IS 'Registry of user-defined tables. Each row has a real table user_tables.<table_name>; both change in one transaction.';
COMMENT ON COLUMN data_tables.id         IS 'URL slug. Immutable.';
COMMENT ON COLUMN data_tables.table_name IS 'Physical table name in schema user_tables (derived from id).';
COMMENT ON COLUMN data_tables.seq        IS 'Insertion order; GET /tables lists tables by it.';

-- Table names are unique regardless of case (tables are picked by name in
-- form bindings and workflows).
CREATE UNIQUE INDEX data_tables_name_unique ON data_tables (lower(name));

-- ---------------------------------------------------------------------------
-- data_table_columns  <->  models/tables.py#TableColumn (one row per column)
-- ---------------------------------------------------------------------------
CREATE TABLE data_table_columns (
    table_id    text        NOT NULL REFERENCES data_tables (id) ON DELETE CASCADE,
    -- Physical column name in the user table and key in every row's JSON.
    -- Immutable; a plain lower-case identifier so it never needs quoting.
    id          text        NOT NULL,
    -- Display order in the grid (PostgreSQL cannot reorder columns).
    position    integer     NOT NULL,
    name        text        NOT NULL,
    -- API column type (text | textarea | number | date | select | checkbox);
    -- decides the SQL type (services/table_ddl.py). No CHECK: adding a type
    -- is a code change, not a migration (schema plan D5).
    type        text        NOT NULL,
    -- Mirrors the NOT NULL constraint on the physical column.
    required    boolean     NOT NULL DEFAULT false,
    description text        NOT NULL DEFAULT '',
    -- select: allowed values, mirrored by a CHECK constraint on the column.
    options     text[],
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (table_id, id),
    CONSTRAINT data_table_columns_id_format   CHECK (id ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT data_table_columns_id_length   CHECK (char_length(id) <= 48),
    CONSTRAINT data_table_columns_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT data_table_columns_name_length CHECK (char_length(name) <= 120),
    CONSTRAINT data_table_columns_type_not_blank CHECK (btrim(type) <> ''),
    -- Deferred so a reorder can swap positions inside one transaction.
    CONSTRAINT data_table_columns_position_unique UNIQUE (table_id, position) DEFERRABLE INITIALLY DEFERRED
);

COMMENT ON TABLE  data_table_columns          IS 'Registry of the columns of each user table (display metadata). The physical column is the source of truth for type and NOT NULL.';
COMMENT ON COLUMN data_table_columns.id       IS 'Physical column name and JSON key. Immutable.';
COMMENT ON COLUMN data_table_columns.type     IS 'API column type; maps to the SQL type in services/table_ddl.py.';
COMMENT ON COLUMN data_table_columns.options  IS 'Allowed values of a select column; enforced by CHECK constraint <id>_options on the user table.';

-- Column names are unique per table regardless of case.
CREATE UNIQUE INDEX data_table_columns_name_unique ON data_table_columns (table_id, lower(name));

INSERT INTO schema_migrations (version) VALUES ('0003_data_tables');

COMMIT;

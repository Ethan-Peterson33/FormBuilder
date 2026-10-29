"""The SQL page's statement guard (no database needed)."""

from __future__ import annotations

import pytest

from services.sql_guard import SqlGuardError, check, tokenize


def _deny(sql: str, message: str) -> None:
    with pytest.raises(SqlGuardError, match=message):
        check(sql)


def test_tokenizer_recognises_strings_identifiers_and_comments():
    tokens = tokenize("""SELECT 'a''b', E'c\\'d', $$e'f$$, $x$ g $x$, "q""i", 1.5e3, x::int -- c\n/* a /* nested */ b */ FROM t""")
    assert [(t.kind, t.text) for t in tokens] == [
        ("word", "SELECT"), ("string", "'a''b'"), ("symbol", ","), ("string", "E'c\\'d'"), ("symbol", ","),
        ("string", "e'f"), ("symbol", ","), ("string", " g "), ("symbol", ","), ("ident", 'q"i'), ("symbol", ","),
        ("number", "1.5e3"), ("symbol", ","), ("word", "x"), ("symbol", "::"), ("word", "int"), ("word", "FROM"), ("word", "t"),
    ]
    for broken in ("SELECT 'abc", 'SELECT "abc', "SELECT $$abc", "SELECT /* abc"):
        with pytest.raises(SqlGuardError, match="unterminated"):
            tokenize(broken)


@pytest.mark.parametrize("sql, kind, statement", [
    ("SELECT 1", "read", "SELECT 1"),
    ("  select * from forms ;  ", "read", "  select * from forms"),  # leading text kept: error positions stay right
    ("WITH x AS (SELECT 1) SELECT * FROM x", "read", None),
    ("VALUES (1, 2)", "read", None),
    ("TABLE forms", "read", None),
    ("EXPLAIN SELECT 1", "explain", None),
    ("SHOW server_version", "explain", None),
    ("-- leading comment\nSELECT 1", "read", None),
    ("SELECT 'DROP TABLE forms', \"delete\" FROM t /* TRUNCATE */ -- ALTER", "read", None),
    ("SELECT $$DROP$$, E'DELETE\\'s'", "read", None),
    ("SELECT CASE WHEN true THEN 1 END", "read", None),
])
def test_allowed_read_statements(sql, kind, statement):
    verdict = check(sql)
    assert verdict.kind == kind and verdict.targets == []
    if statement:
        assert verdict.statement == statement


@pytest.mark.parametrize("sql, targets", [
    ("UPDATE forms SET name = 'x' WHERE id = 'a'", ["forms"]),
    ("update only user_tables.work_orders set title = 'x'", ["user_tables.work_orders"]),
    ('UPDATE "public"."forms" SET name = $$x$$', ["public.forms"]),
    ("INSERT INTO option_lists (id, name, options) VALUES ('x', 'X', '{a}')", ["option_lists"]),
    ("INSERT INTO forms (id, name) VALUES ('x', 'X') ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name", ["forms"]),
    ("INSERT INTO forms (id, name) SELECT id || '-copy', name FROM forms", ["forms"]),
    ("WITH src AS (SELECT 1) UPDATE forms SET name = 'x' FROM src", None),  # starts with WITH: a read
])
def test_write_statements_and_their_targets(sql, targets):
    if targets is None:
        _deny(sql, "UPDATE inside a WITH statement is not allowed")
        return
    verdict = check(sql)
    assert verdict.kind == "write" and verdict.targets == targets


@pytest.mark.parametrize("sql, message", [
    ("", "empty"),
    ("   ;  ", "empty"),
    ("DROP TABLE forms", "starting with 'DROP' are not allowed"),
    ("DELETE FROM forms", "starting with 'DELETE'"),
    ("TRUNCATE forms", "starting with 'TRUNCATE'"),
    ("ALTER TABLE forms ADD COLUMN x int", "starting with 'ALTER'"),
    ("CREATE TABLE x (id int)", "starting with 'CREATE'"),
    ("SET ROLE postgres", "starting with 'SET'"),
    ("DO $$ BEGIN END $$", "starting with 'DO'"),
    ("BEGIN", "starting with 'BEGIN'"),
    ("COPY forms TO '/tmp/x'", "starting with 'COPY'"),
    ("SELECT 1; DROP TABLE forms", "one statement at a time"),
    ("SELECT 1;;", "one statement at a time"),
    ("WITH d AS (DELETE FROM forms RETURNING *) SELECT * FROM d", "DELETE is not allowed"),
    ("WITH d AS (UPDATE forms SET name = 'x' RETURNING *) SELECT * FROM d", "UPDATE inside a WITH"),
    ("SELECT * FROM forms FOR UPDATE", "FOR UPDATE"),
    ("SELECT * FROM forms FOR NO KEY UPDATE", "FOR UPDATE"),
    ("UPDATE forms SET name = (SELECT 1 FROM x FOR UPDATE)", None),      # allowed: locking inside a write
    ("SELECT pg_sleep(5)", "pg_sleep is not allowed"),
    ("SELECT PG_TERMINATE_BACKEND(1)", "PG_TERMINATE_BACKEND is not allowed"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT set_config('x', 'y', false)", "set_config"),
    ("SELECT * FROM dblink('x', 'y')", "dblink"),
    ("UPDATE schema_migrations SET checksum = NULL", "writes to schema_migrations are not allowed"),
    ("UPDATE pg_catalog.pg_class SET relname = 'x'", "writes to pg_catalog.pg_class"),
    ("INSERT INTO information_schema.tables VALUES (1)", "writes to information_schema.tables"),
    ("UPDATE forms SET name = 'x' WHERE id IN (UPDATE schema_migrations SET version = 'x' RETURNING version)",
     "writes to schema_migrations"),
    ("INSERT forms VALUES (1)", "INSERT must be followed by INTO"),
    ("SELECT 'abc", "unterminated string"),
    ("SELECT 1 FROM t; -- ok", None),
    ("EXPLAIN ANALYZE SELECT 1", None),
    ("SELECT x FROM t WHERE y = 'COPY'", None),
])
def test_denied_and_edge_cases(sql, message):
    if message is None:
        check(sql)
    else:
        _deny(sql, message)

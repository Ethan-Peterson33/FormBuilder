"""Decide whether a statement from the SQL page may run, and how.

Two layers keep the SQL page safe; this module is the first:

1. **Lexical check (here).** The statement is tokenised - string literals,
   dollar-quoted strings, quoted identifiers and comments are recognised, so
   a keyword hidden in a CTE is found and one inside a string is not - and
   must be a single statement whose first keyword is one of the allowed
   kinds. Blocked keywords (``DROP``, ``DELETE``, ``TRUNCATE``, ``ALTER``,
   ``CREATE``, ...) and dangerous functions (``pg_terminate_backend``,
   ``pg_sleep``, ...) may not appear anywhere. Writes may only target tables
   the row API exposes.
2. **The database (``repositories/pg_sql.py``).** Read statements run in a
   ``READ ONLY`` transaction wrapped as a subquery with a row limit, so
   anything the scan did not foresee (a data-modifying CTE, ``SELECT INTO``,
   ``FOR UPDATE``, a function that writes) is refused by PostgreSQL itself.
   Every statement has a timeout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from services.table_catalog import ALLOWED_SCHEMAS, HIDDEN_TABLES

StatementKind = Literal["read", "explain", "write"]

# First keyword -> how the statement is run.
READ_STARTERS = {"SELECT", "WITH", "VALUES", "TABLE"}
EXPLAIN_STARTERS = {"EXPLAIN", "SHOW"}
WRITE_STARTERS = {"INSERT", "UPDATE"}
ALLOWED_STARTERS = READ_STARTERS | EXPLAIN_STARTERS | WRITE_STARTERS

# Never allowed anywhere in a statement (outside strings and comments).
# ``SET``, ``DO`` and ``END`` are deliberately absent: they are legitimate
# inside ``UPDATE ... SET``, ``ON CONFLICT DO UPDATE`` and ``CASE ... END``;
# as top-level statements they are stopped by the first-keyword check.
BLOCKED_KEYWORDS = frozenset({
    "ALTER", "DROP", "DELETE", "TRUNCATE", "CREATE", "GRANT", "REVOKE", "COPY",
    "VACUUM", "REINDEX", "CLUSTER", "CALL", "LOCK", "COMMENT", "MERGE", "REFRESH",
    "REASSIGN", "SECURITY", "IMPORT", "DISCARD", "CHECKPOINT", "LOAD", "LISTEN",
    "NOTIFY", "UNLISTEN", "PREPARE", "DEALLOCATE", "DECLARE", "FETCH", "MOVE",
})
BLOCKED_FUNCTIONS = frozenset({
    "pg_terminate_backend", "pg_cancel_backend", "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "pg_reload_conf",
    "pg_rotate_logfile", "pg_switch_wal", "pg_create_restore_point", "pg_promote",
    "lo_import", "lo_export", "lo_unlink", "dblink", "dblink_exec", "dblink_connect",
    "set_config", "pg_advisory_lock", "pg_advisory_xact_lock", "pg_notify",
})

_TOKEN_RE = re.compile(
    r"""
    (?P<line_comment>--[^\n]*)
  | (?P<block_comment>/\*)
  | (?P<dollar>\$(?P<tag>[A-Za-z_][A-Za-z0-9_]*)?\$)
  | (?P<estring>[eE]')
  | (?P<string>')
  | (?P<ident>")
  | (?P<word>[A-Za-z_][A-Za-z0-9_$]*)
  | (?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)
  | (?P<symbol>::|[^\sA-Za-z_0-9"'$])
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class Token:
    kind: Literal["word", "ident", "string", "number", "symbol"]
    text: str
    position: int


class SqlGuardError(ValueError):
    """The statement is not allowed; ``str(exc)`` says why."""


def tokenize(sql: str) -> list[Token]:
    """Tokens outside comments; strings and quoted identifiers as single tokens.
    Raises :class:`SqlGuardError` for unterminated constructs."""
    tokens: list[Token] = []
    pos = 0
    length = len(sql)
    while pos < length:
        if sql[pos].isspace():
            pos += 1
            continue
        match = _TOKEN_RE.match(sql, pos)
        if match is None:
            raise SqlGuardError(f"unexpected character {sql[pos]!r} at position {pos}")
        start = pos
        if match.group("line_comment"):
            pos = match.end()
        elif match.group("block_comment"):
            depth, pos = 1, match.end()
            while depth and pos < length:
                if sql.startswith("/*", pos):
                    depth, pos = depth + 1, pos + 2
                elif sql.startswith("*/", pos):
                    depth, pos = depth - 1, pos + 2
                else:
                    pos += 1
            if depth:
                raise SqlGuardError("unterminated comment")
        elif match.group("dollar"):
            closer = match.group("dollar")
            end = sql.find(closer, match.end())
            if end < 0:
                raise SqlGuardError("unterminated dollar-quoted string")
            pos = end + len(closer)
            tokens.append(Token("string", sql[match.end():end], start))
        elif match.group("estring") or match.group("string"):
            escaped = bool(match.group("estring"))
            pos = match.end()
            while True:
                if pos >= length:
                    raise SqlGuardError("unterminated string literal")
                if escaped and sql[pos] == "\\":
                    pos += 2
                    continue
                if sql[pos] == "'":
                    if sql.startswith("''", pos):
                        pos += 2
                        continue
                    pos += 1
                    break
                pos += 1
            tokens.append(Token("string", sql[start:pos], start))
        elif match.group("ident"):
            pos = match.end()
            while True:
                if pos >= length:
                    raise SqlGuardError("unterminated quoted identifier")
                if sql[pos] == '"':
                    if sql.startswith('""', pos):
                        pos += 2
                        continue
                    pos += 1
                    break
                pos += 1
            tokens.append(Token("ident", sql[start + 1:pos - 1].replace('""', '"'), start))
        elif match.group("word"):
            pos = match.end()
            tokens.append(Token("word", match.group("word"), start))
        elif match.group("number"):
            pos = match.end()
            tokens.append(Token("number", match.group("number"), start))
        else:
            pos = match.end()
            tokens.append(Token("symbol", match.group("symbol"), start))
    return tokens


@dataclass(frozen=True)
class Verdict:
    kind: StatementKind
    statement: str                 # trimmed, without the trailing semicolon
    first_keyword: str
    targets: list[str] = field(default_factory=list)   # tables a write statement names


def _strip_trailing_semicolon(sql: str, tokens: list[Token]) -> tuple[str, list[Token]]:
    if tokens and tokens[-1].kind == "symbol" and tokens[-1].text == ";":
        return sql[: tokens[-1].position].rstrip(), tokens[:-1]
    return sql.rstrip(), tokens


def _write_target(tokens: list[Token], index: int) -> tuple[str, int]:
    """The (possibly schema-qualified) name starting at ``tokens[index]``."""
    parts: list[str] = []
    while index < len(tokens) and tokens[index].kind in ("word", "ident"):
        token = tokens[index]
        parts.append(token.text if token.kind == "ident" else token.text.lower())
        index += 1
        if index < len(tokens) and tokens[index] == Token("symbol", ".", tokens[index].position):
            index += 1
            continue
        break
    return ".".join(parts), index


def _is_clause_keyword(tokens: list[Token], position: int) -> bool:
    """``UPDATE`` as part of ``FOR UPDATE`` / ``FOR NO KEY UPDATE`` /
    ``ON CONFLICT DO UPDATE`` rather than the start of an UPDATE statement."""
    previous = tokens[position - 1]
    return previous.kind == "word" and previous.text.upper() in {"FOR", "KEY", "DO"}


def _check_write_target(name: str) -> None:
    if not name:
        raise SqlGuardError("could not tell which table the statement writes to")
    parts = name.split(".")
    schema, table = (parts[-2], parts[-1]) if len(parts) >= 2 else ("public", parts[-1])
    if schema not in ALLOWED_SCHEMAS or table in HIDDEN_TABLES:
        raise SqlGuardError(f"writes to {name} are not allowed; only tables in {', '.join(sorted(ALLOWED_SCHEMAS))} can be changed")


def check(sql: str) -> Verdict:
    """Classify ``sql`` or raise :class:`SqlGuardError`."""
    if not sql or not sql.strip():
        raise SqlGuardError("the statement is empty")
    tokens = tokenize(sql)
    statement, tokens = _strip_trailing_semicolon(sql, tokens)
    if not tokens:
        raise SqlGuardError("the statement is empty")
    if any(token.kind == "symbol" and token.text == ";" for token in tokens):
        raise SqlGuardError("one statement at a time (remove the ';' between statements)")

    first = tokens[0]
    if first.kind != "word" or first.text.upper() not in ALLOWED_STARTERS:
        raise SqlGuardError(
            f"statements starting with {first.text!r} are not allowed; use "
            f"{', '.join(sorted(ALLOWED_STARTERS))}"
        )
    keyword = first.text.upper()

    for token in tokens:
        if token.kind != "word":
            continue
        upper = token.text.upper()
        if upper in BLOCKED_KEYWORDS:
            raise SqlGuardError(f"{upper} is not allowed (position {token.position})")
        if token.text.lower() in BLOCKED_FUNCTIONS:
            raise SqlGuardError(f"{token.text} is not allowed (position {token.position})")

    targets: list[str] = []
    if keyword in WRITE_STARTERS:
        kind: StatementKind = "write"
        index = 1
        if keyword == "UPDATE" and index < len(tokens) and tokens[index].kind == "word" and tokens[index].text.upper() == "ONLY":
            index += 1
        if keyword == "INSERT":
            if index < len(tokens) and tokens[index].kind == "word" and tokens[index].text.upper() == "INTO":
                index += 1
            else:
                raise SqlGuardError("INSERT must be followed by INTO <table>")
        target, _ = _write_target(tokens, index)
        _check_write_target(target)
        targets.append(target)
        # A nested write (INSERT/UPDATE in a CTE or subquery) must obey the same rule.
        for position, token in enumerate(tokens[1:], start=1):
            if token.kind == "word" and token.text.upper() in WRITE_STARTERS and not _is_clause_keyword(tokens, position):
                nested_index = position + 1
                if token.text.upper() == "INSERT":
                    nested_index += 1
                nested, _ = _write_target(tokens, nested_index)
                if nested and nested not in targets:
                    _check_write_target(nested)
                    targets.append(nested)
    elif keyword in EXPLAIN_STARTERS:
        kind = "explain"
    else:
        kind = "read"
        # INSERT/UPDATE inside a read statement (data-modifying CTE) would run
        # in the read-only transaction and fail there; refuse it up front
        # with a better message.
        for position, token in enumerate(tokens[1:], start=1):
            if token.kind != "word" or token.text.upper() not in WRITE_STARTERS:
                continue
            if _is_clause_keyword(tokens, position):
                raise SqlGuardError("FOR UPDATE / FOR NO KEY UPDATE is not allowed: read statements run read-only")
            raise SqlGuardError(
                f"{token.text.upper()} inside a {keyword} statement is not allowed; "
                "run the INSERT or UPDATE as its own statement"
            )
    return Verdict(kind=kind, statement=statement, first_keyword=keyword, targets=targets)

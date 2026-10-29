"""Data table foundation: models, save-time rules and the DDL builder (no
database needed; the statements are executed in ``test_tables_schema.py``)."""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import ValidationError

from models.forms import OPERATORS_BY_FIELD_TYPE, FieldType
from models.tables import (
    COLUMN_TYPES,
    TABLE_SERVER_MANAGED_KEYS,
    DataTable,
    DataTableCreate,
    DataTableUpdate,
    TableColumn,
    TableRow,
    TableRowUpdate,
)
from services import table_ddl
from services.ids import unique_table_id
from services.table_rules import MAX_COLUMNS, TableRuleError, table_problems, validate_table_rules

STATUS = {"id": "status", "name": "Status", "type": "select", "options": ["Open", "Closed"]}
TITLE = {"id": "title", "name": "Title", "type": "text", "required": True}


def _table(*columns: dict, **overrides) -> DataTableCreate:
    return DataTableCreate.model_validate({"name": "Work Orders", "columns": list(columns), **overrides})


# -- column types -----------------------------------------------------------------

def test_column_types_are_form_field_types_so_operators_and_coercion_are_shared():
    assert set(COLUMN_TYPES) <= set(get_args(FieldType))
    assert all(column_type in OPERATORS_BY_FIELD_TYPE for column_type in COLUMN_TYPES)
    assert "radio" not in COLUMN_TYPES
    assert set(table_ddl.SQL_TYPES) == set(COLUMN_TYPES), "every column type needs a SQL type"


def test_unknown_column_type_is_rejected_structurally():
    with pytest.raises(ValidationError):
        TableColumn.model_validate({"id": "x", "name": "X", "type": "radio"})


# -- models -----------------------------------------------------------------------

def test_definition_round_trips_as_camel_case():
    table = DataTable(id="work-orders", created_at="2026-01-01T00:00:00.000Z", **_table(STATUS, TITLE).model_dump(
        exclude=TABLE_SERVER_MANAGED_KEYS))
    record = table.to_record()
    assert list(record)[0] == "id"
    assert record["createdAt"] == "2026-01-01T00:00:00.000Z"
    assert record["columns"][0] == {
        "id": "status", "name": "Status", "type": "select", "required": False,
        "description": "", "options": ["Open", "Closed"],
    }
    assert DataTable.model_validate(record) == table
    assert table.column("title").required is True and table.column("missing") is None


def test_names_and_options_are_trimmed():
    table = _table({"id": "status", "name": "  Status ", "type": "select", "options": [" Open ", "", "  "]})
    assert table.columns[0].name == "Status" and table.columns[0].options == ["Open"]
    with pytest.raises(ValidationError, match="Table name is required"):
        _table(name="   ")
    with pytest.raises(ValidationError):
        _table(name="x" * 121)


def test_unknown_keys_are_rejected():
    with pytest.raises(ValidationError):
        TableColumn.model_validate({**TITLE, "colour": "red"})
    with pytest.raises(ValidationError):
        TableRowUpdate.model_validate({"data": {}, "rowId": "x"})


def test_a_get_response_is_valid_put_input():
    stored = DataTable(id="work-orders", created_at="2026-01-01T00:00:00.000Z",
                       updated_at="2026-01-02T00:00:00.000Z", name="Work Orders", columns=[TITLE])
    update = DataTableUpdate.model_validate(stored.to_record())
    assert set(update.model_dump(exclude=TABLE_SERVER_MANAGED_KEYS)) == {"name", "description", "columns"}


def test_models_load_rule_breaking_definitions_so_problems_can_be_shown():
    """Rules are checked on save; the models themselves only check structure."""
    stored = DataTable.model_validate({
        "id": "legacy", "name": "Legacy",
        "columns": [
            {"id": "Bad Id", "name": "", "type": "select"},
            {"id": "Bad Id", "name": "Dup", "type": "number", "options": ["x"]},
        ],
    })
    assert len(stored.columns) == 2
    assert len(table_problems(stored)) >= 4


def test_row_values_keep_their_json_types():
    row = TableRow(id=7, table_id="t", data={"n": 2, "f": 2.5, "b": True, "s": "2", "e": None})
    assert row.to_record() == {"id": 7, "tableId": "t", "data": {"n": 2, "f": 2.5, "b": True, "s": "2", "e": None},
                               "createdAt": None, "updatedAt": None}
    assert isinstance(row.data["n"], int) and isinstance(row.data["b"], bool)
    # Cells may carry structures (jsonb / text[] columns); the column type decides (services/table_rows.py).
    assert TableRowUpdate(data={"x": ["a"], "y": {"k": 1}}).data == {"x": ["a"], "y": {"k": 1}}
    assert TableRow(id="sub-1", table_id="public.submissions").id == "sub-1"


# -- rules --------------------------------------------------------------------------

def test_valid_definition_passes():
    validate_table_rules(_table(STATUS, TITLE, {"id": "due_date", "name": "Due", "type": "date"}))
    validate_table_rules(_table())  # a table may start without columns


@pytest.mark.parametrize(
    "column, message",
    [
        ({"id": "9lives", "name": "A", "type": "text"}, "must start with a lower-case letter"),
        ({"id": "camelCase", "name": "A", "type": "text"}, "must start with a lower-case letter"),
        ({"id": "has-dash", "name": "A", "type": "text"}, "must start with a lower-case letter"),
        ({"id": "has space", "name": "A", "type": "text"}, "must start with a lower-case letter"),
        ({"id": "id", "name": "A", "type": "text"}, "is reserved"),
        ({"id": "created_at", "name": "A", "type": "text"}, "is reserved"),
        ({"id": "ctid", "name": "A", "type": "text"}, "is reserved"),
        ({"id": "a" * 49, "name": "A", "type": "text"}, "longer than 48"),
        ({"id": "done", "name": "Done", "type": "checkbox", "required": True}, "'required' does not apply"),
        ({"id": "a", "name": "", "type": "text"}, "needs a name"),
        ({"id": "a", "name": "n" * 121, "type": "text"}, "name is longer than 120"),
        ({"id": "a", "name": "A", "type": "select"}, "needs at least one option"),
        ({"id": "a", "name": "A", "type": "select", "options": ["Yes", "yes"]}, "duplicate options: yes"),
        ({"id": "a", "name": "A", "type": "number", "options": ["1"]}, "only allowed on select"),
    ],
)
def test_column_rules(column, message):
    with pytest.raises(TableRuleError, match=re.escape(message)):
        validate_table_rules(_table(column))


def test_column_ids_unique_and_names_unique_case_insensitively():
    problems = table_problems(_table(
        TITLE, {**TITLE, "name": "Other"}, {"id": "title2", "name": "TITLE", "type": "text"},
    ))
    assert "Duplicate column id 'title'" in problems
    assert "Duplicate column name 'TITLE'" in problems


def test_column_limit():
    columns = [{"id": f"c{i}", "name": f"C{i}", "type": "text"} for i in range(MAX_COLUMNS + 1)]
    with pytest.raises(TableRuleError, match=f"at most {MAX_COLUMNS} columns"):
        validate_table_rules(_table(*columns))


def test_every_problem_is_reported_at_once():
    with pytest.raises(TableRuleError) as info:
        validate_table_rules(_table({"id": "1", "name": "", "type": "select"}))
    assert len(info.value.problems) == 3


def test_sql_keywords_are_fine_as_column_ids():
    """They are always quoted, so `select`, `order`, `table` are legal ids."""
    validate_table_rules(_table({"id": "select", "name": "S", "type": "text"},
                                {"id": "order", "name": "O", "type": "number"}))


# -- ids ------------------------------------------------------------------------------

def test_table_ids_and_physical_names():
    assert unique_table_id("Work Orders", {"work-orders"}.__contains__) == "work-orders-2"
    assert unique_table_id("!!!", lambda _: False) == "table"
    assert table_ddl.table_name_for("work-orders-2") == "work_orders_2"


# -- DDL builder ------------------------------------------------------------------------

def _sql(statement) -> str:
    """Render a Composed statement without a connection (psycopg escapes identifiers the same way)."""
    return statement.as_string(None)


def test_create_table_statement_covers_every_column_type():
    columns = [
        TableColumn(id="title", name="Title", type="text", required=True),
        TableColumn(id="notes", name="Notes", type="textarea"),
        TableColumn(id="cost", name="Cost", type="number"),
        TableColumn(id="due", name="Due", type="date", required=True),
        TableColumn(id="status", name="Status", type="select", options=["Open", "Closed"]),
        TableColumn(id="urgent", name="Urgent", type="checkbox"),
    ]
    assert _sql(table_ddl.create_table("work_orders", columns)) == (
        'CREATE TABLE "user_tables"."work_orders" ('
        "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "updated_at timestamptz NOT NULL DEFAULT now(), "
        '"title" text NOT NULL, '
        '"notes" text, '
        '"cost" numeric, '
        '"due" date NOT NULL, '
        '"status" text CONSTRAINT "status_options" CHECK ("status" IN (\'Open\', \'Closed\')), '
        '"urgent" boolean NOT NULL DEFAULT false)'
    )


def test_alter_statements():
    assert _sql(table_ddl.add_column("t", TableColumn(id="cost", name="Cost", type="number", required=True))) == (
        'ALTER TABLE "user_tables"."t" ADD COLUMN "cost" numeric NOT NULL'
    )
    assert _sql(table_ddl.drop_column("t", "cost")) == 'ALTER TABLE "user_tables"."t" DROP COLUMN "cost"'
    assert _sql(table_ddl.alter_column_type("t", "cost", "text")) == (
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "cost" TYPE text USING "cost"::text'
    )
    assert _sql(table_ddl.set_required("t", "cost", True)) == 'ALTER TABLE "user_tables"."t" ALTER COLUMN "cost" SET NOT NULL'
    assert _sql(table_ddl.set_required("t", "cost", False)) == 'ALTER TABLE "user_tables"."t" ALTER COLUMN "cost" DROP NOT NULL'
    assert _sql(table_ddl.drop_options_check("t", "status")) == (
        'ALTER TABLE "user_tables"."t" DROP CONSTRAINT IF EXISTS "status_options"'
    )
    assert _sql(table_ddl.add_options_check("t", TableColumn(id="status", name="S", type="select", options=["A"]))) == (
        'ALTER TABLE "user_tables"."t" ADD CONSTRAINT "status_options" CHECK ("status" IN (\'A\'))'
    )
    assert _sql(table_ddl.drop_table("t")) == 'DROP TABLE "user_tables"."t"'


def test_user_text_is_never_interpolated_into_sql():
    """Identifiers are quoted and option values are literals, so hostile
    input is inert. (The rules reject such ids anyway; this is the second line.)"""
    hostile = TableColumn(id="x", name="X", type="select", options=["a'); DROP TABLE forms; --"])
    statement = _sql(table_ddl.add_options_check('t"; DROP TABLE forms; --', hostile))
    assert statement == (
        'ALTER TABLE "user_tables"."t""; DROP TABLE forms; --" ADD CONSTRAINT "x_options" '
        'CHECK ("x" IN (\'a\'\'); DROP TABLE forms; --\'))'
    )


# -- definition diff -> ALTER steps (repositories/pg_tables.py) -----------------------

from repositories.pg_tables import ColumnChange, ddl_for_change, diff_columns  # noqa: E402


def _col(id: str, type: str = "text", **kw) -> TableColumn:
    return TableColumn(id=id, name=id.title(), type=type, **kw)


def test_diff_matches_columns_by_id_kept_first():
    stored = [_col("a"), _col("b"), _col("c")]
    desired = [_col("d"), _col("c", "number"), _col("a")]
    changes = diff_columns(stored, desired)
    assert [(c.before and c.before.id, c.after and c.after.id) for c in changes] == [
        ("a", "a"), ("b", None), ("c", "c"), (None, "d"),
    ]


def test_ddl_steps_per_change():
    steps = lambda before, after: [s.as_string(None) for s in ddl_for_change("t", ColumnChange(before, after))]
    assert steps(None, _col("a", required=True)) == ['ALTER TABLE "user_tables"."t" ADD COLUMN "a" text NOT NULL']
    assert steps(_col("a"), None) == ['ALTER TABLE "user_tables"."t" DROP COLUMN "a"']
    assert steps(_col("a"), _col("a")) == []
    assert steps(_col("a"), _col("a", "textarea")) == []                     # same SQL type
    assert steps(_col("a"), _col("a", required=True)) == ['ALTER TABLE "user_tables"."t" ALTER COLUMN "a" SET NOT NULL']
    assert steps(_col("a", "number"), _col("a", "text")) == [
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "a" TYPE text USING "a"::text',
    ]
    # select -> number: the CHECK goes before the cast, no CHECK afterwards.
    assert steps(_col("a", "select", options=["1"]), _col("a", "number")) == [
        'ALTER TABLE "user_tables"."t" DROP CONSTRAINT IF EXISTS "a_options"',
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "a" TYPE numeric USING "a"::numeric',
    ]
    # options change only: replace the CHECK.
    assert steps(_col("a", "select", options=["x"]), _col("a", "select", options=["x", "y"])) == [
        'ALTER TABLE "user_tables"."t" DROP CONSTRAINT IF EXISTS "a_options"',
        'ALTER TABLE "user_tables"."t" ADD CONSTRAINT "a_options" CHECK ("a" IN (\'x\', \'y\'))',
    ]
    assert steps(_col("a", "select", options=["x"]), _col("a", "select", options=["x"])) == []
    # text -> checkbox: cast, NULL -> false, default, NOT NULL.
    assert steps(_col("a"), _col("a", "checkbox")) == [
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "a" TYPE boolean USING "a"::boolean',
        'UPDATE "user_tables"."t" SET "a" = false WHERE "a" IS NULL',
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "a" SET DEFAULT false',
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "a" SET NOT NULL',
    ]
    assert steps(_col("a", "checkbox"), _col("a")) == [
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "a" TYPE text USING "a"::text',
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "a" DROP DEFAULT',
        'ALTER TABLE "user_tables"."t" ALTER COLUMN "a" DROP NOT NULL',
    ]

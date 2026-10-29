"""Tests for services/form_logic.py.

The cases mirror ``frontend/__tests__/lib/formLogic.test.ts`` so the two
implementations stay in lock-step.
"""

from __future__ import annotations

import pytest

from models.forms import FieldConfig, FieldLogic
from services.form_logic import evaluate_condition, evaluate_field_logic, get_visible_field_ids, is_empty_value


def test_is_empty_value():
    assert is_empty_value(None)
    assert is_empty_value("")
    assert is_empty_value("   ")
    assert is_empty_value(float("nan"))
    assert not is_empty_value(0)
    assert not is_empty_value(False)
    assert not is_empty_value("no")


@pytest.mark.parametrize(
    "operator, expected, actual, source_type, result",
    [
        # strings
        ("equals", "Yes", "yes", "radio", True),
        ("equals", "Yes", " YES ", "radio", True),
        ("equals", "Yes", "No", "radio", False),
        ("not_equals", "Yes", "No", "radio", True),
        ("contains", "urgent", "This is URGENT please", "text", True),
        ("contains", "urgent", "all good", "text", False),
        ("not_contains", "urgent", "all good", "text", True),
        ("starts_with", "ACME", "Acme Corp", "text", True),
        ("ends_with", "Corp", "Acme Corp", "text", True),
        ("ends_with", "Inc", "Acme Corp", "text", False),
        ("is_one_of", ["Phone", "Text"], "Text", "select", True),
        ("is_one_of", "Phone, Text", "text", "select", True),
        ("is_one_of", "Phone, Text", "Email", "select", False),
        ("is_not_one_of", "Phone, Text", "Email", "select", True),
        # missing answers
        ("equals", "Yes", None, "radio", False),
        ("contains", "x", "", "text", False),
        ("not_equals", "Yes", None, "radio", True),
        ("not_contains", "x", "", "text", True),
        ("is_not_one_of", "a,b", None, "select", True),
        ("is_empty", None, "", "text", True),
        ("is_empty", None, "hi", "text", False),
        ("is_not_empty", None, "hi", "text", True),
        ("is_not_empty", None, None, "text", False),
        # numbers
        ("greater_than", 10, 11, "number", True),
        ("greater_than", "10", "9", "number", False),
        ("greater_than", "9", "10", "number", True),
        ("greater_than_or_equal", 10, 10, "number", True),
        ("less_than", 10, 9.5, "number", True),
        ("less_than_or_equal", 10, 10, "number", True),
        ("less_than_or_equal", 10, 10.1, "number", False),
        ("equals", "5", 5, "number", True),
        ("not_equals", "5", 6, "number", True),
        ("equals", 0, 0, "number", True),
        ("less_than", 1, 0, "number", True),
        ("greater_than", 10, "abc", "number", False),
        ("greater_than", 10, None, "number", False),
        ("equals", "abc", 10, "number", False),
        # dates
        ("before", "2024-06-01", "2024-05-31", "date", True),
        ("before", "2024-06-01", "2024-06-01", "date", False),
        ("on_or_before", "2024-06-01", "2024-06-01", "date", True),
        ("after", "2024-06-01", "2024-06-02", "date", True),
        ("on_or_after", "2024-06-01", "2024-06-01", "date", True),
        ("equals", "2024-06-01", "2024-06-01", "date", True),
        ("not_equals", "2024-06-01", "2024-06-02", "date", True),
        ("after", "2024-01-01", "2024-01-02T10:00:00Z", "date", True),
        ("after", "2024-01-01", "not a date", "date", False),
        ("after", "garbage", "2024-01-01", "date", False),
        ("before", "2024-01-01", None, "date", False),
        # checkbox
        ("is_checked", None, True, "checkbox", True),
        ("is_checked", None, False, "checkbox", False),
        ("is_checked", None, None, "checkbox", False),
        ("is_not_checked", None, False, "checkbox", True),
        ("is_not_checked", None, None, "checkbox", True),
    ],
)
def test_evaluate_condition(operator, expected, actual, source_type, result):
    assert evaluate_condition(operator, expected, actual, source_type) is result


def _logic(action="show", match="all", **_):
    return lambda conditions: FieldLogic(action=action, match=match, conditions=conditions)


def test_field_without_logic_is_visible():
    assert evaluate_field_logic(None, {}) is True
    assert evaluate_field_logic(FieldLogic(conditions=[]), {}) is True


def test_match_all_and_any():
    types = {"pets": "radio", "count": "number"}
    conditions = [
        {"fieldId": "pets", "operator": "equals", "value": "Yes"},
        {"fieldId": "count", "operator": "greater_than_or_equal", "value": 2},
    ]
    all_logic = FieldLogic(action="show", match="all", conditions=conditions)
    any_logic = FieldLogic(action="show", match="any", conditions=conditions)

    assert evaluate_field_logic(all_logic, {"pets": "Yes", "count": 2}, types)
    assert not evaluate_field_logic(all_logic, {"pets": "Yes", "count": 1}, types)
    assert evaluate_field_logic(any_logic, {"pets": "No", "count": 5}, types)
    assert not evaluate_field_logic(any_logic, {"pets": "No", "count": 1}, types)


def test_hide_inverts():
    logic = FieldLogic(action="hide", conditions=[{"fieldId": "pets", "operator": "equals", "value": "No"}])
    assert evaluate_field_logic(logic, {"pets": "No"}) is False
    assert evaluate_field_logic(logic, {"pets": "Yes"}) is True
    assert evaluate_field_logic(logic, {}) is True


PET_FIELDS = [
    FieldConfig(id="hasPet", type="radio", label="Pet?", options=["Yes", "No"]),
    FieldConfig(
        id="petType", type="select", label="Type", options=["Dog", "Cat"],
        logic={"conditions": [{"fieldId": "hasPet", "operator": "equals", "value": "Yes"}]},
    ),
    FieldConfig(
        id="dogBreed", type="text", label="Breed",
        logic={"conditions": [{"fieldId": "petType", "operator": "equals", "value": "Dog"}]},
    ),
    FieldConfig(id="notes", type="textarea", label="Notes"),
]


def test_visible_fields_progressively_revealed():
    assert get_visible_field_ids(PET_FIELDS, {}) == ["hasPet", "notes"]
    assert get_visible_field_ids(PET_FIELDS, {"hasPet": "Yes"}) == ["hasPet", "petType", "notes"]
    assert get_visible_field_ids(PET_FIELDS, {"hasPet": "Yes", "petType": "Dog"}) == [
        "hasPet", "petType", "dogBreed", "notes",
    ]


def test_chain_collapses_when_upstream_hidden():
    visible = get_visible_field_ids(PET_FIELDS, {"hasPet": "No", "petType": "Dog"})
    assert "petType" not in visible
    assert "dogBreed" not in visible

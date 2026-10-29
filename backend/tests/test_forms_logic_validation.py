"""Saving a form validates its conditional logic."""

from __future__ import annotations


def _form(fields):
    return {"name": "Logic Form", "fields": fields}


BASE = [
    {"id": "hasPet", "type": "radio", "label": "Pet?", "options": ["Yes", "No"]},
    {"id": "age", "type": "number", "label": "Age"},
]


def test_valid_logic_is_saved(client):
    fields = BASE + [
        {
            "id": "petName",
            "type": "text",
            "label": "Pet name",
            "logic": {
                "action": "show",
                "match": "all",
                "conditions": [{"fieldId": "hasPet", "operator": "equals", "value": "Yes"}],
            },
        },
        {
            "id": "guardian",
            "type": "text",
            "label": "Guardian",
            "logic": {
                "action": "show",
                "match": "any",
                "conditions": [
                    {"fieldId": "age", "operator": "less_than", "value": 18},
                    {"fieldId": "age", "operator": "is_empty"},
                ],
            },
        },
    ]
    response = client.post("/forms", json=_form(fields))
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["fields"][2]["logic"]["conditions"][0] == {
        "fieldId": "hasPet",
        "operator": "equals",
        "value": "Yes",
    }
    # Round-trips through GET unchanged.
    fetched = client.get(f"/forms/{saved['id']}").json()
    assert fetched["fields"][3]["logic"]["match"] == "any"


def test_logic_defaults_are_filled_in(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "hasPet", "operator": "is_not_empty"}]}},
    ]
    saved = client.post("/forms", json=_form(fields)).json()
    assert saved["fields"][2]["logic"]["action"] == "show"
    assert saved["fields"][2]["logic"]["match"] == "all"


def test_logic_cannot_reference_later_field(client):
    fields = [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "age", "operator": "equals", "value": 1}]}},
    ] + BASE
    response = client.post("/forms", json=_form(fields))
    assert response.status_code == 422
    assert "not an earlier field" in response.text


def test_logic_cannot_reference_self(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "x", "operator": "is_empty"}]}},
    ]
    response = client.post("/forms", json=_form(fields))
    assert response.status_code == 422
    assert "cannot reference itself" in response.text


def test_operator_must_suit_source_type(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "age", "operator": "contains", "value": "1"}]}},
    ]
    response = client.post("/forms", json=_form(fields))
    assert response.status_code == 422
    assert "cannot be used with a 'number' field" in response.text


def test_unknown_operator_rejected(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "age", "operator": "approximately", "value": "1"}]}},
    ]
    assert client.post("/forms", json=_form(fields)).status_code == 422


def test_comparison_operators_need_a_value(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "age", "operator": "greater_than"}]}},
    ]
    response = client.post("/forms", json=_form(fields))
    assert response.status_code == 422
    assert "needs a value" in response.text


def test_logic_validated_on_update_too(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "missing", "operator": "is_empty"}]}},
    ]
    assert client.put("/forms/contact-form", json=_form(fields)).status_code == 422


def test_condition_value_must_be_one_of_source_options(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "hasPet", "operator": "equals", "value": "Maybe"}]}},
    ]
    response = client.post("/forms", json=_form(fields))
    assert response.status_code == 422
    assert "not one of the options" in response.text


def test_condition_list_values_must_all_be_options(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "hasPet", "operator": "is_one_of", "value": ["Yes", "Sometimes"]}]}},
    ]
    response = client.post("/forms", json=_form(fields))
    assert response.status_code == 422
    assert "Sometimes" in response.text


def test_condition_value_matching_option_case_insensitively_is_ok(client):
    fields = BASE + [
        {"id": "x", "type": "text", "label": "X",
         "logic": {"conditions": [{"fieldId": "hasPet", "operator": "equals", "value": "yes"}]}},
    ]
    assert client.post("/forms", json=_form(fields)).status_code == 201

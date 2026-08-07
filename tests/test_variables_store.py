"""Tests for variables_store: persistence, CRUD, template, random combos."""

from __future__ import annotations

from unittest.mock import patch

import variables_store


def test_default_lists_seeded_on_first_access(variables_file):
    lists = variables_store.get_lists()
    assert set(lists) == {"poses", "angles", "actions"}
    assert all(len(items) > 0 for items in lists.values())


def test_defaults_persisted_to_disk(variables_file):
    variables_store.get_lists()
    assert variables_file.exists()
    data = variables_store._load()
    assert "lists" in data
    assert data["template"] == variables_store.DEFAULT_TEMPLATE


def test_add_item_appends_and_persists(variables_file):
    assert variables_store.add_item("poses", "volando")
    assert "volando" in variables_store.get_list("poses")
    # survives a reload
    assert "volando" in variables_store.get_lists()["poses"]


def test_add_item_rejects_duplicate_and_invalid(variables_file):
    assert variables_store.add_item("poses", "de pie") is False  # duplicate default
    assert variables_store.add_item("poses", "   ") is False  # blank
    assert variables_store.add_item("unknown", "x") is False  # bad list name


def test_add_item_has_no_length_limit(variables_file):
    """List options must not be capped (previously limited to 200 chars)."""
    long_text = ("Peso apoyado en la pierna trasera, torso girado 45 grados, mentón hacia el hombro. " * 4).strip()
    assert len(long_text) > 200
    assert variables_store.add_item("poses", long_text)
    assert long_text in variables_store.get_list("poses")


def test_update_item_replaces_in_place(variables_file):
    assert variables_store.update_item("poses", 0, "tumbado")
    items = variables_store.get_list("poses")
    assert items[0] == "tumbado"
    assert "de pie" not in items


def test_update_item_out_of_range_and_duplicate(variables_file):
    assert variables_store.update_item("poses", 999, "x") is False
    assert variables_store.update_item("poses", 0, "de rodillas") is False  # duplicate


def test_update_item_noop_same_text_is_success(variables_file):
    items = variables_store.get_list("poses")
    assert variables_store.update_item("poses", 0, items[0]) is True


def test_delete_item_removes(variables_file):
    before = variables_store.get_list("poses")
    assert variables_store.delete_item("poses", 0)
    after = variables_store.get_list("poses")
    assert len(after) == len(before) - 1
    assert before[0] not in after
    assert variables_store.delete_item("poses", 999) is False


def test_template_default_and_set(variables_file):
    assert variables_store.get_template() == "{pose}, {angle}, {action}"
    assert variables_store.set_template("El sujeto está {pose} con {angle} y {action}")
    assert variables_store.get_template() == "El sujeto está {pose} con {angle} y {action}"
    assert variables_store.set_template("   ") is False
    assert variables_store.get_template() == "El sujeto está {pose} con {angle} y {action}"


def test_set_template_has_no_length_limit(variables_file):
    """The prompt template must not be capped (previously limited to 500 chars)."""
    long_template = ("El sujeto está {pose} con {angle} y {action}, además " * 20).strip()
    assert len(long_template) > 500
    assert variables_store.set_template(long_template)
    assert variables_store.get_template() == long_template


def test_build_prompt_fills_placeholders(variables_file):
    assert (
        variables_store.build_prompt("de pie", "frontal", "mirando")
        == "de pie, frontal, mirando"
    )


def test_build_prompt_fallback_on_unknown_placeholder(variables_file):
    variables_store.set_template("{unknown} {pose}")
    prompt = variables_store.build_prompt("a", "b", "c")
    assert prompt == "a, b, c"


def test_build_prompt_fallback_on_attribute_error(variables_file):
    variables_store.set_template("{pose.foo}")
    prompt = variables_store.build_prompt("a", "b", "c")
    assert prompt == "a, b, c"


def test_random_combination_uses_lists_and_template(variables_file):
    with patch(
        "variables_store.random.choice",
        side_effect=["de pie", "lateral", "mirando a la cámara"],
    ):
        prompt, combo = variables_store.random_combination()
    assert combo == ("de pie", "lateral", "mirando a la cámara")
    assert prompt == "de pie, lateral, mirando a la cámara"


def test_random_combination_avoids_exclude(variables_file):
    exclude = {("de pie", "lateral", "mirando a la cámara")}
    with patch(
        "variables_store.random.choice",
        side_effect=["de pie", "lateral", "mirando a la cámara", "sentado", "cenital", "saltando"],
    ):
        prompt, combo = variables_store.random_combination(exclude=exclude)
    assert combo == ("sentado", "cenital", "saltando")
    assert combo not in exclude
    assert "sentado" in prompt


def test_random_combination_returns_none_on_empty_list(variables_file):
    # empty the poses list entirely
    while variables_store.delete_item("poses", 0):
        pass
    assert variables_store.random_combination() is None

"""Tests for variables_store: persistence, CRUD, template, random combos."""

from __future__ import annotations

import json
from unittest.mock import patch

import variables_store


def test_default_lists_seeded_on_first_access(variables_file):
    lists = variables_store.get_lists()
    assert set(lists) == {"poses", "angles"}
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
    assert variables_store.add_item("poses", "standing with weight shifted to one leg, free hand resting on hip") is False  # duplicate default
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
    assert variables_store.update_item("poses", 0, "combat-ready stance with knees slightly bent and torso angled forward") is False  # duplicate


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
    assert variables_store.get_template() == "{pose}, {angle}"
    assert variables_store.set_template("El sujeto está {pose} con {angle}")
    assert variables_store.get_template() == "El sujeto está {pose} con {angle}"
    assert variables_store.set_template("   ") is False
    assert variables_store.get_template() == "El sujeto está {pose} con {angle}"


def test_set_template_has_no_length_limit(variables_file):
    """The prompt template must not be capped (previously limited to 500 chars)."""
    long_template = ("El sujeto está {pose} con {angle}, además " * 20).strip()
    assert len(long_template) > 500
    assert variables_store.set_template(long_template)
    assert variables_store.get_template() == long_template


def test_build_prompt_fills_placeholders(variables_file):
    assert (
        variables_store.build_prompt("de pie", "frontal")
        == "de pie, frontal"
    )


def test_build_prompt_fallback_on_unknown_placeholder(variables_file):
    variables_store.set_template("{unknown} {pose}")
    prompt = variables_store.build_prompt("a", "b")
    assert prompt == "a, b"


def test_build_prompt_fallback_on_attribute_error(variables_file):
    variables_store.set_template("{pose.foo}")
    prompt = variables_store.build_prompt("a", "b")
    assert prompt == "a, b"


JSON_TEMPLATE = (
    '{\n'
    '  "subject": "2B",\n'
    '  "pose": "{pose}",\n'
    '  "camera": {"angle": "{angle}"},\n'
    '  "note": "keep braces literal {}"\n'
    '}'
)


def test_build_prompt_renders_json_template(variables_file):
    variables_store.set_template(JSON_TEMPLATE)
    prompt = variables_store.build_prompt("de pie", "frontal")
    assert '"pose": "de pie"' in prompt
    assert '"camera": {"angle": "frontal"}' in prompt
    # Literal JSON braces survive the render (str.format used to choke on them).
    assert prompt.startswith("{")
    assert prompt.endswith("}")
    assert '"keep braces literal {}"' in prompt
    data = json.loads(prompt)
    assert data["pose"] == "de pie"
    assert data["camera"]["angle"] == "frontal"


def test_build_prompt_shuffled_renders_json_template(variables_file):
    variables_store.set_template(JSON_TEMPLATE)
    with patch("variables_store.random.shuffle", side_effect=lambda x: x.reverse()):
        prompt = variables_store.build_prompt_shuffled("A", "B")
    data = json.loads(prompt)
    assert data["pose"] == "B"
    assert data["camera"]["angle"] == "A"


def test_template_fields_ignores_json_braces(variables_file):
    variables_store.set_template(JSON_TEMPLATE)
    assert variables_store.template_fields() == ["pose", "angle"]


def test_build_prompt_json_template_single_field(variables_file):
    variables_store.set_template('{"subject": "2B", "pose": "{pose}"}')
    prompt = variables_store.build_prompt("de pie", "frontal")
    assert json.loads(prompt)["pose"] == "de pie"


def test_random_combination_uses_lists_and_template(variables_file):
    with patch(
        "variables_store.random.choice",
        side_effect=["de pie", "lateral"],
    ):
        prompt, combo = variables_store.random_combination()
    assert combo == ("de pie", "lateral")
    assert prompt == "de pie, lateral"


def test_random_combination_avoids_exclude(variables_file):
    exclude = {("de pie", "lateral")}
    with patch(
        "variables_store.random.choice",
        side_effect=["de pie", "lateral", "sentado", "cenital"],
    ):
        prompt, combo = variables_store.random_combination(exclude=exclude)
    assert combo == ("sentado", "cenital")
    assert combo not in exclude
    assert "sentado" in prompt


def test_random_combination_returns_none_on_empty_list(variables_file):
    # empty the poses list entirely
    while variables_store.delete_item("poses", 0):
        pass
    assert variables_store.random_combination() is None


def test_template_fields_returns_placeholder_names(variables_file):
    assert variables_store.template_fields() == ["pose", "angle"]


def test_template_fields_tracks_custom_template(variables_file):
    variables_store.set_template("{pose} {angle}")
    assert variables_store.template_fields() == ["pose", "angle"]


def test_combo_key_tracks_only_template_fields(variables_file):
    variables_store.set_template("{pose} {angle}")
    assert variables_store.combo_key("de pie", "de frente") == ("de pie", "de frente")


def test_combo_key_default_template_keeps_both(variables_file):
    assert variables_store.combo_key("a", "b") == ("a", "b")


def test_build_prompt_shuffled_swaps_two_fields(variables_file):
    variables_store.set_template("{pose} {angle}")
    # With exactly two contributing fields the derangement is a guaranteed swap.
    assert variables_store.build_prompt_shuffled("A", "B") == "B A"


def test_build_prompt_shuffled_preserves_values(variables_file):
    variables_store.set_template("{pose} {angle}")
    with patch("variables_store.random.shuffle", side_effect=lambda x: x.reverse()):
        prompt = variables_store.build_prompt_shuffled("A", "B")
    # Both values still render, just in a different order.
    assert set(prompt.split()) == {"A", "B"}
    assert prompt == "B A"


def test_blacklist_add_get_persist(variables_file):
    assert variables_store.blacklist_add(("de pie", "de frente"))
    assert ("de pie", "de frente") in variables_store.get_blacklist()
    # persisted on disk, not just in memory
    raw = variables_store._load()
    assert ["de pie", "de frente"] in raw["blacklist"]


def test_blacklist_add_rejects_duplicate_and_invalid(variables_file):
    assert variables_store.blacklist_add(("a", "b")) is True
    assert variables_store.blacklist_add(("a", "b")) is False  # duplicate
    assert variables_store.blacklist_add("not-a-tuple") is False  # invalid


def test_blacklist_clear_empties(variables_file):
    variables_store.blacklist_add(("a", "b"))
    variables_store.blacklist_clear()
    assert variables_store.get_blacklist() == set()


def test_random_combination_excludes_blacklist(variables_file):
    variables_store.blacklist_add(("de pie", "lateral"))
    with patch(
        "variables_store.random.choice",
        side_effect=["de pie", "lateral", "sentado", "cenital"],
    ):
        prompt, combo = variables_store.random_combination()
    assert combo == ("sentado", "cenital")

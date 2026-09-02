"""Tests for the pose-package system (variables_store package CRUD)."""

from __future__ import annotations

import json

import pytest

import variables_store


@pytest.fixture
def packages_dir(tmp_path, monkeypatch):
    path = tmp_path / "variables_packages"
    monkeypatch.setattr(variables_store, "PACKAGES_DIR", path)
    return path


def _valid_payload() -> dict:
    return {
        "lists": {
            "poses": ["de pie", "sentado"],
            "angles": ["frontal", "lateral"],
            "actions": ["elegante", "táctico"],
        },
        "template": "{pose}, {angle}, {action}",
    }


def test_slugify_normalizes_names():
    assert variables_store._slugify("2B Outfits!") == "2b_outfits"
    assert variables_store._slugify("  Mi Paquete  ") == "mi_paquete"
    assert variables_store._slugify("!!!") == ""


def test_save_and_list_packages(packages_dir):
    ok, err = variables_store.save_package("2b_outfits", _valid_payload())
    assert ok and err is None
    assert variables_store.package_exists("2b_outfits")
    assert "2b_outfits" in variables_store.list_packages()


def test_save_accepts_fields_alias(packages_dir):
    payload = {
        "fields": {"pose": ["a"], "angle": ["b"]},
        "template": "{pose} {angle}",
    }
    ok, err = variables_store.save_package("alias", payload)
    assert ok and err is None
    loaded = variables_store.load_package("alias")
    assert loaded["lists"] == {"pose": ["a"], "angle": ["b"]}


def test_save_rejects_invalid_payloads(packages_dir):
    assert variables_store.save_package("bad", "no es dict")[1]
    assert variables_store.save_package("bad", {"template": "x"})[1]  # no lists
    assert variables_store.save_package("bad", {"lists": {}, "template": "x"})[1]
    assert variables_store.save_package("bad", {"lists": {"pose": ["a"]}})[1]  # no template
    assert variables_store.save_package("", _valid_payload())[1]  # bad name


def test_activate_copies_into_active_file_and_sets_marker(variables_file, packages_dir):
    variables_store.save_package("pack_a", _valid_payload())
    assert variables_store.activate_package("pack_a")
    assert variables_store.active_package_name() == "pack_a"
    data = variables_store._load()
    assert set(data["lists"]) == {"poses", "angles", "actions"}
    assert data["template"] == "{pose}, {angle}, {action}"
    assert data["_package"] == "pack_a"


def test_activate_clears_blacklist(variables_file, packages_dir):
    variables_store.blacklist_add(("a", "b", "c"))
    variables_store.save_package("pack_a", _valid_payload())
    variables_store.activate_package("pack_a")
    assert variables_store.get_blacklist() == set()


def test_active_package_none_when_hand_edited(variables_file, packages_dir):
    assert variables_store.active_package_name() is None


def test_activate_missing_package_fails(variables_file, packages_dir):
    assert not variables_store.activate_package("no_existe")


def test_delete_package(packages_dir):
    variables_store.save_package("temp", _valid_payload())
    assert variables_store.delete_package("temp")
    assert not variables_store.package_exists("temp")


def test_delete_active_package_rejected(variables_file, packages_dir):
    variables_store.save_package("activo", _valid_payload())
    variables_store.activate_package("activo")
    assert not variables_store.delete_package("activo")
    assert variables_store.package_exists("activo")


def test_load_package_missing_returns_none(packages_dir):
    assert variables_store.load_package("no_existe") is None


def test_overwrite_existing_package(packages_dir):
    variables_store.save_package("p", _valid_payload())
    payload = {"fields": {"pose": ["nuevo"]}, "template": "{pose}"}
    ok, err = variables_store.save_package("p", payload)
    assert ok and err is None
    assert variables_store.load_package("p")["lists"] == {"pose": ["nuevo"]}

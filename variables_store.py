#!/usr/bin/env python3
"""Persistent lists of editing variables (poses, angles, actions) for /variables.

The bot's /variables command builds image-edit prompts by randomly combining
one item from each list. The lists and the prompt template are managed through
the Telegram admin panel (variables_flow.py) and persisted as JSON next to
sessions.json.

Prompt template placeholders:
    {pose}    — a random item from the "poses" list
    {angle}   — a random item from the "angles" list
    {action}  — a random item from the "actions" list
"""

from __future__ import annotations

import json
import random
import threading
from pathlib import Path

VARIABLES_FILE = Path(__file__).parent / "variables_lists.json"

# Guards the read-modify-write cycles over VARIABLES_FILE (single process,
# but protects against interleaved admin edits / concurrent coroutines).
_LOCK = threading.Lock()

LIST_NAMES = ("poses", "angles", "actions")

DEFAULT_TEMPLATE = "{pose}, {angle}, {action}"

DEFAULT_LISTS: dict[str, list[str]] = {
    "poses": [
        "de pie",
        "sentado",
        "acostado",
        "de rodillas",
        "caminando",
        "saltando",
    ],
    "angles": [
        "ángulo frontal",
        "ángulo lateral",
        "ángulo cenital",
        "ángulo picado",
        "ángulo contrapicado",
    ],
    "actions": [
        "mirando a la cámara",
        "con los brazos cruzados",
        "señalando al horizonte",
        "sosteniendo una taza de café",
        "saludando con la mano",
    ],
}

# Maximum random draws when trying to avoid repeating a combination within a batch.
MAX_COMBO_ATTEMPTS = 30


def _load() -> dict:
    if VARIABLES_FILE.exists():
        try:
            with open(VARIABLES_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: dict) -> None:
    VARIABLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VARIABLES_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _normalize_items(raw) -> list[str]:
    """Coerce a stored list value into a clean list of non-empty strings."""
    if not isinstance(raw, list):
        return []
    items = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
    return items


def _ensure_full(data: dict) -> bool:
    """Ensure all lists + template exist with defaults. Returns True if saved."""
    changed = False
    if not isinstance(data.get("lists"), dict):
        data["lists"] = {}
        changed = True
    for name in LIST_NAMES:
        if name not in data["lists"] or not isinstance(data["lists"].get(name), list):
            data["lists"][name] = list(DEFAULT_LISTS[name])
            changed = True
    if not isinstance(data.get("template"), str) or not data["template"].strip():
        data["template"] = DEFAULT_TEMPLATE
        changed = True
    return changed


def _data() -> dict:
    """Load the persisted structure, applying defaults when missing/corrupt."""
    with _LOCK:
        data = _load()
        if _ensure_full(data):
            _save(data)
    return data


def is_valid_list_name(name: str) -> bool:
    return name in LIST_NAMES


def get_lists() -> dict[str, list[str]]:
    """Return the three editable lists as {name: [items]}."""
    data = _data()
    lists = data.get("lists", {})
    return {
        name: _normalize_items(lists.get(name, []))
        for name in LIST_NAMES
    }


def get_list(name: str) -> list[str]:
    if not is_valid_list_name(name):
        raise ValueError(f"Unknown list: {name!r}")
    return get_lists()[name]


def get_template() -> str:
    return _data().get("template", DEFAULT_TEMPLATE)


def set_template(template: str) -> bool:
    """Persist a new prompt template. Returns False when invalid (empty)."""
    if not isinstance(template, str) or not template.strip():
        return False
    with _LOCK:
        data = _load()
        _ensure_full(data)
        data["template"] = template.strip()
        _save(data)
    return True


def add_item(name: str, item: str) -> bool:
    """Append an item to a list. Returns False when invalid or a duplicate."""
    if not is_valid_list_name(name):
        return False
    if not isinstance(item, str) or not item.strip():
        return False
    clean = item.strip()
    with _LOCK:
        data = _load()
        _ensure_full(data)
        items = _normalize_items(data["lists"].get(name, []))
        if clean in items:
            return False
        items.append(clean)
        data["lists"][name] = items
        _save(data)
    return True


def update_item(name: str, index: int, item: str) -> bool:
    """Replace the item at `index`. Returns False on invalid args or duplicates."""
    if not is_valid_list_name(name):
        return False
    if not isinstance(item, str) or not item.strip():
        return False
    clean = item.strip()
    with _LOCK:
        data = _load()
        _ensure_full(data)
        items = _normalize_items(data["lists"].get(name, []))
        if not 0 <= index < len(items):
            return False
        if items[index] == clean:
            # No-op edit (same text): treat as success, nothing to persist.
            return True
        if clean in items:
            return False
        items[index] = clean
        data["lists"][name] = items
        _save(data)
    return True


def delete_item(name: str, index: int) -> bool:
    """Remove the item at `index`. Returns False when out of range."""
    if not is_valid_list_name(name):
        return False
    with _LOCK:
        data = _load()
        _ensure_full(data)
        items = _normalize_items(data["lists"].get(name, []))
        if not 0 <= index < len(items):
            return False
        del items[index]
        data["lists"][name] = items
        _save(data)
    return True


def build_prompt(pose: str, angle: str, action: str) -> str:
    """Fill the configured template with the three selected items."""
    template = get_template()
    try:
        return template.format(pose=pose, angle=angle, action=action)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        # Fall back to a plain join when the template references unknown fields.
        return f"{pose}, {angle}, {action}"


def random_combination(exclude: set[tuple[str, str, str]] | None = None) -> tuple[str, tuple[str, str, str]] | None:
    """Pick a random (pose, angle, action) combo, avoiding `exclude` when possible.

    Returns (prompt, combo) or None when any list is empty.
    """
    lists = get_lists()
    for name in LIST_NAMES:
        if not lists[name]:
            return None
    exclude = exclude or set()
    for _ in range(MAX_COMBO_ATTEMPTS):
        combo = (
            random.choice(lists["poses"]),
            random.choice(lists["angles"]),
            random.choice(lists["actions"]),
        )
        if combo not in exclude:
            break
    else:
        combo = (
            random.choice(lists["poses"]),
            random.choice(lists["angles"]),
            random.choice(lists["actions"]),
        )
    return build_prompt(*combo), combo


def combo_label(combo: tuple[str, str, str]) -> str:
    """Short human-readable label for a combo (used in status/result text)."""
    return ", ".join(combo)

#!/usr/bin/env python3
"""Persistent lists of editing variables (poses, angles) for /variables.

The bot's /variables command builds image-edit prompts by randomly combining
one item from each list. The lists and the prompt template are managed through
the Telegram admin panel (variables_flow.py) and persisted as JSON next to
sessions.json.

Prompt template placeholders:
    {pose}    — a random item from the "poses" list
    {angle}   — a random item from the "angles" list
    {action}  — a random item from the "actions" list

Decisión (2026-08-20): la lista "actions" se eliminó (pose y acción se pisaban
en foto). Decisión revertida (2026-09-01) por la dueña: el campo se recupera
con su nombre original y ahora se usa como outfit (y ocasionalmente acción
extra), administrado desde el panel /listas.
"""

from __future__ import annotations

import json
import random
import re
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
        "standing with weight shifted to one leg, free hand resting on hip",
        "combat-ready stance with knees slightly bent and torso angled forward",
        "leaning one shoulder against the wall",
        "classic model contrapposto with one leg slightly forward",
        "twisted torso looking back over the shoulder",
        "crouched low with one knee almost touching the ground",
        "legs crossed at the ankles while standing tall",
        "dynamic mid-stride pose as if just stopping",
        "sitting on the steps with knees together",
        "kneeling on one knee with the sword resting across the thigh",
    ],
    "actions": [
        "wearing a black combat dress with a high collar",
        "wearing a flowing white gown with a side slit",
        "dressed in a sleek futuristic bodysuit with glowing accents",
        "wearing a casual oversized hoodie and ripped jeans",
        "in a red evening dress with matching gloves",
    ],
    "angles": [
        "eye-level full body frontal",
        "low angle looking slightly upward",
        "high angle looking downward",
        "three-quarter view from the left side",
        "profile side view from the right",
        "slightly elevated three-quarter rear angle",
        "close full-body shot from below the waist upward",
        "over-the-shoulder perspective from behind",
        "eye-level medium shot",
        "wide shot from a slight distance",
    ],
}

# Maximum random draws when trying to avoid repeating a combination within a batch.
MAX_COMBO_ATTEMPTS = 30

# Positional index of each field within a (pose, angle, action) combo tuple.
_FIELD_INDEX = {"pose": 0, "angle": 1, "action": 2}

# Matches named template placeholders like {pose}, {angle}, {action}.
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Matches str.format expressions (attribute/index/conversion/format-spec) such as
# {pose.foo}, {pose!r}, {pose:>10}. These are invalid as plain placeholders and
# make the template fall back to a join (JSON object braces never match, since a
# JSON key follows `{` with whitespace and a quote, not an identifier).
_FORMAT_EXPR_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*(?:[.!:])")


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
    if not isinstance(data.get("blacklist"), list):
        data["blacklist"] = []
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


def _needs_fallback(template: str, values: dict[str, str]) -> bool:
    """True when the template cannot be rendered with the supplied values."""
    if _FORMAT_EXPR_RE.search(template):
        return True
    return any(f not in values for f in _PLACEHOLDER_RE.findall(template))


def build_prompt(pose: str, angle: str, action: str) -> str:
    """Fill the configured template with the three selected items.

    Placeholders are replaced by regex instead of ``str.format``, so templates
    that contain literal braces — e.g. a JSON-structured prompt — render intact.
    """
    template = get_template()
    values = {"pose": pose, "angle": angle, "action": action}
    if _needs_fallback(template, values):
        # Fall back to a plain join when the template references unknown fields.
        return f"{pose}, {angle}, {action}"
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def build_prompt_inline(fields: list[str]) -> str:
    """Render the configured template with inline /var fields.

    The fields fill the template placeholders positionally, in order of
    appearance (first field → first placeholder). A single field without commas
    therefore lands on the first placeholder. Placeholders without a matching
    field render empty and the leftover separator artifacts are cleaned up
    (", ," collapses, leading/trailing ", " is trimmed), so "/var de pie" with
    the default "{pose}, {angle}, {action}" template renders "de pie". Fields beyond the
    placeholder count are ignored. When the template has no placeholders the
    fields are joined with ", ".
    """
    clean = [f.strip() for f in fields if isinstance(f, str) and f.strip()]
    template = get_template()
    names = _PLACEHOLDER_RE.findall(template)
    if not names or _FORMAT_EXPR_RE.search(template):
        return ", ".join(clean)
    values = {name: "" for name in names}
    for i, name in enumerate(names):
        if i < len(clean):
            values[name] = clean[i]
    rendered = _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)
    return _clean_placeholder_gaps(rendered)


def _clean_placeholder_gaps(text: str) -> str:
    """Collapse separator artifacts left by placeholders that rendered empty."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r",\s*,", ",", text)
    text = re.sub(r",\s*$", "", text)
    text = re.sub(r"^\s*,\s*", "", text)
    return text.strip()


def template_fields(template: str | None = None) -> list[str]:
    """Placeholder field names in the template, in order of appearance."""
    tpl = template if template is not None else get_template()
    return _PLACEHOLDER_RE.findall(tpl)


def combo_key(pose: str, angle: str, action: str) -> tuple:
    """Ordered tuple of the values that actually render into the prompt.

    Only the fields the template references contribute, so the key identifies the
    combination by its prompt content (e.g. ``(pose, angle)`` when the template
    drops ``{action}``), independent of the other lists.
    """
    values = {"pose": pose, "angle": angle, "action": action}
    return tuple(values.get(f, "") for f in template_fields())


def _render_positional(template: str, values: list[str]) -> str:
    """Render the template with `values` filling its placeholders left to right."""
    if _FORMAT_EXPR_RE.search(template):
        return ", ".join(values)
    it = iter(values)
    return _PLACEHOLDER_RE.sub(lambda _m: next(it, ""), template)


def build_prompt_shuffled(pose: str, angle: str, action: str) -> str:
    """Render the template with the contributing values in a different order.

    Guarantees a derangement (order differs from the canonical template order) when
    two or more fields contribute; with three fields the shuffle must avoid the
    canonical order (reversed as a last resort).
    """
    values = {"pose": pose, "angle": angle, "action": action}
    ordered = [values.get(f, "") for f in template_fields()]
    if len(ordered) >= 2:
        canonical = list(ordered)
        random.shuffle(ordered)
        if ordered == canonical:
            ordered.reverse()
    return _render_positional(get_template(), ordered)


def random_combination(exclude: set[tuple[str, str, str]] | None = None) -> tuple[str, tuple[str, str, str]] | None:
    """Pick a random (pose, angle, action) combo, avoiding `exclude` when possible.

    Returns (prompt, combo) or None when any list is empty.
    """
    lists = get_lists()
    for name in LIST_NAMES:
        if not lists[name]:
            return None
    exclude = exclude or set()
    blacklist = get_blacklist()
    for _ in range(MAX_COMBO_ATTEMPTS):
        combo = (
            random.choice(lists["poses"]),
            random.choice(lists["angles"]),
            random.choice(lists["actions"]),
        )
        if combo not in exclude and combo_key(*combo) not in blacklist:
            break
    else:
        combo = (
            random.choice(lists["poses"]),
            random.choice(lists["angles"]),
            random.choice(lists["actions"]),
        )
    return build_prompt(*combo), combo


def combo_label(combo: tuple[str, str]) -> str:
    """Short human-readable label for a combo (used in status/result text)."""
    return ", ".join(combo)


def get_blacklist() -> set[tuple]:
    """Persistent set of combo keys marked as exhausted (never retry)."""
    data = _data()
    raw = data.get("blacklist", [])
    out: set[tuple] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, list):
                out.add(tuple(item))
    return out


def blacklist_add(key: tuple) -> bool:
    """Persist a combo key to the blacklist. Returns False if invalid or already there."""
    if not isinstance(key, tuple):
        return False
    entry = list(key)
    with _LOCK:
        data = _load()
        _ensure_full(data)
        blacklist = data["blacklist"]
        if entry in blacklist:
            return False
        blacklist.append(entry)
        _save(data)
    return True


def blacklist_clear() -> None:
    """Remove all blacklisted combo keys."""
    with _LOCK:
        data = _load()
        _ensure_full(data)
        data["blacklist"] = []
        _save(data)


# ---------------------------------------------------------------------------
# Paquetes de poses: archivos JSON con nombre gestionados desde el panel /listas
#
# Cada paquete es un archivo en variables_packages/<slug>.json con la forma
# normalizada {"lists": {campo: [items]}, "template": str}. "Activar" copia el
# contenido del paquete al archivo activo variables_lists.json (y deja la marca
# "_package" para saber cuál está activo). El runtime solo lee el archivo activo,
# así que el resto del sistema no cambia.
# ---------------------------------------------------------------------------
PACKAGES_DIR = Path(__file__).parent / "variables_packages"
_ACTIVE_PACKAGE_KEY = "_package"


def _slugify(name: str) -> str:
    """Normalize a package name into a filesystem/callback-safe slug."""
    slug = re.sub(r"\W+", "_", name.strip().lower()).strip("_")
    return slug


def list_packages() -> dict[str, Path]:
    """Available packages as {slug: path}, sorted alphabetically."""
    if not PACKAGES_DIR.exists():
        return {}
    out = {}
    for path in sorted(PACKAGES_DIR.glob("*.json")):
        out[path.stem] = path
    return out


def active_package_name() -> str | None:
    """Slug of the active package, or None when the file is hand-edited."""
    name = _data().get(_ACTIVE_PACKAGE_KEY)
    return name if isinstance(name, str) and name else None


def _normalize_package_payload(raw) -> tuple[dict | None, str | None]:
    """Validate and normalize a raw package payload into
    {"lists": {...}, "template": str}. Accepts both the ``lists`` and the
    ``fields`` top-level key. Returns (payload, None) or (None, error)."""
    if not isinstance(raw, dict):
        return None, "El paquete debe ser un objeto JSON."
    raw_lists = raw.get("lists") if isinstance(raw.get("lists"), dict) else None
    if raw_lists is None and isinstance(raw.get("fields"), dict):
        raw_lists = raw["fields"]
    if not raw_lists:
        return None, "Falta 'lists' (o 'fields') con al menos un campo."
    lists: dict[str, list[str]] = {}
    for field, items in raw_lists.items():
        normalized = _normalize_items(items)
        if not normalized:
            continue
        lists[str(field)] = normalized
    if not lists:
        return None, "Ningún campo tiene elementos."
    template = raw.get("template")
    if not isinstance(template, str) or not template.strip():
        return None, "Falta 'template' (texto del prompt)."
    return {"lists": lists, "template": template.strip()}, None


def save_package(name: str, payload: dict) -> tuple[bool, str | None]:
    """Create (or overwrite) a package file. Returns (ok, None) or (False, error)."""
    slug = _slugify(name)
    if not slug:
        return False, "El nombre del paquete no es válido."
    normalized, err = _normalize_package_payload(payload)
    if err:
        return False, err
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with open(PACKAGES_DIR / f"{slug}.json", "w") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
    return True, None


def load_package(name: str) -> dict | None:
    """Load a package's normalized content, or None when missing/invalid."""
    slug = _slugify(name)
    path = PACKAGES_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def package_exists(name: str) -> bool:
    return (PACKAGES_DIR / f"{_slugify(name)}.json").exists()


def activate_package(name: str) -> bool:
    """Copy a package's content into the active file, clearing the blacklist."""
    payload = load_package(name)
    if payload is None:
        return False
    slug = _slugify(name)
    with _LOCK:
        data = {
            "lists": payload["lists"],
            "template": payload["template"],
            "blacklist": [],
            _ACTIVE_PACKAGE_KEY: slug,
        }
        _save(data)
    return True


def delete_package(name: str) -> bool:
    """Delete a package file. Returns False when missing or when it is active."""
    slug = _slugify(name)
    if not package_exists(slug):
        return False
    if active_package_name() == slug:
        return False
    (PACKAGES_DIR / f"{slug}.json").unlink()
    return True
